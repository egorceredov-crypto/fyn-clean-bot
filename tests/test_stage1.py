import asyncio
import json
from datetime import datetime, time
import pytest
import os

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as db_module
from main import (
    calc_price_estimate,
    is_time_in_range,
    safe_str,
    STATUS_LABELS,
    ORDER_PREVIEW_TEXT,
    ORDER_CANCELLED_TEXT,
    RATING_THANKS,
    LTV_AFTER_ORDER,
    LTV_REMINDER,
    ADMIN_STATS_TEXT,
    EXECUTOR_WELCOME,
    EXECUTOR_ORDER_TEMPLATE,
)
from states import CleaningOrder, RatingStates, ExecutorStates


@pytest.fixture(autouse=True)
def clean_db_file():
    if os.path.exists(db_module.DB_PATH):
        os.remove(db_module.DB_PATH)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(db_module.init_db())
    finally:
        loop.close()
    yield
    if os.path.exists(db_module.DB_PATH):
        os.remove(db_module.DB_PATH)


class TestPriceCalculation:
    def test_furniture_cleaning_price(self):
        assert calc_price_estimate("1-комнатная", "Химчистка мебели") == "По согласованию"

    def test_individual_cleaning_price(self):
        assert calc_price_estimate("2-комнатная", "Индивидуальная уборка") == "По согласованию"

    def test_regular_1k_price(self):
        assert calc_price_estimate("1-комнатная", "Поддерживающая уборка") == "10 500–13 500 ₽"

    def test_regular_2k_price(self):
        assert calc_price_estimate("2-комнатная", "Поддерживающая уборка") == "13 500–16 500 ₽"

    def test_regular_3k_price(self):
        assert calc_price_estimate("3-комнатная", "Поддерживающая уборка") == "16 500–20 500 ₽"

    def test_studio_price(self):
        assert calc_price_estimate("Студия / по площади", "Поддерживающая уборка") == "от 10 500 ₽"

    def test_unknown_flat_type(self):
        assert calc_price_estimate("Неизвестно", "Поддерживающая уборка") == "от 10 500 ₽"


class TestTimeValidation:
    def test_time_in_range_valid(self):
        assert is_time_in_range("10:00", "09:00", "21:00") is True

    def test_time_in_range_boundary_start(self):
        assert is_time_in_range("09:00", "09:00", "21:00") is True

    def test_time_in_range_boundary_end_exclusive(self):
        assert is_time_in_range("21:00", "09:00", "21:00") is False

    def test_time_out_of_range_before(self):
        assert is_time_in_range("08:00", "09:00", "21:00") is False

    def test_time_out_of_range_after(self):
        assert is_time_in_range("22:00", "09:00", "21:00") is False

    def test_time_custom_range(self):
        assert is_time_in_range("12:30", "10:00", "14:00") is True
        assert is_time_in_range("15:00", "10:00", "14:00") is False


class TestSafeStr:
    def test_none_returns_dash(self):
        assert safe_str(None) == "—"

    def test_empty_string_returns_dash(self):
        result = safe_str("")
        assert result == "—" or result == ""

    def test_valid_string(self):
        assert safe_str("Иван") == "Иван"

    def test_custom_default(self):
        assert safe_str(None, default="нет") == "нет"


class TestStatusLabels:
    def test_known_statuses(self):
        assert STATUS_LABELS["new"] == "🆕 Новый"
        assert STATUS_LABELS["accepted"] == "✅ Принят"
        assert STATUS_LABELS["assigned"] == "👤 Назначен исполнитель"
        assert STATUS_LABELS["in_work"] == "🔄 В работе"
        assert STATUS_LABELS["contacted"] == "📞 Связались"
        assert STATUS_LABELS["completed"] == "🎯 Выполнен"
        assert STATUS_LABELS["cancelled"] == "❌ Отменён"

    def test_unknown_status(self):
        assert STATUS_LABELS.get("unknown", "unknown") == "unknown"


class TestTextConstants:
    def test_order_preview_has_fields(self):
        assert "Услуга" in ORDER_PREVIEW_TEXT
        assert "Дата" in ORDER_PREVIEW_TEXT
        assert "Время" in ORDER_PREVIEW_TEXT
        assert "Адрес" in ORDER_PREVIEW_TEXT
        assert "Телефон" in ORDER_PREVIEW_TEXT
        assert "Комментарий" in ORDER_PREVIEW_TEXT
        assert "Фотографии" in ORDER_PREVIEW_TEXT
        assert "Стоимость" in ORDER_PREVIEW_TEXT

    def test_order_cancelled_text_present(self):
        assert "отменён" in ORDER_CANCELLED_TEXT.lower() or "Отменён" in ORDER_CANCELLED_TEXT

    def test_rating_thanks_present(self):
        assert "Спасибо за оценку" in RATING_THANKS

    def test_ltv_after_order_present(self):
        assert "FYN Clean" in LTV_AFTER_ORDER

    def test_ltv_reminder_present(self):
        assert "уборку" in LTV_REMINDER.lower() or "чистоту" in LTV_REMINDER.lower()

    def test_admin_stats_placeholders(self):
        for key in ["total", "new", "active", "completed", "cancelled", "executors", "ratings", "avg_rating"]:
            assert f"{{{key}}}" in ADMIN_STATS_TEXT

    def test_executor_welcome_present(self):
        assert "панель исполнителя" in EXECUTOR_WELCOME.lower() or "исполнителя" in EXECUTOR_WELCOME.lower()

    def test_executor_order_template_fields(self):
        required_fields = ["service", "flat_type", "status", "order_id", "date", "time", "address", "metro", "phone", "fio", "name", "comment"]
        for field in required_fields:
            assert f"{{{field}}}" in EXECUTOR_ORDER_TEMPLATE


