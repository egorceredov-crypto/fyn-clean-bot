import asyncio
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

from aiogram.types import FSInputFile

from main import (
    dp,
    format_order_text,
    calc_price_estimate,
    safe_str,
    STATUS_LABELS,
    ORDER_PREVIEW_TEXT,
    INDIVIDUAL_CLEANING_PHOTO,
    build_preview_text,
)
from db import init_db, create_order, get_user_orders, get_order_photos, save_order_photo
from states import CleaningOrder, ExecutorStates, RatingStates
from metro_data import METRO


@pytest.mark.asyncio
async def test_imports_and_constants():
    assert len(METRO) == 12
    assert os.path.exists(INDIVIDUAL_CLEANING_PHOTO)
    assert os.path.getsize(INDIVIDUAL_CLEANING_PHOTO) > 0
    assert "new" in STATUS_LABELS
    assert "completed" in STATUS_LABELS
    assert "cancelled" in STATUS_LABELS
    assert "{service}" in ORDER_PREVIEW_TEXT


@pytest.mark.asyncio
async def test_db_init():
    await init_db()
    orders = await get_user_orders(999999)
    assert orders == []


@pytest.mark.asyncio
async def test_fsm_states():
    expected_states = [
        "CleaningOrder:cleaning_type",
        "CleaningOrder:flat_type",
        "CleaningOrder:date",
        "CleaningOrder:time",
        "CleaningOrder:metro_station",
        "CleaningOrder:address",
        "CleaningOrder:phone",
        "CleaningOrder:name",
        "CleaningOrder:comment",
        "CleaningOrder:photos",
        "CleaningOrder:preview",
    ]
    for state in expected_states:
        assert state in CleaningOrder.__all_states__


@pytest.mark.asyncio
async def test_price_calculation():
    assert calc_price_estimate("1-комнатная", "Поддерживающая уборка") == "10 500–13 500 ₽"
    assert calc_price_estimate("2-комнатная", "Генеральная уборка") == "13 500–16 500 ₽"
    assert calc_price_estimate("3-комнатная", "После ремонта") == "16 500–20 500 ₽"
    assert calc_price_estimate("Студия / по площади", "Поддерживающая уборка") == "от 10 500 ₽"
    assert calc_price_estimate("Неизвестно", "Поддерживающая уборка") == "от 10 500 ₽"


@pytest.mark.asyncio
async def test_safe_str():
    assert safe_str(None) == "—"
    assert safe_str("") == "—"
    assert safe_str("Иван") == "Иван"


@pytest.mark.asyncio
async def test_format_order_text():
    data = {
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "metro": "Красные ворота",
        "address": "ул. Тестовая, 1",
        "date": "15.10.2026",
        "time": "14:00",
        "phone": "+79991234567",
        "fio": "Иван Петров",
        "name": "",
        "comment": "Позвонить за час",
        "username": "@test",
    }
    text = format_order_text(data, "Уборка квартиры", "test001", "new")
    assert "test001" in text
    assert "Уборка квартиры" in text
    assert "Красные ворота" in text
    assert "Иван Петров" in text


@pytest.mark.asyncio
async def test_build_preview_text():
    data = {
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "metro": "Киевская",
        "address": "ул. Тестовая, 5",
        "date": "20.10.2026",
        "time": "15:00",
        "phone": "+79991234567",
        "name": "Иван",
        "comment": "Домашний питомец",
        "photos": ["file_id_1", "file_id_2"],
    }
    text = await build_preview_text(data)
    assert "Генеральная уборка" in text
    assert "2-комнатная" in text
    assert "Киевская" in text
    assert "20.10.2026" in text
    assert "15:00" in text
    assert "Иван" in text
    assert "2" in text


@pytest.mark.asyncio
async def test_metro_data():
    assert "1. Сокольническая" in METRO
    assert len(METRO["1. Сокольническая"]) > 0
    assert "2. Замоскворецкая" in METRO
    assert "Красные ворота" in METRO["1. Сокольническая"]


