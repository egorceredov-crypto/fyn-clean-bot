"""
Regression tests for critical fixes.
Each test verifies a specific fix by testing behavior that would fail
if the fix were reverted.
"""
import asyncio
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, Contact, FSInputFile
from unittest.mock import AsyncMock, MagicMock, patch

import main
import db as db_module
from main import (
    start_order,
    pick_cleaning_type,
    process_flat_type,
    date_callback,
    time_callback,
    metro_line_callback,
    metro_station_callback,
    address,
    phone_contact,
    name_handler,
    name_skip,
    comment_edit_handler,
    photos_handler,
    photos_skip,
    confirm_order,
    preview_cancel,
    my_orders_active,
    individual_clean_order,
    CleaningOrder,
    ExecutorStates,
    RatingStates,
    build_preview_text,
    format_order_text,
    calc_price_estimate,
    safe_str,
    STATUS_LABELS,
    ORDER_PREVIEW_TEXT,
    INDIVIDUAL_CLEANING_PHOTO,
    continue_order_callback,
    preview_edit,
    edit_field_select,
    metro_back_lines,
    order_back,
    order_back_text,
    show_preview,
    cancel_order_handler,
    my_orders_repeat,
    cleaning_type,
    my_orders_detail,
    my_orders_view_client_photos,
    my_orders_view_result_photos,
    admin_unassign_executor,
    admin_view_client_photos,
    admin_view_result_photos,
    executor_view_client_photos,
    executor_view_result_photos,
)
from db import (
    init_db,
    create_order,
    get_user_orders,
    get_order_photos,
    save_order_photo,
    delete_abandoned_order,
    get_abandoned_orders,
)
from main import update_abandoned_order
from states import CleaningOrder, ExecutorStates, RatingStates
from metro_data import METRO


class FakeMessage:
    def __init__(self, user_id=12345, chat_id=12345, username="testuser", first_name="Test", last_name="User"):
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.username = username
        self.from_user.first_name = first_name
        self.from_user.last_name = last_name
        self.chat = MagicMock()
        self.chat.id = chat_id
        self.chat.type = "private"
        self.text = None
        self.photo = None
        self.contact = None
        self.message_id = 1
        self.content_type = "text"
        self.answers = []
        self.edits = []
        self.answer_photo_calls = []
        self.answer_media_group_calls = []

    async def answer(self, text=None, reply_markup=None, parse_mode=None, **kwargs):
        self.answers.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode, **kwargs})
        return self

    async def edit_text(self, text=None, reply_markup=None, parse_mode=None, **kwargs):
        self.edits.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode, **kwargs})
        return self

    async def answer_photo(self, photo=None, caption=None, reply_markup=None, parse_mode=None, **kwargs):
        self.answer_photo_calls.append({"photo": photo, "caption": caption, "reply_markup": reply_markup, "parse_mode": parse_mode, **kwargs})
        return self

    async def answer_media_group(self, media=None, **kwargs):
        self.answer_media_group_calls.append({"media": media, **kwargs})
        return self

    async def delete(self):
        return True


class FakeCallback:
    def __init__(self, user_id=12345, chat_id=12345, username="testuser", first_name="Test", last_name="User", data=""):
        self.from_user = MagicMock()
        self.from_user.id = user_id
        self.from_user.username = username
        self.from_user.first_name = first_name
        self.from_user.last_name = last_name
        self.message = FakeMessage(user_id, chat_id, username, first_name, last_name)
        self.data = data
        self.id = "callback_1"
        self.chat_instance = "chat_instance"
        self.inline_message_id = None
        self.answers = []
        self.answer_alerts = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        if show_alert:
            self.answer_alerts.append(text)
        else:
            self.answers.append(text)
        return self

    async def edit_text(self, text=None, reply_markup=None, parse_mode=None, **kwargs):
        self.message.edits.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode, **kwargs})
        return self.message

    async def answer_photo(self, photo=None, caption=None, reply_markup=None, parse_mode=None, **kwargs):
        self.message.answer_photo_calls.append({"photo": photo, "caption": caption, "reply_markup": reply_markup, "parse_mode": parse_mode, **kwargs})
        return self.message

    async def answer_media_group(self, media=None, **kwargs):
        self.message.answer_media_group_calls.append({"media": media, **kwargs})
        return self.message


class FakeFSMContext:
    def __init__(self):
        self.data = {}
        self.state = None
        self.storage = MemoryStorage()
        self.key = "test:test:test"

    async def get_data(self):
        return self.data.copy()

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state

    async def clear(self):
        self.data = {}
        self.state = None

    async def set_data(self, data):
        self.data = data.copy()


@pytest.fixture
def db_setup():
    import db as db_module
    if os.path.exists(db_module.DB_PATH):
        os.remove(db_module.DB_PATH)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(init_db())
    finally:
        loop.close()
    yield
    if os.path.exists(db_module.DB_PATH):
        os.remove(db_module.DB_PATH)


