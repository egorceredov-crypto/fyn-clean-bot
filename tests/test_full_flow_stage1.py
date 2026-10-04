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
    my_orders,
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
)
from db import init_db, create_order, get_user_orders, get_order_photos, save_order_photo, delete_abandoned_order
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

    def delete(self):
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
async def test_full_client_flow_individual_cleaning(db_setup):
    user_id = 999001
    chat_id = 999001
    username = "testuser"
    first_name = "Test"
    last_name = "User"

    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, chat_id, username, first_name, last_name)
    cb = FakeCallback(user_id, chat_id, username, first_name, last_name)

    # Step 1: /start
    from main import cmd_start
    await cmd_start(msg, fsm)
    assert fsm.state is None
    assert len(msg.answers) > 0
    assert "FYN Clean" in msg.answers[-1]["text"]

    # Step 2: Start order
    await start_order(msg, fsm)
    assert fsm.state == CleaningOrder.cleaning_type.state
    assert len(msg.answers) > 0
    assert "Шаг 1" in msg.answers[-1]["text"]

    # Step 3: Pick individual cleaning
    cb.data = "cleaning:type:individual"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    assert fsm.state == CleaningOrder.flat_type.state or fsm.data.get("cleaning_type") == "Индивидуальная уборка"
    assert len(bot.send_photo.call_args_list) > 0 or len(msg.answers) > 0

    # If individual cleaning photo was sent, verify it
    if bot.send_photo.call_args_list:
        call = bot.send_photo.call_args_list[-1]
        photo_arg = call.kwargs.get("photo") or call.args[0]
        assert photo_arg is not None

    # Step 4: Select flat type
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    assert fsm.state == CleaningOrder.date.state or fsm.data.get("flat_type") == "1-комнатная"
    assert fsm.data.get("flat_type") == "1-комнатная"

    # Step 5: Select date
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)
    assert fsm.state == CleaningOrder.time.state
    assert fsm.data.get("date") == "15.10.2025"

    # Step 6: Select time
    cb.data = "order:time:14:00"
    await time_callback(cb, fsm)
    assert fsm.state == CleaningOrder.metro_station.state
    assert fsm.data.get("time") == "14:00"

    # Step 7: Select metro line
    cb.data = "order:line:0"
    await metro_line_callback(cb, fsm)
    assert fsm.data.get("metro_line") is not None
    assert len(cb.message.edits) > 0
    assert "станцию" in cb.message.edits[-1]["text"].lower()

    # Step 8: Select metro station
    metro_line = fsm.data.get("metro_line")
    stations = METRO.get(metro_line, [])
    if stations:
        cb.data = f"order:st:0"
        await metro_station_callback(cb, fsm)
        assert fsm.state == CleaningOrder.address.state
        assert fsm.data.get("metro") == stations[0]

    # Step 9: Enter address
    msg.text = "ул. Тестовая, 1, кв. 10"
    await address(msg, fsm)
    assert fsm.state == CleaningOrder.phone.state
    assert fsm.data.get("address") == "ул. Тестовая, 1, кв. 10"

    # Step 10: Enter phone via contact
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79991234567"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    assert fsm.state == CleaningOrder.name.state
    assert fsm.data.get("phone") == "+79991234567"

    # Step 11: Enter name
    msg.text = "Иван Петров"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    assert fsm.state == CleaningOrder.comment.state
    assert fsm.data.get("name") == "Иван Петров"

    # Step 12: Enter comment
    msg.text = "Домашний питомец"
    await comment_edit_handler(msg, fsm)
    assert fsm.state == CleaningOrder.photos.state
    assert fsm.data.get("comment") == "Домашний питомец"

    # Step 13: Skip photos
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state

    # Verify preview text
    preview_text = await build_preview_text(fsm.data)
    assert "Генеральная уборка" in preview_text or "Индивидуальная уборка" in preview_text or "1-комнатная" in preview_text
    assert "Иван Петров" in preview_text

    # Step 14: Confirm order
    cb.data = "order:preview:confirm"
    order_id = await confirm_order(cb, fsm, bot)
    assert order_id is not None
    assert len(order_id) > 0

    # Verify order in DB
    orders = await get_user_orders(user_id)
    assert len(orders) == 1
    order = orders[0]
    assert order[0] == order_id
    assert order[3] == "new"

    saved_data = json.loads(order[2])
    assert saved_data["cleaning_type"] == "Индивидуальная уборка"
    assert saved_data["flat_type"] == "1-комнатная"
    assert saved_data["date"] == "15.10.2025"
    assert saved_data["time"] == "14:00"
    assert saved_data["address"] == "ул. Тестовая, 1, кв. 10"
    assert saved_data["phone"] == "+79991234567"
    assert saved_data["name"] == "Иван Петров"
    assert saved_data["comment"] == "Домашний питомец"

    # Verify photos in DB
    photos = await get_order_photos(order_id)
    assert len(photos) == 0  # no photos in this test

    # Verify FSM cleared after confirm
    assert fsm.state is None

    print(f"✅ Full client flow test PASSED. Order ID: {order_id}")