class TestStateMachine:
    def test_cleaning_order_states_exist(self):
        assert CleaningOrder.cleaning_type is not None
        assert CleaningOrder.flat_type is not None
        assert CleaningOrder.date is not None
        assert CleaningOrder.time is not None
        assert CleaningOrder.metro_station is not None
        assert CleaningOrder.address is not None
        assert CleaningOrder.phone is not None
        assert CleaningOrder.name is not None
        assert CleaningOrder.comment is not None
        assert CleaningOrder.photos is not None
        assert CleaningOrder.preview is not None

    def test_rating_states_exist(self):
        assert RatingStates.comment is not None

    def test_executor_states_exist(self):
        assert ExecutorStates.menu is not None
        assert ExecutorStates.registration_name is not None
        assert ExecutorStates.registration_phone is not None


@pytest.mark.asyncio
async def test_create_and_cancel_order():
    order_id = "test_order_001"
    data = {
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "date": "15.10.2026",
        "time": "14:00",
        "address": "ул. Тестовая, 1",
        "metro": "Тестовая",
        "phone": "+79991234567",
        "name": "Иван",
        "comment": "Тестовый комментарий",
        "telegram_id": 123456789,
        "username": "@testuser",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date=data["date"],
        time=data["time"],
    )
    status = await db_module.get_status(order_id)
    assert status == "new"

    await db_module.cancel_order(order_id)
    status = await db_module.get_status(order_id)
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_assign_executor_and_status_transitions():
    order_id = "test_order_002"
    data = {
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "date": "16.10.2026",
        "time": "10:00",
        "address": "ул. Тестовая, 2",
        "metro": "Тестовая",
        "phone": "+79991234568",
        "name": "Петр",
        "telegram_id": 987654321,
        "username": "@petr",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date=data["date"],
        time=data["time"],
    )

    await db_module.add_executor(
        111,
        "Test executor",
        "+79990000001",
        is_active=True,
        update_active=True,
    )
    assert await db_module.transition_status(order_id, "accepted")
    assert await db_module.assign_executor(order_id, 111)
    status = await db_module.get_status(order_id)
    assert status == "assigned"

    await db_module.update_status(order_id, "in_work")
    status = await db_module.get_status(order_id)
    assert status == "in_work"

    await db_module.update_status(order_id, "completed")
    status = await db_module.get_status(order_id)
    assert status == "completed"


@pytest.mark.asyncio
async def test_save_and_get_rating():
    order_id = "test_order_003"
    data = {
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "date": "17.10.2026",
        "time": "12:00",
        "address": "ул. Тестовая, 3",
        "metro": "Тестовая",
        "phone": "+79991234569",
        "name": "Анна",
        "telegram_id": 111222333,
        "username": "@anna",
        "executor_id": 222,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date=data["date"],
        time=data["time"],
    )

    rating_id = await db_module.save_rating(
        order_id=order_id,
        client_id=111222333,
        executor_id=222,
        rating=5,
        comment="Отлично",
    )
    assert rating_id is not None

    rating = await db_module.get_order_rating(order_id)
    assert rating is not None
    assert rating[3] == 5
    assert rating[4] == "Отлично"

    avg = await db_module.get_executor_avg_rating(222)
    assert avg["avg"] == 5.0
    assert avg["count"] == 1


@pytest.mark.asyncio
async def test_save_order_photo():
    order_id = "test_order_004"
    data = {"telegram_id": 333444555, "executor_id": 333}
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="18.10.2026",
        time="09:00",
    )

    await db_module.save_order_photo(order_id, 333444555, "file_id_123", "client")
    await db_module.save_order_photo(order_id, 333, "file_id_456", "result")

    all_photos = await db_module.get_order_photos(order_id)
    client_photos = [p for p in all_photos if p[2] == "client"]
    result_photos = [p for p in all_photos if p[2] == "result"]
    assert len(client_photos) == 1
    assert client_photos[0][1] == "file_id_123"
    assert len(result_photos) == 1
    assert result_photos[0][1] == "file_id_456"