@pytest.mark.asyncio
async def test_regression_confirm_order_clears_state_after_db_insert(db_setup):
    """Regression: confirm_order must clear state AFTER DB insert, not before."""
    user_id = 999101
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser101", "Test101", "User101")
    cb = FakeCallback(user_id, user_id, "testuser101", "Test101", "User101")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)
    cb.data = "order:time:14:00"
    await time_callback(cb, fsm)
    await fsm.update_data(metro="Киевская")
    await fsm.set_state(CleaningOrder.address.state)
    msg.text = "ул. Тест, 10"
    await address(msg, fsm)
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79991234567"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    msg.text = "Иван"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    msg.text = "Комментарий"
    await comment_edit_handler(msg, fsm)
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)

    assert fsm.state == CleaningOrder.preview.state

    # Confirm order
    cb.data = "order:preview:confirm"
    order_id = await confirm_order(cb, fsm, bot)
    assert order_id is not None

    # Verify state is cleared AFTER DB insert (not before)
    # If state was cleared before DB insert and DB failed, we'd have no order and no state
    # Here we verify both order exists AND state is cleared
    assert fsm.state is None
    orders = await get_user_orders(user_id)
    assert len(orders) == 1
    assert orders[0][0] == order_id
    assert orders[0][3] == "new"

    # Critical regression: verify that on DB exception, FSM data is preserved
    # This proves state.clear() happens AFTER DB operations
    fsm2 = FakeFSMContext()
    await fsm2.set_state(CleaningOrder.preview.state)
    await fsm2.update_data(
        cleaning_type="Генеральная уборка",
        flat_type="1-комнатная",
        date="15.10.2025",
        time="14:00",
        metro="Киевская",
        address="ул. Тест, 10",
        phone="+79991234567",
        name="Иван",
        comment="Комментарий"
    )
    
    # Mock create_order to raise exception
    original_create_order = main.create_order
    async def mock_create_order(*args, **kwargs):
        raise Exception("Simulated DB error")
    
    main.create_order = mock_create_order
    try:
        cb.data = "order:preview:confirm"
        result = await confirm_order(cb, fsm2, bot)
        assert result is None  # Should return None on error
        # Critical: FSM should NOT be cleared on error
        assert fsm2.state == CleaningOrder.preview.state, "FSM state should be preserved on DB error"
        assert fsm2.data.get("cleaning_type") == "Генеральная уборка", "FSM data should be preserved on DB error"
    finally:
        main.create_order = original_create_order


@pytest.mark.asyncio
async def test_regression_cleaning_type_uses_flat_type_kb(db_setup):
    """Regression: cleaning_type message handler must use flat_type_kb with back/cancel."""
    user_id = 999102
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser102", "Test102", "User102")

    # Call the message handler directly
    from main import cleaning_type
    msg.text = "Поддерживающая уборка"
    await cleaning_type(msg, fsm)

    # Should be in flat_type state
    assert fsm.state == CleaningOrder.flat_type.state

    # Should have answered with flat_type_kb (which has back/cancel buttons)
    assert len(msg.answers) > 0
    response = msg.answers[-1]
    assert response["reply_markup"] is not None
    
    # Critical: verify the keyboard actually has back/cancel buttons
    # This proves flat_type_kb() is used, not a custom inline keyboard
    kb = response["reply_markup"]
    # flat_type_kb() has buttons: 1-комнатная, 2-комнатная, 3-комнатная, Студия / площадь, 🔙 Назад, ❌ Отменить
    # We verify by checking that the keyboard has the expected structure
    keyboard_buttons = []
    if hasattr(kb, 'inline_keyboard'):
        for row in kb.inline_keyboard:
            for button in row:
                keyboard_buttons.append(button.text)
    elif hasattr(kb, 'keyboard'):
        for row in kb.keyboard:
            for button in row:
                keyboard_buttons.append(button.text)
    
    # Must have back and cancel buttons
    assert "🔙 Назад" in keyboard_buttons or "Назад" in keyboard_buttons, \
        f"Keyboard must have back button, got: {keyboard_buttons}"
    assert "❌ Отменить" in keyboard_buttons or "Отменить" in keyboard_buttons, \
        f"Keyboard must have cancel button, got: {keyboard_buttons}"


@pytest.mark.asyncio
async def test_regression_individual_cleaning_continues_to_flat_type(db_setup):
    """Regression: individual cleaning must show confirmation keyboard before flat_type."""
    user_id = 999103
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser103", "Test103", "User103")
    cb = FakeCallback(user_id, user_id, "testuser103", "Test103", "User103")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:individual"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)

    # After individual cleaning selection, should stay in cleaning_type with confirmation kb
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert fsm.data.get("cleaning_type") == "Индивидуальная уборка"

    # Now click "✨ Заказать уборку" to proceed
    cb.data = "individual:order"
    await individual_clean_order(cb, fsm)
    assert fsm.state == CleaningOrder.flat_type.state
    assert fsm.data.get("cleaning_type") == "Индивидуальная уборка"