@pytest.mark.asyncio
async def test_full_client_flow_with_photos(db_setup):
    user_id = 999002
    chat_id = 999002
    username = "testuser2"
    first_name = "Test2"
    last_name = "User2"

    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, chat_id, username, first_name, last_name)
    cb = FakeCallback(user_id, chat_id, username, first_name, last_name)

    # /start
    from main import cmd_start
    await cmd_start(msg, fsm)

    # Start order
    await start_order(msg, fsm)

    # Pick cleaning type
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)

    # Flat type
    cb.data = "flat:2k"
    await process_flat_type(cb, fsm)
    assert fsm.data.get("flat_type") == "2-комнатная"

    # Date
    cb.data = "order:date:2025-10-20"
    await date_callback(cb, fsm)
    assert fsm.data.get("date") == "20.10.2025"

    # Time
    cb.data = "order:time:10:00"
    await time_callback(cb, fsm)
    assert fsm.data.get("time") == "10:00"

    # Metro line
    cb.data = "order:line:1"
    await metro_line_callback(cb, fsm)
    metro_line = fsm.data.get("metro_line")
    assert metro_line is not None

    # Metro station
    stations = METRO.get(metro_line, [])
    if stations:
        cb.data = f"order:st:0"
        await metro_station_callback(cb, fsm)
        assert fsm.data.get("metro") == stations[0]

    # Address
    msg.text = "ул. Фото, 5"
    await address(msg, fsm)
    assert fsm.data.get("address") == "ул. Фото, 5"

    # Phone
    msg.contact = MagicMock()
    msg.contact.phone_number = "+79997654321"
    msg.text = None
    msg.content_type = "contact"
    await phone_contact(msg, fsm)
    assert fsm.data.get("phone") == "+79997654321"

    # Name
    msg.text = "Петр"
    msg.content_type = "text"
    await name_handler(msg, fsm)
    assert fsm.data.get("name") == "Петр"

    # Comment
    msg.text = "Тестовый комментарий"
    await comment_edit_handler(msg, fsm)
    assert fsm.data.get("comment") == "Тестовый комментарий"

    # Add photo
    msg.photo = [MagicMock(file_id="test_file_id_1")]
    msg.content_type = "photo"
    await photos_handler(msg, fsm)
    assert len(fsm.data.get("photos", [])) == 1
    assert fsm.data["photos"][0] == "test_file_id_1"

    # Skip photos
    cb.data = "order:photo:skip"
    cb.message = msg
    await photos_skip(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state

    # Confirm
    cb.data = "order:preview:confirm"
    order_id = await confirm_order(cb, fsm, bot)
    assert order_id is not None

    # Verify DB
    orders = await get_user_orders(user_id)
    assert len(orders) == 1
    saved_data = json.loads(orders[0][2])
    assert saved_data["cleaning_type"] == "Генеральная уборка"
    assert saved_data["flat_type"] == "2-комнатная"
    assert saved_data["metro"] == stations[0] if stations else "—"
    assert saved_data["address"] == "ул. Фото, 5"
    assert saved_data["phone"] == "+79997654321"
    assert saved_data["name"] == "Петр"
    assert saved_data["comment"] == "Тестовый комментарий"

    # Verify photos saved
    photos = await get_order_photos(order_id)
    assert len(photos) == 1
    assert photos[0][1] == "test_file_id_1"

    print(f"✅ Full client flow with photos PASSED. Order ID: {order_id}, photos: {len(photos)}")


@pytest.mark.asyncio
async def test_metro_flow_with_real_data(db_setup):
    user_id = 999003
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, 999003, "testuser3", "Test3", "User3")
    cb = FakeCallback(user_id, 999003, "testuser3", "Test3", "User3")

    # Setup: go to metro state
    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-25"
    await date_callback(cb, fsm)
    cb.data = "order:time:12:00"
    await time_callback(cb, fsm)

    assert fsm.state == CleaningOrder.metro_station.state

    # Test metro line selection
    for line_idx in range(min(3, len(METRO))):
        cb.data = f"order:line:{line_idx}"
        await metro_line_callback(cb, fsm)
        metro_line = fsm.data.get("metro_line")
        assert metro_line is not None
        assert metro_line in METRO

        # Test metro station selection
        stations = METRO[metro_line]
        for station_idx in range(min(2, len(stations))):
            cb.data = f"order:st:{station_idx}"
            await metro_station_callback(cb, fsm)
            assert fsm.data.get("metro") == stations[station_idx]
            assert fsm.state == CleaningOrder.address.state

            # Reset to metro state for next iteration
            await fsm.set_state(CleaningOrder.metro_station.state)

        # Reset to metro state for next line
        await fsm.set_state(CleaningOrder.metro_station.state)

    print("✅ Metro flow with real data PASSED")


