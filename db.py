import aiosqlite
import json
import os
from typing import Optional
import random
import string
import uuid

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "orders.db")

# The extra "contacted" value is kept for rows created by the legacy bot.
# New code uses the six Stage 1 lifecycle states and never invents another
# status.
ORDER_STATUS_TRANSITIONS = {
    "new": {"accepted", "assigned", "cancelled"},
    "accepted": {"assigned", "in_work", "cancelled"},
    "assigned": {"in_work", "cancelled"},
    "in_work": {"completed", "cancelled"},
    "contacted": {"assigned", "in_work", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def can_transition_status(current: str | None, new: str) -> bool:
    return current in ORDER_STATUS_TRANSITIONS and new in ORDER_STATUS_TRANSITIONS[current]


def same_id(left, right) -> bool:
    return left is not None and right is not None and str(left) == str(right)


# ================= DATABASE INIT =================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:

        # Таблица заказов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                service TEXT,
                data_json TEXT,
                status TEXT,
                created_at TEXT,
                executor_id INTEGER,
                date TEXT,
                time TEXT
            )
        """)

        # Таблица пользователей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
               user_id INTEGER PRIMARY KEY,
               username TEXT,
               full_name TEXT,
               name TEXT,
               first_seen TEXT,
               orders_cleaning INTEGER DEFAULT 0,
               referral_code TEXT UNIQUE,
               invited_by TEXT,
               bonuses INTEGER DEFAULT 0,
               last_reminder_at TEXT
             )
         """)
        async with db.execute("PRAGMA table_info(users)") as cur:
            cols = [row[1] async for row in cur]
            if "patronymic" in cols and "name" not in cols:
                await db.execute("ALTER TABLE users RENAME COLUMN patronymic TO name")
            elif "name" not in cols:
                await db.execute("ALTER TABLE users ADD COLUMN name TEXT")

        # Таблица брошенных заявок
        await db.execute("""
            CREATE TABLE IF NOT EXISTS abandoned_orders (
              telegram_id INTEGER PRIMARY KEY,
              cleaning_type TEXT,
              step TEXT,
              created_at TEXT,
              reminded INTEGER DEFAULT 0,
              flat_type TEXT,
              metro_line TEXT,
              metro TEXT,
              address TEXT,
              phone TEXT,
              comment TEXT,
              date TEXT,
              time TEXT,
              name TEXT,
              photos TEXT
             )
         """)
        async with db.execute("PRAGMA table_info(abandoned_orders)") as cur:
            cols = [row[1] async for row in cur]
            if "patronymic" in cols and "name" not in cols:
                await db.execute("ALTER TABLE abandoned_orders RENAME COLUMN patronymic TO name")
            for col in ["flat_type", "metro_line", "metro", "address", "phone", "comment", "date", "time", "photos"]:
                if col not in cols:
                    await db.execute(f"ALTER TABLE abandoned_orders ADD COLUMN {col} TEXT")

        # Таблица исполнителей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS executors (
              telegram_id INTEGER PRIMARY KEY,
              full_name TEXT,
              phone TEXT,
              bio TEXT,
              is_active INTEGER DEFAULT 0,
              created_at TEXT
             )
         """)
        async with db.execute("PRAGMA table_info(executors)") as cur:
            executor_columns = [row[1] async for row in cur]
        if "is_active" not in executor_columns:
            await db.execute(
                "ALTER TABLE executors ADD COLUMN is_active INTEGER DEFAULT 0"
            )
        if "created_at" not in executor_columns:
            await db.execute(
                "ALTER TABLE executors ADD COLUMN created_at TEXT"
            )
        if "bio" not in executor_columns:
            await db.execute(
                "ALTER TABLE executors ADD COLUMN bio TEXT"
            )

        # Таблица фотографий заказов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS order_photos (
              id TEXT PRIMARY KEY,
              order_id TEXT,
              telegram_id INTEGER,
              photo_file_id TEXT,
              photo_type TEXT,
              created_at TEXT
             )
         """)

        # Таблица оценок исполнителей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
              id TEXT PRIMARY KEY,
              order_id TEXT,
              client_id INTEGER,
              executor_id INTEGER,
              rating INTEGER,
              comment TEXT,
              created_at TEXT
             )
         """)

        # Миграции для существующих таблиц
        async with db.execute("PRAGMA table_info(orders)") as cur:
            columns = await cur.fetchall()
        column_names = [column[1] for column in columns]
        if "executor_id" not in column_names:
            await db.execute("ALTER TABLE orders ADD COLUMN executor_id INTEGER")
        if "date" not in column_names:
            await db.execute("ALTER TABLE orders ADD COLUMN date TEXT")
        if "time" not in column_names:
            await db.execute("ALTER TABLE orders ADD COLUMN time TEXT")

        async with db.execute("PRAGMA table_info(users)") as cur:
            columns = await cur.fetchall()
        column_names = [column[1] for column in columns]
        if "last_reminder_at" not in column_names:
            await db.execute("""
                ALTER TABLE users
                ADD COLUMN last_reminder_at TEXT
        """)

        async with db.execute("PRAGMA table_info(orders)") as cur:
            cols = [row[1] async for row in cur]
            if "patronymic" in cols and "name" not in cols:
                await db.execute("ALTER TABLE orders RENAME COLUMN patronymic TO name")
            for col in ["flat_type", "metro", "address", "phone", "comment", "date", "time"]:
                if col not in cols:
                    await db.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")
            if "completed_at" not in cols:
                await db.execute("ALTER TABLE orders ADD COLUMN completed_at TEXT")
            if "updated_at" not in cols:
                await db.execute("ALTER TABLE orders ADD COLUMN updated_at TEXT")

        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_abandoned_telegram_reminded ON abandoned_orders(telegram_id, reminded)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_executors_telegram_id ON executors(telegram_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_order_photos_order_id ON order_photos(order_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ratings_order_id ON ratings(order_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_ratings_executor_id ON ratings(executor_id)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_order_client ON ratings(order_id, client_id)")

        await db.commit()


# ================= ORDERS =================

async def create_order(
    order_id: str,
    service: str,
    data_json: str,
    created_at: str,
    status: str = "new",
    date: str = None,
    time: str = None,
    executor_id: int = None
):
    if status not in ORDER_STATUS_TRANSITIONS:
        raise ValueError(f"unknown order status: {status}")
    try:
        parsed_data = json.loads(data_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("data_json must contain valid JSON") from exc
    if not isinstance(parsed_data, dict):
        raise ValueError("data_json must contain a JSON object")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO orders
            (id, service, data_json, status, created_at, updated_at, date, time, executor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                service,
                data_json,
                status,
                created_at,
                created_at,
                date,
                time,
                executor_id
            )
        )
        await db.commit()


async def update_status(order_id: str, status: str):
    """Compatibility wrapper that still enforces the lifecycle."""
    return await transition_status(order_id, status)


async def get_status(order_id: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            """
            SELECT status
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ) as cur:

            row = await cur.fetchone()

    return row[0] if row else None