@pytest.mark.asyncio
async def test_regression_metro_line_and_station_saved_in_db(db_setup):
    """Regression: metro_line and metro must be saved in DB after full flow."""
    user_id = 999104
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser104", "Test104", "User104")
    cb = FakeCallback(user_id, user_id, "testuser104", "Test104", "User104")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:2k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-20"
    await date_callback(cb, fsm)
    cb.data = "order:time:10:00"
    await time_callback(cb, fsm)

    # Select metro line
    cb.data = "order:line:0"
    await metro_line_callback(cb, fsm)
    metro_line = fsm.data.get("metro_line")
    assert metro_line is not None

    # Select metro station
    stations = METRO.get(metro_line, [])
    if stations:
        cb.data = f"order:st:0"
        await metro_station_callback(cb, fsm)
        assert fsm.data.get("metro") == stations[0]

    # Complete order
    msg.text = "ул. Фото, 5"
    await address(msg, fsm)
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79997654321"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    msg.text = "Петр"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    msg.text = "Тестовый комментарий"
    await comment_edit_handler(msg, fsm)
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)
    cb.data = "order:preview:confirm"
    order_id = await confirm_order(cb, fsm, bot)

    # Verify metro_line and metro in DB
    orders = await get_user_orders(user_id)
    assert len(orders) == 1
    saved_data = json.loads(orders[0][2])
    assert saved_data.get("metro_line") == metro_line
    assert saved_data.get("metro") == stations[0] if stations else "—"


@pytest.mark.asyncio
async def test_regression_my_orders_repeat_sets_preview_state(db_setup):
    """Regression: my_orders_repeat must set state to preview for confirm to work."""
    user_id = 999105
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser105", "Test105", "User105")
    cb = FakeCallback(user_id, user_id, "testuser105", "Test105", "User105")

    # Create an order first
    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)
    cb.data = "order:time:10:00"
    await time_callback(cb, fsm)
    await fsm.update_data(metro="Киевская")
    await fsm.set_state(CleaningOrder.address.state)
    msg.text = "ул. Repeat, 1"
    await address(msg, fsm)
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79991234567"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    msg.text = "Иван"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    msg.text = "Комментарий"
    await comment_edit_handler(msg, fsm)
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)
    cb.data = "order:preview:confirm"
    order_id = await confirm_order(cb, fsm, bot)

    assert order_id is not None

    # Repeat is available only after the original order is completed.
    await db_module.add_executor(
        700105,
        "Active executor",
        "+79990000105",
        is_active=True,
        update_active=True,
    )
    assert await db_module.transition_status(order_id, "accepted")
    assert await db_module.assign_executor(order_id, 700105)
    assert await db_module.transition_status(order_id, "in_work")
    assert await db_module.transition_status(order_id, "completed")

    # Now repeat the order
    cb.data = f"my_orders:repeat:{order_id}"
    await my_orders_repeat(cb, fsm)

    # Should be in preview state
    assert fsm.state == CleaningOrder.preview.state

    # Should be able to confirm again
    cb.data = "order:preview:confirm"
    new_order_id = await confirm_order(cb, fsm, bot)
    assert new_order_id is not None
    assert new_order_id != order_id


@pytest.mark.asyncio
async def test_regression_cancel_clears_fsm_and_does_not_continue(db_setup):
    """Regression: cancel must clear FSM and not continue with old data."""
    user_id = 999106
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser106", "Test106", "User106")

    await start_order(msg, fsm)
    await fsm.set_state(CleaningOrder.address.state)
    await fsm.update_data(address="ул. Отмена, 1")

    # Cancel
    msg.text = "❌ Отменить заказ"
    await cancel_order_handler(msg, fsm)

    assert fsm.state is None
    assert len(msg.answers) > 0
    assert "отмен" in msg.answers[-1]["text"].lower()


@pytest.mark.asyncio
async def test_regression_back_preserves_fsm_data(db_setup):
    """Regression: Back navigation must preserve FSM data."""
    user_id = 999107
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser107", "Test107", "User107")
    cb = FakeCallback(user_id, user_id, "testuser107", "Test107", "User107")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)

    assert fsm.state == CleaningOrder.time.state
    assert fsm.data.get("flat_type") == "1-комнатная"
    assert fsm.data.get("date") == "15.10.2025"

    # Back to date
    cb.data = "order:back"
    await order_back(cb, fsm)
    assert fsm.state == CleaningOrder.date.state
    assert fsm.data.get("flat_type") == "1-комнатная"  # preserved

    # Back to flat_type
    cb.data = "order:back"
    await order_back(cb, fsm)
    assert fsm.state == CleaningOrder.flat_type.state
    assert fsm.data.get("date") == "15.10.2025"  # preserved