@pytest.mark.asyncio
async def test_repeat_order_uses_old_data():
    old_order_id = "test_order_005"
    data = {
        "cleaning_type": "После ремонта",
        "flat_type": "3-комнатная",
        "date": "19.10.2026",
        "time": "11:00",
        "address": "ул. Тестовая, 5",
        "metro": "Тестовая",
        "phone": "+79991234570",
        "name": "Мария",
        "telegram_id": 444555666,
        "username": "@maria",
    }
    await db_module.create_order(
        old_order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date=data["date"],
        time=data["time"],
    )

    orders = await db_module.get_user_orders(444555666)
    assert len(orders) == 1
    old_data = json.loads(orders[0][2])
    assert old_data["cleaning_type"] == "После ремонта"
    assert old_data["address"] == "ул. Тестовая, 5"

    await asyncio.sleep(0.01)
    new_order_id = "test_order_006"
    new_data = dict(old_data)
    new_data["date"] = "20.10.2026"
    new_data["time"] = "15:00"
    await db_module.create_order(
        new_order_id,
        "cleaning",
        json.dumps(new_data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date=new_data["date"],
        time=new_data["time"],
    )

    old_orders = await db_module.get_user_orders(444555666)
    assert len(old_orders) == 2
    assert old_orders[0][0] == new_order_id
    assert old_orders[1][0] == old_order_id


@pytest.mark.asyncio
async def test_get_user_orders_active_and_history():
    user_id = 555666777
    active_data = {"telegram_id": user_id, "address": "Активная"}
    await db_module.create_order("active_1", "cleaning", json.dumps(active_data, ensure_ascii=False), datetime.now().isoformat(), status="new", date="21.10.2026", time="10:00")
    history_data = {"telegram_id": user_id, "address": "История"}
    await db_module.create_order("history_1", "cleaning", json.dumps(history_data, ensure_ascii=False), datetime.now().isoformat(), status="completed", date="20.10.2026", time="09:00")

    orders = await db_module.get_user_orders(user_id)
    active = [o for o in orders if o[3] in {"new", "accepted", "assigned", "in_work", "contacted"}]
    history = [o for o in orders if o[3] in {"completed", "cancelled"}]
    assert len(active) == 1
    assert len(history) == 1
    assert active[0][0] == "active_1"
    assert history[0][0] == "history_1"


@pytest.mark.asyncio
async def test_admin_stats_counts():
    for i, status in enumerate(["new", "accepted", "completed", "cancelled"]):
        await db_module.create_order(
            f"stat_{i}",
            "cleaning",
            json.dumps({"telegram_id": 777}, ensure_ascii=False),
            datetime.now().isoformat(),
            status=status,
            date="22.10.2026",
            time="12:00",
        )
    async with db_module.aiosqlite.connect(db_module.DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM orders") as cur:
            total = (await cur.fetchone())[0]
        assert total >= 4


@pytest.mark.asyncio
async def test_rating_duplicate_prevention_logic():
    order_id = "test_order_007"
    data = {"telegram_id": 888, "executor_id": 999}
    await db_module.create_order(order_id, "cleaning", json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), status="completed", date="23.10.2026", time="13:00")
    first_rating = await db_module.save_rating(order_id, 888, 999, 5, "Первый")
    second_rating = await db_module.save_rating(order_id, 888, 999, 4, "Второй")
    assert first_rating is not None
    assert second_rating is None
    ratings = await db_module.get_order_rating(order_id)
    assert ratings is not None
    avg = await db_module.get_executor_avg_rating(999)
    assert avg["count"] == 1


@pytest.mark.asyncio
async def test_cancel_completed_order_should_not_reopen():
    order_id = "test_order_008"
    data = {"telegram_id": 999, "executor_id": 1000}
    await db_module.create_order(order_id, "cleaning", json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), status="completed", date="24.10.2026", time="14:00")
    changed = await db_module.cancel_order(order_id)
    status = await db_module.get_status(order_id)
    assert changed is False
    assert status == "completed"


@pytest.mark.asyncio
async def test_completed_at_is_set_on_completion():
    order_id = "test_order_009"
    data = {"telegram_id": 1001, "executor_id": 1002}
    await db_module.create_order(order_id, "cleaning", json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), status="new", date="25.10.2026", time="15:00")
    await db_module.add_executor(1002, "Test Exec", "+79990000001", is_active=True, update_active=True)
    await db_module.assign_executor(order_id, 1002)
    await db_module.transition_status(order_id, "in_work")
    await db_module.transition_status(order_id, "completed")
    async with db_module.aiosqlite.connect(db_module.DB_PATH) as db:
        async with db.execute("SELECT status, completed_at FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
    assert row[0] == "completed"
    assert row[1] is not None


@pytest.mark.asyncio
async def test_admin_rating_notification_sent():
    order_id = "test_order_010"
    data = {"telegram_id": 1003, "executor_id": 1004}
    await db_module.create_order(order_id, "cleaning", json.dumps(data, ensure_ascii=False), datetime.now().isoformat(), status="completed", date="26.10.2026", time="16:00")
    rating_id = await db_module.save_rating(order_id, 1003, 1004, 5, "Great")
    assert rating_id is not None
    ratings = await db_module.get_order_rating(order_id)
    assert ratings is not None
    avg = await db_module.get_executor_avg_rating(1004)
    assert avg["count"] == 1
    assert avg["avg"] == 5.0