# ================= USERS =================

async def upsert_user(
    user_id: int,
    username: Optional[str],
    full_name: Optional[str],
    invited_by=None,
    name=None
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                full_name,
                name,
                first_seen,
                orders_cleaning,
                referral_code,
                invited_by,
                bonuses
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                datetime('now'),
                0,
                ?,
                ?,
                0
            )

            ON CONFLICT(user_id)
            DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                name = CASE WHEN excluded.name IS NOT NULL AND excluded.name != '' THEN excluded.name ELSE users.name END
            """,
            (
                user_id,
                username,
                full_name,
                name,
                str(user_id),
                invited_by
            )
        )

        await db.commit()


async def get_user_profile(
    user_id: int
) -> Optional[dict]:

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            """
            SELECT
                user_id,
                username,
                full_name,
                name,
                first_seen,
                orders_cleaning
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cur:

            row = await cur.fetchone()

    if not row:
        return None

    return {
        "user_id": row[0],
        "username": row[1],
        "full_name": row[2],
        "name": row[3],
        "first_seen": row[4],
        "orders_cleaning": row[5],
    }


async def inc_cleaning_orders(
    user_id: int,
    by: int = 1
):
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE users
            SET orders_cleaning = orders_cleaning + ?
            WHERE user_id = ?
            """,
            (
                by,
                user_id
            )
        )

        await db.commit()


# ================= REMINDERS =================


async def mark_reminder_sent(
    user_id: int
):
    """
    Записывает время последнего отправленного
    напоминания.
    """

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE users
            SET last_reminder_at = datetime('now')
            WHERE user_id = ?
            """,
            (user_id,)
        )

        await db.commit()

async def get_users_for_reminder(days: int = 1):
    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("""
            SELECT
                json_extract(o.data_json, '$.telegram_id') AS telegram_id,
                u.full_name,
                MAX(o.created_at) AS last_order
            FROM orders o
            LEFT JOIN users u
                ON u.user_id = json_extract(o.data_json, '$.telegram_id')
            WHERE o.service = 'cleaning'
              AND o.status = 'completed'
              AND json_extract(o.data_json, '$.telegram_id') IS NOT NULL
            GROUP BY telegram_id
            HAVING datetime(last_order) <= datetime('now', ?)
               AND (
                   u.last_reminder_at IS NULL
                   OR datetime(u.last_reminder_at) < datetime(last_order)
               )
        """, (f"-{days} days",)) as cur:

            rows = await cur.fetchall()

    return rows