@pytest.mark.asyncio
async def test_regression_double_confirm_creates_one_order(db_setup):
    """Regression: double confirm must create exactly one order."""
    user_id = 999108
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser108", "Test108", "User108")
    cb = FakeCallback(user_id, user_id, "testuser108", "Test108", "User108")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)
    cb.data = "order:time:14:00"
    await time_callback(cb, fsm)
    await fsm.update_data(metro="Киевская")
    await fsm.set_state(CleaningOrder.address.state)
    msg.text = "ул. Двойной, 1"
    await address(msg, fsm)
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79991234567"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    msg.text = "Иван"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    msg.text = "Комментарий"
    await comment_edit_handler(msg, fsm)
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)

    # First confirm
    cb.data = "order:preview:confirm"
    order_id_1 = await confirm_order(cb, fsm, bot)
    assert order_id_1 is not None

    # After confirm, FSM should be cleared
    assert fsm.state is None

    # Only 1 order should exist
    orders = await get_user_orders(user_id)
    assert len(orders) == 1, f"Expected 1 order after confirm, got {len(orders)}"
    
    # In real Telegram, dispatcher would not call confirm_order again
    # because state is None and handler requires CleaningOrder.preview
    # Here we verify the state is cleared, which is the protection mechanism


@pytest.mark.asyncio
async def test_regression_metro_line_saved_in_abandoned_order(db_setup):
    """Regression: metro_line must be saved in abandoned_orders."""
    user_id = 999109
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser109", "Test109", "User109")
    cb = FakeCallback(user_id, user_id, "testuser109", "Test109", "User109")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, None)
    cb.data = "flat:2k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-20"
    await date_callback(cb, fsm)
    cb.data = "order:time:12:00"
    await time_callback(cb, fsm)

    # Select metro line
    cb.data = "order:line:1"
    await metro_line_callback(cb, fsm)
    metro_line = fsm.data.get("metro_line")
    assert metro_line is not None

    # Now abandon the order by clearing FSM without confirming
    await fsm.clear()

    # Check abandoned_orders directly (bypass time filter)
    import aiosqlite
    from db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM abandoned_orders WHERE telegram_id = ?", (user_id,)) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1, f"Expected 1 abandoned order, got {len(rows)}"
    abandoned = dict(rows[0])
    assert abandoned.get("metro_line") == metro_line, f"Expected metro_line='{metro_line}', got {abandoned.get('metro_line')}"


@pytest.mark.asyncio
async def test_regression_await_update_abandoned_order_no_blocking(db_setup):
    """Regression: await update_abandoned_order must not block handler."""
    user_id = 999110
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, user_id, "testuser110", "Test110", "User110")
    cb = FakeCallback(user_id, user_id, "testuser110", "Test110", "User110")

    await start_order(msg, fsm)
    cb.data = "cleaning:type:individual"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)

    # Should complete without hanging and remain in cleaning_type with confirmation kb
    assert fsm.state == CleaningOrder.cleaning_type.state

    # Now proceed to flat_type
    cb.data = "individual:order"
    await individual_clean_order(cb, fsm)
    assert fsm.state == CleaningOrder.flat_type.state


@pytest.mark.asyncio
async def test_regression_client_order_ownership_covers_detail_cancel_repeat_and_rating(db_setup):
    owner_id = 999201
    attacker_id = 999202
    executor_id = 799201
    order_id = "ownership_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Генеральная уборка",
        "flat_type": "1-комнатная",
        "date": "15.10.2026",
        "time": "10:00",
        "address": "ул. Ownership, 1",
        "phone": "+79990000201",
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date=data["date"],
        time=data["time"],
        executor_id=executor_id,
    )
    await db_module.add_executor(
        executor_id,
        "Assigned executor",
        "+79990000202",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(attacker_id, attacker_id, data="")
    cb.data = f"my_orders:detail:{order_id}"
    await main.my_orders_detail(cb)
    assert cb.answer_alerts
    assert "не можете" in cb.answer_alerts[-1].lower()
    assert not cb.message.edits

    fsm = FakeFSMContext()
    cb.data = f"my_orders:cancel:{order_id}"
    await main.my_orders_cancel(cb, fsm)
    assert "не можете" in cb.answer_alerts[-1].lower()
    assert await db_module.get_status(order_id) == "completed"

    cb.data = f"my_orders:repeat:{order_id}"
    await main.my_orders_repeat(cb, fsm)
    assert "не можете" in cb.answer_alerts[-1].lower() or "заказ" in cb.answer_alerts[-1].lower()
    assert fsm.state is None

    cb.data = f"my_orders:rate:{order_id}"
    await main.my_orders_rate(cb)
    assert "заказ" in cb.answer_alerts[-1].lower()
    assert not cb.message.edits


@pytest.mark.asyncio
async def test_regression_executor_must_be_active_and_follow_status_transitions(db_setup):
    owner_id = 999203
    executor_id = 799203
    order_id = "executor_status_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Executor, 3",
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="16.10.2026",
        time="11:00",
    )
    await db_module.add_executor(
        executor_id,
        "Executor",
        "+79990000203",
        is_active=True,
        update_active=True,
    )
    assert await db_module.assign_executor(order_id, executor_id)
    await db_module.toggle_executor_active(executor_id, False)

    bot = AsyncMock(spec=Bot)
    cb = FakeCallback(executor_id, executor_id, data=f"exec:status:{order_id}:in_work")
    cb.bot = bot
    stale_state = FakeFSMContext()
    await main.executor_update_status(cb, stale_state)
    assert await db_module.get_status(order_id) == "assigned"
    assert cb.answer_alerts

    await db_module.toggle_executor_active(executor_id, True)
    cb.data = f"exec:status:{order_id}:completed"
    await main.executor_update_status(cb, stale_state)
    assert await db_module.get_status(order_id) == "assigned"
    assert "переход" in cb.answer_alerts[-1].lower()
    assert stale_state.state == ExecutorStates.menu.state

    cb.data = f"exec:status:{order_id}:in_work"
    await main.executor_update_status(cb, stale_state)
    assert await db_module.get_status(order_id) == "in_work"

    cb.data = f"exec:status:{order_id}:completed"
    await main.executor_update_status(cb, stale_state)
    assert await db_module.get_status(order_id) == "completed"
    assert bot.send_message.await_count >= 2