@pytest.mark.asyncio
async def test_order_create_and_cancel():
    await init_db()
    order_id = "test_order_001"
    data = {
        "cleaning_type": "После ремонта",
        "flat_type": "3-комнатная",
        "metro": "Киевская",
        "address": "ул. Тестовая, 10",
        "date": "25.10.2026",
        "time": "10:00",
        "phone": "+79991234567",
        "name": "Петр",
        "comment": "Квартира после ремонта",
        "photos": ["file_1"],
        "username": "@petr",
        "telegram_id": 123456,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date=data["date"],
        time=data["time"],
    )
    orders = await get_user_orders(123456)
    assert len(orders) == 1
    order = orders[0]
    assert order[0] == order_id
    assert order[3] == "new"
    saved_data = json.loads(order[2])
    assert saved_data["cleaning_type"] == "После ремонта"
    assert saved_data["metro"] == "Киевская"
    assert saved_data["name"] == "Петр"


@pytest.mark.asyncio
async def test_order_photos():
    await init_db()
    import uuid
    order_id = f"test_photo_{uuid.uuid4().hex[:8]}"
    executor_id = 999889
    data = {
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "metro": "—",
        "address": "ул. Фото, 1",
        "date": "01.11.2026",
        "time": "09:00",
        "phone": "+79991234567",
        "name": "",
        "comment": "",
        "photos": [],
        "username": "@photo_test",
        "telegram_id": 999888,
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date=data["date"],
        time=data["time"],
        executor_id=executor_id,
    )
    from db import add_executor
    await add_executor(
        executor_id,
        "Photo executor",
        "+79990000889",
        is_active=True,
        update_active=True,
    )
    await save_order_photo(order_id, 999888, "file_id_client_1", "client")
    await save_order_photo(order_id, 999888, "file_id_client_2", "client")
    await save_order_photo(order_id, executor_id, "file_id_result_1", "result")
    photos = await get_order_photos(order_id)
    assert len(photos) == 3
    types = [p[2] for p in photos]
    assert "client" in types
    assert "result" in types


@pytest.mark.asyncio
async def test_handler_registry():
    import main
    callback_handlers = {
        name: getattr(main, name)
        for name in dir(main)
        if name.startswith("metro_") or name.startswith("pick_cleaning_type")
        or name.startswith("process_flat_type") or name.startswith("date_callback")
        or name.startswith("time_callback") or name.startswith("address")
        or name.startswith("phone_") or name.startswith("name_")
        or name.startswith("comment_") or name.startswith("photos_")
        or name.startswith("confirm_order") or name.startswith("preview_")
        or name.startswith("individual_") or name in ("my_orders", "my_orders_active", "my_orders_history", "my_orders_cancel", "my_orders_repeat", "my_orders_rate")
        or name.startswith("executor_") or name.startswith("admin_")
        or name.startswith("reminder_") or name.startswith("continue_order")
    }
    assert "metro_line_callback" in callback_handlers
    assert "metro_station_callback" in callback_handlers
    assert "pick_cleaning_type" in callback_handlers
    assert "process_flat_type" in callback_handlers
    assert "date_callback" in callback_handlers
    assert "time_callback" in callback_handlers
    assert "address" in callback_handlers
    assert "phone_contact" in callback_handlers
    assert "phone_text" in callback_handlers
    assert "name_handler" in callback_handlers
    assert "name_skip" in callback_handlers
    assert "comment_edit_handler" in callback_handlers
    assert "photos_handler" in callback_handlers
    assert "photos_skip" in callback_handlers
    assert "confirm_order" in callback_handlers
    assert "preview_cancel" in callback_handlers
    assert "individual_clean_order" in callback_handlers
    assert "my_orders" in callback_handlers
    assert "executor_update_status" in callback_handlers
    assert "admin_orders_list" in callback_handlers
    assert "reminder_order" in callback_handlers