# ================= TEST REMINDER =================

async def get_test_reminder_users(days: int = 30):
    """
    Возвращает клиентов с заказами, которым
    не отправлялось напоминание в течение `days` дней.
    """

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            """
            SELECT DISTINCT
                json_extract(o.data_json, '$.telegram_id') AS telegram_id,
                u.full_name

            FROM orders o

            LEFT JOIN users u
                ON u.user_id = json_extract(o.data_json, '$.telegram_id')

            WHERE
                o.service = 'cleaning'
                AND o.status = 'completed'

                AND json_extract(
                    o.data_json,
                    '$.telegram_id'
                ) IS NOT NULL

                AND (
                    u.last_reminder_at IS NULL
                    OR datetime(u.last_reminder_at)
                        <= datetime('now', '-' || ? || ' days')
                )
            """,
            (days,)
        ) as cur:

            rows = await cur.fetchall()

    return rows

async def get_or_create_referral_code(user_id):

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute(
            """
            SELECT referral_code
            FROM users
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cur:

            row = await cur.fetchone()


        if row and row[0]:
            return row[0]


        code = str(user_id)


        await db.execute(
            """
            UPDATE users
            SET referral_code = ?
            WHERE user_id = ?
            """,
            (
                code,
                user_id
            )
        )

        await db.commit()


        return code

async def save_abandoned_order(
    telegram_id,
    cleaning_type,
    step,
    flat_type=None,
    metro_line=None,
    metro=None,
    address=None,
    phone=None,
    comment=None,
    date=None,
    time=None,
    name=None,
    photos=None
):
    if photos is not None and not isinstance(photos, str):
        photos = json.dumps(photos, ensure_ascii=False)
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute("""
        INSERT INTO abandoned_orders
        (
            telegram_id,
            cleaning_type,
            step,
            created_at,
            reminded,
            flat_type,
            metro_line,
            metro,
            address,
            phone,
            comment,
            date,
            time,
            name,
            photos
        )
        VALUES (?, ?, ?, datetime('now'), 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(telegram_id)
        DO UPDATE SET
            cleaning_type=excluded.cleaning_type,
            step=excluded.step,
            created_at=datetime('now'),
            reminded=0,
            flat_type=excluded.flat_type,
            metro_line=excluded.metro_line,
            metro=excluded.metro,
            address=excluded.address,
            phone=excluded.phone,
            comment=excluded.comment,
            date=excluded.date,
            time=excluded.time,
            name=excluded.name,
            photos=excluded.photos
        """, (
            telegram_id,
            cleaning_type,
            step,
            flat_type,
            metro_line,
            metro,
            address,
            phone,
            comment,
            date,
            time,
            name,
            photos
        )
        )
        await db.commit()


async def transition_status(order_id: str, status: str) -> bool:
    """Apply a validated lifecycle transition and report whether it changed a row."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT status, executor_id, data_json FROM orders WHERE id = ?",
            (order_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row or not can_transition_status(row[0], status):
            await db.rollback()
            return False
        if status in {"assigned", "in_work", "completed"}:
            try:
                data = json.loads(row[2] or "{}")
            except (TypeError, json.JSONDecodeError):
                data = {}
            executor_id = row[1] or data.get("executor_id")
            if executor_id is None:
                await db.rollback()
                return False
            async with db.execute(
                "SELECT is_active FROM executors WHERE telegram_id = ?",
                (executor_id,),
            ) as cur:
                executor_row = await cur.fetchone()
            if not executor_row or not executor_row[0]:
                await db.rollback()
                return False
        if status == "completed":
            cursor = await db.execute(
                "UPDATE orders SET status = ?, completed_at = datetime('now'), updated_at = datetime('now') WHERE id = ? AND status = ?",
                (status, order_id, row[0]),
            )
        else:
            cursor = await db.execute(
                "UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ? AND status = ?",
                (status, order_id, row[0]),
            )
        await db.commit()
        return cursor.rowcount == 1



async def delete_abandoned_order(
    telegram_id
):
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            DELETE FROM abandoned_orders
            WHERE telegram_id=?
            """,
            (telegram_id,)
        )

        await db.commit()



async def get_abandoned_orders():

    async with aiosqlite.connect(DB_PATH) as db:

        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
        SELECT *
        FROM abandoned_orders
        WHERE reminded = 0
        AND datetime(created_at)
        <= datetime('now','-30 minutes')
        """)

        rows = await cursor.fetchall()

    return rows



async def mark_abandoned_reminded(
    telegram_id
):

    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE abandoned_orders
            SET reminded=1
            WHERE telegram_id=?
            """,
            (telegram_id,)
        )

        await db.commit()


# ================= EXECUTORS =================

async def add_executor(
    telegram_id: int,
    full_name: str,
    phone: str,
    bio: str = None,
    is_active: bool = False,
    update_active: bool = False,
):
    async with aiosqlite.connect(DB_PATH) as db:
        if update_active:
            await db.execute("""
                INSERT INTO executors
                    (telegram_id, full_name, phone, bio, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(telegram_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    phone=excluded.phone,
                    bio=excluded.bio,
                    is_active=excluded.is_active
            """, (telegram_id, full_name, phone, bio, 1 if is_active else 0))
        else:
            await db.execute("""
                INSERT INTO executors
                    (telegram_id, full_name, phone, bio, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(telegram_id) DO UPDATE SET
                    full_name=excluded.full_name,
                    phone=excluded.phone,
                    bio=excluded.bio
            """, (telegram_id, full_name, phone, bio, 1 if is_active else 0))
        await db.commit()
    return True


async def get_all_executors():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, full_name, phone, bio, is_active
            FROM executors
            ORDER BY full_name
        """) as cur:
            rows = await cur.fetchall()
    return rows


async def get_active_executors():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, full_name, phone, bio, is_active
            FROM executors
            WHERE is_active = 1
            ORDER BY full_name
        """) as cur:
            rows = await cur.fetchall()
    return rows


async def get_executor(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, full_name, phone, bio, is_active
            FROM executors
            WHERE telegram_id = ?
        """, (telegram_id,)) as cur:
            row = await cur.fetchone()
    return row


async def toggle_executor_active(telegram_id: int, is_active: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE executors
            SET is_active = ?
            WHERE telegram_id = ?
        """, (1 if is_active else 0, telegram_id))
        await db.commit()


# ================= ORDER PHOTOS =================

async def save_order_photo(
    order_id: str,
    telegram_id: int,
    photo_file_id: str,
    photo_type: str = "client"
):
    photo_id = uuid.uuid4().hex[:12]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT data_json, executor_id FROM orders WHERE id = ?",
            (order_id,),
        ) as cur:
            order = await cur.fetchone()
        if order is None:
            await db.rollback()
            return None
        try:
            order_data = json.loads(order[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            order_data = {}
        owner_id = order_data.get("telegram_id")
        assigned_executor_id = order[1] or order_data.get("executor_id")
        if photo_type not in {"client", "result"}:
            await db.rollback()
            raise ValueError(f"unknown photo type: {photo_type}")
        if photo_type == "client" and not same_id(owner_id, telegram_id):
            await db.rollback()
            return None
        if photo_type == "result" and not same_id(assigned_executor_id, telegram_id):
            await db.rollback()
            return None
        await db.execute("""
            INSERT INTO order_photos (id, order_id, telegram_id, photo_file_id, photo_type, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (photo_id, order_id, telegram_id, photo_file_id, photo_type))
        await db.execute(
            "UPDATE orders SET updated_at = datetime('now') WHERE id = ?",
            (order_id,),
        )
        await db.commit()
    return photo_id


async def get_order_photos(order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, photo_file_id, photo_type, telegram_id, created_at
            FROM order_photos
            WHERE order_id = ?
            ORDER BY created_at ASC
        """, (order_id,)) as cur:
            rows = await cur.fetchall()
    return rows


# ================= RATINGS =================

async def save_rating(
    order_id: str,
    client_id: int,
    executor_id: int,
    rating: int,
    comment: str = ""
):
    if not 1 <= int(rating) <= 5:
        raise ValueError("rating must be between 1 and 5")
    rating_id = uuid.uuid4().hex[:12]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT data_json, status, executor_id FROM orders WHERE id = ?",
            (order_id,),
        ) as cur:
            order = await cur.fetchone()
        if not order:
            await db.rollback()
            return None
        try:
            order_data = json.loads(order[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            order_data = {}
        stored_executor_id = order[2] or order_data.get("executor_id")
        if (
            order[1] != "completed"
            or str(order_data.get("telegram_id")) != str(client_id)
            or str(stored_executor_id) != str(executor_id)
        ):
            await db.rollback()
            return None
        async with db.execute(
            "SELECT 1 FROM ratings WHERE order_id = ? AND client_id = ?",
            (order_id, client_id),
        ) as cur:
            if await cur.fetchone() is not None:
                await db.rollback()
                return None
        await db.execute("""
            INSERT INTO ratings (id, order_id, client_id, executor_id, rating, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """, (rating_id, order_id, client_id, executor_id, rating, comment))
        await db.commit()
    return rating_id


async def get_executor_ratings(executor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT r.rating, r.comment, r.created_at, o.data_json, u.full_name
            FROM ratings r
            JOIN orders o ON r.order_id = o.id
            LEFT JOIN users u ON r.client_id = u.user_id
            WHERE r.executor_id = ?
            ORDER BY r.created_at DESC
        """, (executor_id,)) as cur:
            rows = await cur.fetchall()
    return rows


async def get_executor_avg_rating(executor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT AVG(rating), COUNT(*)
            FROM ratings
            WHERE executor_id = ?
        """, (executor_id,)) as cur:
            row = await cur.fetchone()
    if row and row[0]:
        return {"avg": round(row[0], 1), "count": row[1]}
    return {"avg": 0.0, "count": 0}


async def get_order_rating(order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, client_id, executor_id, rating, comment, created_at
            FROM ratings
            WHERE order_id = ?
        """, (order_id,)) as cur:
            row = await cur.fetchone()
    return row


# ================= ORDERS HELPERS =================

async def get_order(order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at, updated_at,
                   executor_id, date, time, completed_at
            FROM orders
            WHERE id = ?
        """, (order_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[2] or "{}")
    except (TypeError, json.JSONDecodeError):
        data = {}
    if row[9]:
        data["completed_at"] = row[9]
    return {
        "id": row[0],
        "service": row[1],
        "data": data,
        "status": row[3],
        "created_at": row[4],
        "updated_at": row[5],
        "executor_id": row[6] or data.get("executor_id"),
        "date": row[7],
        "time": row[8],
        "completed_at": row[9],
    }


async def assign_executor(order_id: str, executor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT data_json, status, executor_id FROM orders WHERE id = ?",
            (order_id,),
        ) as cur:
            order = await cur.fetchone()
        async with db.execute(
            "SELECT is_active FROM executors WHERE telegram_id = ?",
            (executor_id,),
        ) as cur:
            executor = await cur.fetchone()
        if not order or not executor or not executor[0]:
            await db.rollback()
            return False
        current_status = order[1]
        if current_status in {"completed", "cancelled"}:
            await db.rollback()
            return False
        if current_status == "assigned" and order[2] == executor_id:
            await db.rollback()
            return False
        if current_status != "assigned" and not can_transition_status(current_status, "assigned"):
            await db.rollback()
            return False
        try:
            data = json.loads(order[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        data["executor_id"] = executor_id
        cursor = await db.execute("""
            UPDATE orders
            SET executor_id = ?, status = ?, data_json = ?, updated_at = datetime('now')
            WHERE id = ? AND status = ?
        """, (
            executor_id,
            "assigned",
            json.dumps(data, ensure_ascii=False),
            order_id,
            current_status,
        ))
        await db.commit()
        return cursor.rowcount == 1


async def get_orders_by_status(status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at,
                   COALESCE(executor_id, json_extract(data_json, '$.executor_id')),
                   date, time
            FROM orders
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (status,)) as cur:
            rows = await cur.fetchall()
    return rows


async def get_orders_by_executor(executor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at, date, time
            FROM orders
            WHERE executor_id = ?
               OR json_extract(data_json, '$.executor_id') = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (executor_id, executor_id)) as cur:
            rows = await cur.fetchall()
    return rows


async def get_user_orders(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at,
                   COALESCE(executor_id, json_extract(data_json, '$.executor_id')),
                   date, time
            FROM orders
            WHERE json_extract(data_json, '$.telegram_id') = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
    return rows


async def unassign_executor(order_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT status, executor_id, data_json FROM orders WHERE id = ?",
            (order_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            return False
        current_status, executor_id, data_json = row
        if current_status in {"completed", "cancelled"}:
            await db.rollback()
            return False
        if not executor_id:
            await db.rollback()
            return False
        try:
            data = json.loads(data_json or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        data.pop("executor_id", None)
        cursor = await db.execute(
            "UPDATE orders SET status = 'accepted', executor_id = NULL, data_json = ?, updated_at = datetime('now') WHERE id = ? AND status = ?",
            (json.dumps(data, ensure_ascii=False), order_id, current_status),
        )
        await db.commit()
        return cursor.rowcount == 1


async def cancel_order(order_id: str):
    return await transition_status(order_id, "cancelled")