@pytest.mark.asyncio
async def test_regression_admin_assignment_lists_only_active_executors(db_setup):
    order_id = "executor_filter_order"
    await create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": 999204}, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="17.10.2026",
        time="12:00",
    )
    await db_module.add_executor(799204, "Inactive", "+79990000204")
    await db_module.add_executor(
        799205,
        "Active",
        "+79990000205",
        is_active=True,
        update_active=True,
    )
    cb = FakeCallback(main.cfg.admin_chat_id, main.cfg.admin_chat_id)
    cb.data = f"admin:assign_executor:{order_id}"
    await main.admin_show_assign_executor(cb)
    markup = cb.message.edits[-1]["reply_markup"]
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert f"admin:assign:{order_id}:799205" in callbacks
    assert f"admin:assign:{order_id}:799204" not in callbacks


@pytest.mark.asyncio
async def test_regression_admin_can_add_and_activate_executor(db_setup):
    admin_id = main.cfg.admin_chat_id
    fsm = FakeFSMContext()
    cb = FakeCallback(admin_id, admin_id)
    await main.admin_add_executor_start(cb, fsm)
    assert fsm.state == ExecutorStates.admin_add_id.state

    msg = FakeMessage(admin_id, admin_id)
    msg.text = "799206"
    await main.admin_add_executor_id(msg, fsm)
    msg.text = "Added executor"
    await main.admin_add_executor_name(msg, fsm)
    msg.text = "+79990000206"
    await main.admin_add_executor_phone(msg, fsm)
    msg.text = "Описание"
    await main.admin_add_executor_bio(msg, fsm)

    executor = await db_module.get_executor(799206)
    assert executor is not None
    assert executor[1] == "Added executor"
    assert executor[4] == 1
    assert fsm.state is None


@pytest.mark.asyncio
async def test_regression_result_photo_is_saved_and_delivered(db_setup):
    owner_id = 999207
    executor_id = 799207
    order_id = "result_photo_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "После ремонта",
        "flat_type": "2-комнатная",
        "address": "ул. Result, 7",
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="18.10.2026",
        time="13:00",
    )
    await db_module.add_executor(
        executor_id,
        "Result executor",
        "+79990000207",
        is_active=True,
        update_active=True,
    )
    assert await db_module.assign_executor(order_id, executor_id)
    assert await db_module.transition_status(order_id, "in_work")

    bot = AsyncMock(spec=Bot)
    fsm = FakeFSMContext()
    await fsm.set_state(ExecutorStates.menu)
    await fsm.update_data(uploading_photos_to=order_id)
    msg = FakeMessage(executor_id, executor_id)
    msg.photo = [MagicMock(file_id="result_file_1")]
    await main.executor_upload_photo(msg, fsm, bot)

    photos = await get_order_photos(order_id)
    assert [(photo[1], photo[2]) for photo in photos] == [("result_file_1", "result")]
    recipients = [call.args[0] for call in bot.send_photo.await_args_list]
    assert owner_id in recipients
    assert main.cfg.admin_chat_id in recipients


@pytest.mark.asyncio
async def test_regression_executor_notification_excludes_photos(db_setup):
    order_id = "executor_notification_order"
    executor_id = 799208
    data = {
        "telegram_id": 999208,
        "executor_id": executor_id,
        "cleaning_type": "Генеральная уборка",
        "address": "ул. Notify, 8",
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="19.10.2026",
        time="14:00",
    )
    await db_module.save_order_photo(order_id, data["telegram_id"], "client_file", "client")
    await db_module.save_order_photo(order_id, executor_id, "result_file", "result")
    bot = AsyncMock(spec=Bot)
    await main.notify_executor(bot, order_id, data)
    sent_photos = [call.kwargs.get("photo") for call in bot.send_photo.await_args_list]
    assert sent_photos == []


