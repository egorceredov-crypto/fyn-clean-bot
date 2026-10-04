import aiosqlite
from typing import Optional
import random
import string
import uuid

DB_PATH = "orders.db"


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
              metro TEXT,
              address TEXT,
              phone TEXT,
              comment TEXT,
              date TEXT,
              time TEXT,
              name TEXT
             )
         """)
        async with db.execute("PRAGMA table_info(abandoned_orders)") as cur:
            cols = [row[1] async for row in cur]
            if "patronymic" in cols and "name" not in cols:
                await db.execute("ALTER TABLE abandoned_orders RENAME COLUMN patronymic TO name")
            for col in ["flat_type", "metro_line", "metro", "address", "phone", "comment", "date", "time"]:
                if col not in cols:
                    await db.execute(f"ALTER TABLE abandoned_orders ADD COLUMN {col} TEXT")

        # Таблица исполнителей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS executors (
              telegram_id INTEGER PRIMARY KEY,
              full_name TEXT,
              phone TEXT,
              is_active INTEGER DEFAULT 1,
              created_at TEXT
             )
         """)

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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO orders
            (id, service, data_json, status, created_at, date, time, executor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                service,
                data_json,
                status,
                created_at,
                date,
                time,
                executor_id
            )
        )
        await db.commit()


async def update_status(order_id: str, status: str):
    async with aiosqlite.connect(DB_PATH) as db:

        await db.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE id = ?
            """,
            (status, order_id)
        )

        await db.commit()


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
                name = excluded.name
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
    name=None
):
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
            name
        )
        VALUES (?, ?, ?, datetime('now'), 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)

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
            name=excluded.name
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
            name
        )
        )
        await db.commit()



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
    phone: str
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO executors (telegram_id, full_name, phone, created_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(telegram_id) DO UPDATE SET
                full_name=excluded.full_name,
                phone=excluded.phone
        """, (telegram_id, full_name, phone))
        await db.commit()


async def get_all_executors():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, full_name, phone, is_active
            FROM executors
            ORDER BY full_name
        """) as cur:
            rows = await cur.fetchall()
    return rows


async def get_executor(telegram_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT telegram_id, full_name, phone, is_active
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
        await db.execute("""
            INSERT INTO order_photos (id, order_id, telegram_id, photo_file_id, photo_type, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """, (photo_id, order_id, telegram_id, photo_file_id, photo_type))
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
    rating_id = uuid.uuid4().hex[:12]
    async with aiosqlite.connect(DB_PATH) as db:
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

async def assign_executor(order_id: str, executor_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE orders
            SET executor_id = ?, status = ?
            WHERE id = ?
        """, (executor_id, "assigned", order_id))
        await db.commit()


async def get_orders_by_status(status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at, executor_id, date, time
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
            ORDER BY created_at DESC
            LIMIT 50
        """, (executor_id,)) as cur:
            rows = await cur.fetchall()
    return rows


async def get_user_orders(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, service, data_json, status, created_at, executor_id, date, time
            FROM orders
            WHERE json_extract(data_json, '$.telegram_id') = ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (user_id,)) as cur:
            rows = await cur.fetchall()
    return rows


async def cancel_order(order_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE orders
            SET status = ?
            WHERE id = ?
        """, ("cancelled", order_id))
        await db.commit()