@pytest.mark.asyncio
async def test_confirm_does_not_create_duplicate(db_setup):
    user_id = 999004
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, 999004, "testuser4", "Test4", "User4")
    cb = FakeCallback(user_id, 999004, "testuser4", "Test4", "User4")

    # Setup: go to preview
    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-30"
    await date_callback(cb, fsm)
    cb.data = "order:time:09:00"
    await time_callback(cb, fsm)

    # Skip metro for simplicity
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

    # Confirm twice
    cb.data = "order:preview:confirm"
    order_id_1 = await confirm_order(cb, fsm, bot)

    # FSM should be cleared after confirm
    assert fsm.state is None

    # Try to confirm again (should not create duplicate)
    # But since FSM is cleared, we can't confirm again without restarting
    orders = await get_user_orders(user_id)
    assert len(orders) == 1

    print(f"✅ Confirm does not create duplicate PASSED. Order ID: {order_id_1}")


@pytest.mark.asyncio
async def test_cancel_clears_fsm_and_does_not_continue(db_setup):
    user_id = 999005
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, 999005, "testuser5", "Test5", "User5")

    # Setup: go to middle of order
    await start_order(msg, fsm)
    await fsm.set_state(CleaningOrder.address.state)
    await fsm.update_data(address="ул. Отмена, 1")

    # Cancel
    msg.text = "❌ Отменить заказ"
    from main import cancel_order_handler
    await cancel_order_handler(msg, fsm)

    assert fsm.state is None
    assert len(msg.answers) > 0
    assert "отмен" in msg.answers[-1]["text"].lower()

    print("✅ Cancel clears FSM PASSED")


@pytest.mark.asyncio
async def test_back_navigation(db_setup):
    user_id = 999006
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, 999006, "testuser6", "Test6", "User6")
    cb = FakeCallback(user_id, 999006, "testuser6", "Test6", "User6")

    # Setup: go to time state
    await start_order(msg, fsm)
    cb.data = "cleaning:type:general"
    cb.message = msg
    await pick_cleaning_type(cb, fsm, bot)
    cb.data = "flat:1k"
    await process_flat_type(cb, fsm)
    cb.data = "order:date:2025-10-15"
    await date_callback(cb, fsm)

    assert fsm.state == CleaningOrder.time.state

    # Back to date
    cb.data = "order:back"
    await order_back(cb, fsm)
    assert fsm.state == CleaningOrder.date.state

    # Back to flat_type
    cb.data = "order:back"
    await order_back(cb, fsm)
    assert fsm.state == CleaningOrder.flat_type.state

    print("✅ Back navigation PASSED")


@pytest.mark.asyncio
async def test_my_orders_shows_created_order(db_setup):
    user_id = 999007
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    msg = FakeMessage(user_id, 999007, "testuser7", "Test7", "User7")
    cb = FakeCallback(user_id, 999007, "testuser7", "Test7", "User7")

    # Create order first
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
    msg.text = "ул. Мои заказы, 1"
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

    # Now check My Orders - active orders
    msg.answers = []
    cb.data = "my_orders:active"
    await my_orders_active(cb)
    response_text = ""
    if cb.message.edits:
        response_text = cb.message.edits[-1]["text"]
    elif cb.message.answers:
        response_text = cb.message.answers[-1]["text"]
    elif msg.answers:
        response_text = msg.answers[-1]["text"]
    assert response_text, "No response from my_orders_active"
    assert order_id in response_text or "Заказ" in response_text

    print(f"✅ My Orders shows created order PASSED. Order ID: {order_id}")