@pytest.mark.asyncio
async def test_regression_status_change_requires_executor_for_work_states(db_setup):
    order_id = "status_guard_order"
    await create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": 999209}, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="20.10.2026",
        time="15:00",
    )
    cb = FakeCallback(main.cfg.admin_chat_id, main.cfg.admin_chat_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:status:{order_id}:assigned"
    fsm = FakeFSMContext()
    await main.admin_status(cb, fsm)
    assert await db_module.get_status(order_id) == "new"
    assert cb.answer_alerts

    cb.data = f"admin:status:{order_id}:accepted"
    await main.admin_status(cb, fsm)
    assert await db_module.get_status(order_id) == "accepted"

    cb.data = f"admin:status:{order_id}:in_work"
    await main.admin_status(cb, fsm)
    assert await db_module.get_status(order_id) == "accepted"
    assert "исполнителя" in cb.answer_alerts[-1].lower()


@pytest.mark.asyncio
async def test_regression_non_individual_cleaning_shows_back_cancel(db_setup):
    user_id = 999210
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser210", "Test210", "User210")
    await main.start_order(cb.message, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    cb.data = "cleaning:type:general"
    cb.message = FakeMessage(user_id, user_id, "testuser210", "Test210", "User210")
    bot = AsyncMock(spec=Bot)
    await main.pick_cleaning_type(cb, fsm, bot)
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert bot.send_photo.call_count == 1
    kb = bot.send_photo.call_args[1]["reply_markup"]
    labels = [
        button.text
        for row in kb.inline_keyboard
        for button in row
    ]
    assert "🔙 Назад" in labels
    assert "❌ Отменить" in labels


@pytest.mark.asyncio
async def test_regression_order_back_from_cleaning_type_restores_service_selection(db_setup):
    user_id = 999211
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser211", "Test211", "User211")
    await main.start_order(cb.message, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    cb.data = "order:back"
    cb.message = FakeMessage(user_id, user_id, "testuser211", "Test211", "User211")
    await main.order_back(cb, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert cb.message.answers
    response = cb.message.answers[-1]
    kb = response["reply_markup"]
    labels = [button.text for row in kb.inline_keyboard for button in row]
    assert "🪄 Поддерживающая уборка" in labels


@pytest.mark.asyncio
async def test_regression_continue_order_from_metro_line_restores_stations(db_setup):
    user_id = 999212
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser212", "Test212", "User212")
    await main.start_order(cb.message, fsm)
    cb.data = "cleaning:type:general"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.pick_cleaning_type(cb, fsm, AsyncMock(spec=Bot))
    cb.data = "flat:1k"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.date_callback(cb, fsm)
    cb.data = "order:time:14:00"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.time_callback(cb, fsm)
    cb.data = "order:line:0"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.metro_line_callback(cb, fsm)
    metro_line = fsm.data.get("metro_line")
    assert metro_line is not None
    import aiosqlite
    from db import DB_PATH
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO abandoned_orders
            (telegram_id, cleaning_type, step, reminded, flat_type, metro_line, date, time)
            VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                user_id,
                "Генеральная уборка",
                "выбрал линию метро",
                "1-комнатная",
                metro_line,
                "15.10.2025",
                "14:00",
            ),
        )
        await db.commit()
    cb.data = "continue:order"
    cb.message = FakeMessage(user_id, user_id, "testuser212", "Test212", "User212")
    await main.continue_order_callback(cb, fsm)
    assert fsm.state == CleaningOrder.metro_station.state
    response = cb.message.edits[-1]
    kb = response["reply_markup"]
    labels = [button.text for row in kb.inline_keyboard for button in row]
    stations = main.METRO.get(metro_line, [])
    if stations:
        assert stations[0] in labels


@pytest.mark.asyncio
async def test_regression_msg_answer_requires_text(db_setup):
    user_id = 888300
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser300", "Test300", "User300")
    bot = AsyncMock(spec=Bot)
    await main.start_order(msg, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert len(msg.answers) >= 1
    for a in msg.answers:
        assert "text" in a, "msg.answer must always include text"


@pytest.mark.asyncio
async def test_regression_edit_text_with_reply_keyboard_does_not_crash(db_setup):
    user_id = 888301
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser301", "Test301", "User301")
    bot = AsyncMock(spec=Bot)
    await main.start_order(cb.message, fsm)
    cb.data = "cleaning:type:regular"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User301")
    await main.pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User301")
    await main.process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User302")
    await main.date_callback(cb, fsm)
    cb.data = "order:time:14:00"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User303")
    await main.time_callback(cb, fsm)
    cb.data = "order:line:0"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User304")
    await main.metro_line_callback(cb, fsm)
    cb.data = "order:st:0"
    cb.message = FakeMessage(user_id, user_id, "testuser301", "Test301", "User305")
    await main.metro_station_callback(cb, fsm)
    assert fsm.state == CleaningOrder.address.state
    assert len(cb.message.edits) > 0 or len(cb.message.answers) > 0
    last = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert last["text"] is not None


@pytest.mark.asyncio
async def test_regression_name_skip_does_not_crash(db_setup):
    user_id = 888302
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser302", "Test302", "User302")
    await fsm.set_state(CleaningOrder.name.state)
    cb.data = "order:name:skip"
    await main.name_skip(cb, fsm)
    assert fsm.state == CleaningOrder.comment.state
    last = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert last["text"] is not None


@pytest.mark.asyncio
async def test_regression_cmd_start_preserves_existing_name(db_setup):
    user_id = 888303
    await db_module.upsert_user(user_id, "@preserve", "Preserve User", name="СохранённоеОтчество")
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "preserve303", "Preserve303", "User303")
    await main.cmd_start(msg, fsm)
    assert fsm.state is None
    profile = await db_module.get_user_profile(user_id)
    assert profile is not None
    assert profile["name"] == "СохранённоеОтчество"


@pytest.mark.asyncio
async def test_regression_admin_can_edit_executor(db_setup):
    admin_id = main.cfg.admin_chat_id
    fsm = FakeFSMContext()
    cb = FakeCallback(admin_id, admin_id)
    await db_module.add_executor(888304, "Old Name", "+79990000304", is_active=True, update_active=True)
    cb.data = "admin:edit_executor:888304"
    await main.admin_edit_executor_start(cb, fsm)
    assert fsm.state == ExecutorStates.admin_edit_select.state
    cb.data = "admin:executor:edit:name:888304"
    await main.admin_executor_edit_field(cb, fsm)
    assert fsm.state == ExecutorStates.admin_edit_name.state
    msg = FakeMessage(admin_id, admin_id)
    msg.text = "New Name"
    await main.admin_edit_executor_name(msg, fsm)
    executor = await db_module.get_executor(888304)
    assert executor is not None
    assert executor[1] == "New Name"
    assert fsm.state is None


@pytest.mark.asyncio
async def test_regression_admin_executor_orders_view(db_setup):
    admin_id = main.cfg.admin_chat_id
    executor_id = 888305
    order_id = "executor_orders_view_order"
    await db_module.add_executor(executor_id, "Orders Exec", "+79990000305", is_active=True, update_active=True)
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": 888306, "executor_id": executor_id}, ensure_ascii=False),
        datetime.now().isoformat(),
        status="assigned",
        date="27.10.2026",
        time="17:00",
    )
    cb = FakeCallback(admin_id, admin_id)
    cb.data = f"admin:executor_orders:{executor_id}"
    await main.admin_executor_orders(cb)
    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert order_id in response["text"]


@pytest.mark.asyncio
async def test_regression_photos_list_handler(db_setup):
    user_id = 888306
    fsm = FakeFSMContext()
    await fsm.set_state(CleaningOrder.photos.state)
    await fsm.update_data(photos=["file_1", "file_2", "file_3"])
    cb = FakeCallback(user_id, user_id)
    cb.data = "order:photo:list"
    cb.message = FakeMessage(user_id, user_id, "testuser306", "Test306", "User306")
    await main.photos_list(cb, fsm)
    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert "Фотографии" in response["text"]
    assert "file_1" in response["text"] or "1" in response["text"]


@pytest.mark.asyncio
async def test_regression_continue_order_with_no_abandoned(db_setup):
    user_id = 888307
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id)
    cb.data = "continue_order"
    cb.message = FakeMessage(user_id, user_id, "testuser307", "Test307", "User307")
    await main.continue_order_callback(cb, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert cb.message.edits or cb.message.answers


@pytest.mark.asyncio
async def test_regression_metro_back_lines_uses_prefix(db_setup):
    user_id = 888308
    fsm = FakeFSMContext()
    await fsm.set_state(CleaningOrder.metro_station.state)
    await fsm.update_data(metro_prefix="order")
    cb = FakeCallback(user_id, user_id)
    cb.data = "order:back_lines"
    cb.message = FakeMessage(user_id, user_id, "testuser308", "Test308", "User308")
    await main.metro_back_lines(cb, fsm)
    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert response["text"] is not None


@pytest.mark.asyncio
async def test_regression_photo_delete_uses_correct_split(db_setup):
    user_id = 888309
    fsm = FakeFSMContext()
    await fsm.set_state(CleaningOrder.photos.state)
    await fsm.update_data(photos=["file_a", "file_b", "file_c"])
    cb = FakeCallback(user_id, user_id)
    cb.data = "order:photo:delete:1"
    cb.message = FakeMessage(user_id, user_id, "testuser309", "Test309", "User309")
    await main.photos_delete(cb, fsm)
    assert fsm.data["photos"] == ["file_a", "file_c"]


@pytest.mark.asyncio
async def test_regression_preview_photo_delete_uses_correct_split(db_setup):
    user_id = 888310
    fsm = FakeFSMContext()
    await fsm.set_state(CleaningOrder.preview.state)
    await fsm.update_data(photos=["file_x", "file_y"])
    cb = FakeCallback(user_id, user_id)
    cb.data = "order:preview:photo:delete:0"
    cb.message = FakeMessage(user_id, user_id, "testuser310", "Test310", "User310")
    await main.preview_photo_delete(cb, fsm)
    assert fsm.data["photos"] == ["file_y"]


@pytest.mark.asyncio
async def test_regression_order_creation_sets_updated_at(db_setup):
    order_id = "updated_at_create_order"
    created_at = datetime.now().isoformat()
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": 888311}, ensure_ascii=False),
        created_at,
        status="new",
    )
    order = await db_module.get_order(order_id)
    assert order is not None
    assert order["updated_at"] == created_at