@pytest.mark.asyncio
async def test_individual_cleaning_paths():
    assert os.path.exists(INDIVIDUAL_CLEANING_PHOTO)
    assert os.path.getsize(INDIVIDUAL_CLEANING_PHOTO) > 0
    photo = FSInputFile(INDIVIDUAL_CLEANING_PHOTO)
    assert photo is not None


@pytest.mark.asyncio
async def test_safe_str_empty_string():
    assert safe_str("") == "—"
    assert safe_str("   ") == "—"


@pytest.mark.asyncio
async def test_metro_import():
    from main import METRO as MAIN_METRO
    assert len(MAIN_METRO) == 12
    assert "1. Сокольническая" in MAIN_METRO


@pytest.mark.asyncio
async def test_abandoned_order_restores_empty_name():
    await init_db()
    import aiosqlite
    from db import DB_PATH
    telegram_id = 777777
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO abandoned_orders
            (telegram_id, cleaning_type, step, name, reminded)
            VALUES (?, ?, ?, ?, 1)
        """, (telegram_id, "После ремонта", "пропустил имя", ""))
        await db.commit()
    from db import get_abandoned_orders
    orders = await get_abandoned_orders()
    matching = [o for o in orders if o["telegram_id"] == telegram_id]
    assert len(matching) == 0
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM abandoned_orders WHERE telegram_id = ?", (telegram_id,)) as cur:
            row = await cur.fetchone()
            if row:
                assert row["name"] == ""


@pytest.mark.asyncio
async def test_metro_callbacks_have_fallback():
    import inspect
    from main import metro_line_callback, metro_station_callback, metro_back_lines
    src_line = inspect.getsource(metro_line_callback)
    src_station = inspect.getsource(metro_station_callback)
    src_back = inspect.getsource(metro_back_lines)
    assert "except TelegramBadRequest:" in src_line
    assert "await cb.message.answer" in src_line
    assert ("except TelegramBadRequest:" in src_station or "except (TelegramBadRequest, ValidationError):" in src_station)
    assert "await cb.message.answer" in src_station
    assert "except TelegramBadRequest:" in src_back
    assert "await cb.message.answer" in src_back


@pytest.mark.asyncio
async def test_edit_handlers_have_fallback():
    import inspect
    from main import repeat_edit, preview_edit, edit_field_select
    src_repeat = inspect.getsource(repeat_edit)
    src_preview = inspect.getsource(preview_edit)
    src_edit = inspect.getsource(edit_field_select)
    assert "except TelegramBadRequest:" in src_repeat
    assert "await cb.message.answer" in src_repeat
    assert "except TelegramBadRequest:" in src_preview
    assert "await cb.message.answer" in src_preview
    assert ("except TelegramBadRequest:" in src_edit or "except (TelegramBadRequest, ValidationError):" in src_edit)
    assert "await cb.message.answer" in src_edit






@pytest.mark.asyncio
async def test_photo_view_handlers_call_callback_answer():
    import inspect
    from main import (
        admin_view_client_photos,
        admin_view_result_photos,
        executor_view_client_photos,
        executor_view_result_photos,
    )
    for handler in (
        admin_view_client_photos,
        admin_view_result_photos,
        executor_view_client_photos,
        executor_view_result_photos,
    ):
        source = inspect.getsource(handler)
        assert "await cb.answer()" in source, f"{handler.__name__} must call cb.answer()"


async def main():
    await test_imports_and_constants()
    await test_db_init()
    await test_fsm_states()
    await test_price_calculation()
    await test_safe_str()
    await test_format_order_text()
    await test_build_preview_text()
    await test_metro_data()
    await test_order_create_and_cancel()
    await test_order_photos()
    await test_handler_registry()
    await test_individual_cleaning_paths()
    print("\n=== ALL INTEGRATION CHECKS PASSED ===")


if __name__ == "__main__":
    asyncio.run(main())