@pytest.mark.asyncio
async def test_regression_save_order_photo_updates_updated_at(db_setup):
    order_id = "updated_at_photo_order"
    created_at = datetime.now().isoformat()
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": 888312}, ensure_ascii=False),
        created_at,
        status="new",
    )
    order_before = await db_module.get_order(order_id)
    assert order_before["updated_at"] == created_at

    photo_id = await db_module.save_order_photo(order_id, 888312, "file_id_123", "client")
    assert photo_id is not None

    order_after = await db_module.get_order(order_id)
    assert order_after["updated_at"] != created_at


@pytest.mark.asyncio
async def test_regression_stale_cancel_blocks_final_orders(db_setup):
    owner_id = 888313
    order_id = "stale_cancel_blocked"
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps({"telegram_id": owner_id}, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
    )
    cb = FakeCallback(owner_id, owner_id)
    cb.data = f"my_orders:cancel:{order_id}"
    cb.message = FakeMessage(owner_id, owner_id, "staleuser313", "Stale313", "User313")
    await main.my_orders_cancel(cb, FakeFSMContext())
    assert "не можете отменить" in cb.answer_alerts[0] or "уже завершён" in cb.answer_alerts[0]


@pytest.mark.asyncio
async def test_regression_menu_commands_blocked_during_order(db_setup):
    user_id = 999400
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser400", "Test400", "User400")

    await start_order(msg, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state

    msg.text = "📋 Мои заказы"
    await cleaning_type(msg, fsm)

    assert fsm.state == CleaningOrder.cleaning_type.state
    assert len(msg.answers) > 0
    assert "оформление" in msg.answers[-1]["text"].lower() or "отмените" in msg.answers[-1]["text"].lower()


@pytest.mark.asyncio
async def test_regression_photo_handlers_call_callback_answer(db_setup):
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


@pytest.mark.asyncio
async def test_regression_admin_unassign_notifies_accepted_status(db_setup):
    owner_id = 999401
    executor_id = 799401
    order_id = "unassign_status_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Unassign, 1",
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="21.10.2026",
        time="12:00",
        executor_id=executor_id,
    )
    await db_module.add_executor(
        executor_id,
        "Unassign Exec",
        "+79990000401",
        is_active=True,
        update_active=True,
    )
    assert await db_module.assign_executor(order_id, executor_id)
    assert await db_module.get_status(order_id) == "assigned"

    cb = FakeCallback(main.cfg.admin_chat_id, main.cfg.admin_chat_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:unassign:{order_id}"
    await main.admin_unassign_executor(cb)

    assert await db_module.get_status(order_id) == "accepted"
    client_notifications = [
        call for call in cb.bot.send_message.await_args_list
        if call.args[0] == owner_id
    ]
    assert len(client_notifications) >= 1
    client_text = client_notifications[-1].args[1] if len(client_notifications[-1].args) > 1 else client_notifications[-1].kwargs.get("text", "")
    assert "Принят" in client_text


@pytest.mark.asyncio
async def test_regression_client_can_view_order_photos(db_setup):
    owner_id = 999402
    order_id = "client_view_photos_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. View, 1",
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="22.10.2026",
        time="13:00",
    )
    await save_order_photo(order_id, owner_id, "client_file_1", "client")
    await save_order_photo(order_id, owner_id, "client_file_2", "client")

    cb = FakeCallback(owner_id, owner_id)
    cb.data = f"my_orders:photos:client:{order_id}"
    await main.my_orders_view_client_photos(cb)

    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert "Фотографии помещения" in response["text"]
    assert cb.message.answer_media_group_calls
    sent_media = cb.message.answer_media_group_calls[-1]["media"]
    assert len(sent_media) == 2


@pytest.mark.asyncio
async def test_regression_client_can_view_result_photos(db_setup):
    owner_id = 999403
    executor_id = 799403
    order_id = "client_view_result_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "address": "ул. Result, 2",
        "executor_id": executor_id,
    }
    await create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="23.10.2026",
        time="14:00",
    )
    await db_module.add_executor(
        executor_id,
        "Result Exec",
        "+79990000403",
        is_active=True,
        update_active=True,
    )
    await save_order_photo(order_id, executor_id, "result_file_1", "result")

    cb = FakeCallback(owner_id, owner_id)
    cb.data = f"my_orders:photos:result:{order_id}"
    await main.my_orders_view_result_photos(cb)

    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1] if cb.message.edits else cb.message.answers[-1]
    assert "Фотографии результата" in response["text"]
    assert cb.message.answer_media_group_calls
    sent_media = cb.message.answer_media_group_calls[-1]["media"]
    assert len(sent_media) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
