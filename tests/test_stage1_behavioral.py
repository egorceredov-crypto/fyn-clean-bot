import asyncio
import json
import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + os.sep + "..")

import pytest
from aiogram import Bot
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, Contact, FSInputFile

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
    my_orders_history,
    my_orders_cancel,
    my_orders_repeat,
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
    rating_callback,
    rating_comment_handler,
    executor_update_status,
    executor_request_photo,
    executor_upload_photo,
    executor_delete_result_photo,
    admin_orders_list,
    admin_order_detail,
    admin_status,
    admin_assign_executor,
    admin_show_assign_executor,
    admin_stats,
    services_info,
    individual_cleaning_info,
    my_orders_detail,
    my_orders_view_client_photos,
    my_orders_view_result_photos,
    admin_unassign_executor,
    admin_view_client_photos,
    admin_view_result_photos,
    executor_view_client_photos,
    executor_view_result_photos,
    executor_orders_list,
    _render_executor_order,
    send_order_to_admin,
    notify_client_status,
    notify_executor_status,
    notify_admin_rating,
    get_order_for_mutation,
    get_owned_order,
    get_assigned_executor_order,
    transition_status,
    can_transition_status,
    assign_executor,
    unassign_executor,
    get_active_executors,
    get_order_photos,
    save_order_photo,
    get_user_orders,
    get_order,
    get_status,
    get_executor,
    add_executor,
    toggle_executor_active,
    get_executor_avg_rating,
    get_executor_ratings,
    get_order_rating,
    save_rating,
    get_user_profile,
    upsert_user,
    delete_abandoned_order,
    get_abandoned_orders,
    update_abandoned_order,
    check_abandoned_orders,
    send_reminders,
    is_time_in_range,
    is_valid_phone,
    normalize_phone,
    same_telegram_id,
    is_order_modifiable,
    html_str,
    SERVICES_TEXT,
    INDIVIDUAL_CLEANING_TEXT,
    INDIVIDUAL_CLEANING_WHAT_INCLUDED,
    ADMIN_STATS_TEXT,
    EXECUTOR_ORDER_TEMPLATE,
    EXECUTOR_WELCOME,
    MY_ORDERS_EMPTY,
    ORDER_CREATED_TEXT,
    ORDER_CANCELLED_TEXT,
    ORDER_COMPLETED_TEXT,
    RATING_THANKS,
    LTV_AFTER_ORDER,
    LTV_REMINDER,
)
from db import (
    init_db,
    create_order,
    get_user_orders,
    get_order_photos,
    save_order_photo,
    delete_abandoned_order,
    get_abandoned_orders,
    DB_PATH,
)
from states import CleaningOrder, ExecutorStates, RatingStates, AdminStates
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
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(init_db())
    finally:
        loop.close()
    yield
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)


@pytest.mark.asyncio
async def test_pick_cleaning_type_sends_caption_with_price_and_desc(db_setup):
    user_id = 999600
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    cb = FakeCallback(user_id, user_id, "testuser600", "Test600", "User600")

    cb.data = "cleaning:type:regular"
    await pick_cleaning_type(cb, fsm, bot)
    assert bot.send_photo.call_args_list
    call = bot.send_photo.call_args_list[-1]
    caption = call.kwargs.get("caption", "")
    assert "Поддерживающая уборка" in caption
    assert "10 500" in caption or "13 500" in caption

    cb.data = "cleaning:type:individual"
    bot.reset_mock()
    await pick_cleaning_type(cb, fsm, bot)
    assert bot.send_photo.call_args_list
    call = bot.send_photo.call_args_list[-1]
    caption = call.kwargs.get("caption", "")
    assert "Индивидуальная уборка" in caption
    assert "По согласованию" in caption or "что входит" in caption.lower()


@pytest.mark.asyncio
async def test_photos_multiple_and_duplicates(db_setup):
    user_id = 999601
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser601", "Test601", "User601")

    await start_order(msg, fsm)
    await fsm.set_state(CleaningOrder.photos.state)
    await fsm.update_data(photos=[])

    msg.photo = [MagicMock(file_id="file_1")]
    await photos_handler(msg, fsm)
    assert len(fsm.data.get("photos", [])) == 1

    msg.photo = [MagicMock(file_id="file_1")]
    await photos_handler(msg, fsm)
    assert len(fsm.data.get("photos", [])) == 1
    assert any("уже добавлено" in ans["text"].lower() for ans in msg.answers)

    for i in range(2, 11):
        msg.photo = [MagicMock(file_id=f"file_{i}")]
        await photos_handler(msg, fsm)
        assert len(fsm.data.get("photos", [])) == i

    msg.photo = [MagicMock(file_id="file_11")]
    await photos_handler(msg, fsm)
    assert len(fsm.data.get("photos", [])) == 10
    assert any("не более 10" in ans["text"].lower() for ans in msg.answers)


@pytest.mark.asyncio
async def test_preview_edit_all_fields(db_setup):
    user_id = 999602
    fsm = FakeFSMContext()
    msg = FakeMessage(user_id, user_id, "testuser602", "Test602", "User602")
    cb = FakeCallback(user_id, user_id, "testuser602", "Test602", "User602")
    bot = AsyncMock(spec=Bot)

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
    cb.data = "order:line:0"
    await metro_line_callback(cb, fsm)
    cb.data = "order:st:0"
    await metro_station_callback(cb, fsm)
    msg.text = "ул. Тест, 1"
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

    cb.data = "order:edit:flat_type"
    await edit_field_select(cb, fsm)
    assert fsm.state == CleaningOrder.flat_type.state
    cb.data = "flat:2k"
    await process_flat_type(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state
    assert fsm.data.get("flat_type") == "2-комнатная"

    cb.data = "order:edit:date"
    await edit_field_select(cb, fsm)
    assert fsm.state == CleaningOrder.date.state
    cb.data = "order:date:2025-10-20"
    await date_callback(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state
    assert fsm.data.get("date") == "20.10.2025"

    cb.data = "order:edit:comment"
    await edit_field_select(cb, fsm)
    assert fsm.state == CleaningOrder.comment.state
    msg.text = "Новый комментарий"
    msg.content_type = "text"
    await comment_edit_handler(msg, fsm)
    assert fsm.state == CleaningOrder.preview.state
    assert fsm.data.get("comment") == "Новый комментарий"


@pytest.mark.asyncio
async def test_confirm_order_notifies_admin(db_setup):
    user_id = 999603
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    cb = FakeCallback(user_id, user_id, "testuser603", "Test603", "User603")
    msg = FakeMessage(user_id, user_id, "testuser603", "Test603", "User603")

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
    cb.data = "order:line:0"
    await metro_line_callback(cb, fsm)
    cb.data = "order:st:0"
    await metro_station_callback(cb, fsm)
    msg.text = "ул. Тест, 1"
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
    with patch('main.cfg') as mock_cfg:
        mock_cfg.admin_chat_id = 777777
        order_id = await confirm_order(cb, fsm, bot)

    admin_calls = [call for call in bot.send_message.call_args_list if call.args and call.args[0] == 777777]
    assert admin_calls, "admin notification not sent"
    admin_text = admin_calls[-1].args[1]
    assert order_id in admin_text


@pytest.mark.asyncio
async def test_preview_cancel_deletes_abandoned_order(db_setup):
    user_id = 999604
    fsm = FakeFSMContext()
    cb = FakeCallback(user_id, user_id, "testuser604", "Test604", "User604")
    msg = FakeMessage(user_id, user_id, "testuser604", "Test604", "User604")

    await start_order(msg, fsm)
    await fsm.set_state(CleaningOrder.preview.state)
    await fsm.update_data(some_data=True)

    async with db_module.aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO abandoned_orders (telegram_id, step) VALUES (?, ?)", (user_id, "пропустил фото"))
        await db.commit()

    cb.data = "order:cancel"
    await preview_cancel(cb, fsm)

    async with db_module.aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM abandoned_orders WHERE telegram_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            assert row is None


@pytest.mark.asyncio
async def test_my_orders_cancel_changes_status(db_setup):
    owner_id = 999605
    order_id = "cancel_test_order"
    data = {"telegram_id": owner_id, "cleaning_type": "Поддерживающая уборка", "flat_type": "1-комнатная", "address": "ул. Cancel, 1"}
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="21.10.2026",
        time="12:00",
    )
    cb = FakeCallback(owner_id, owner_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"my_orders:cancel:{order_id}"
    await main.my_orders_cancel(cb, FakeFSMContext())
    assert await db_module.get_status(order_id) == "cancelled"


@pytest.mark.asyncio
async def test_admin_status_changes_order_status(db_setup):
    admin_id = 999606
    order_id = "status_test_order"
    data = {"telegram_id": 9996060, "cleaning_type": "Поддерживающая уборка", "flat_type": "1-комнатная", "address": "ул. Status, 1"}
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="22.10.2026",
        time="13:00",
    )
    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:status:{order_id}:accepted"
    with patch('main.cfg') as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_status(cb, FakeFSMContext())
    assert await db_module.get_status(order_id) == "accepted"


@pytest.mark.asyncio
async def test_my_orders_history_shows_completed_and_cancelled(db_setup):
    user_id = 999607
    order_id1 = "history_completed"
    data1 = {"telegram_id": user_id, "cleaning_type": "Поддерживающая уборка", "flat_type": "1-комнатная", "address": "ул. Hist, 1"}
    await db_module.create_order(
        order_id1,
        "cleaning",
        json.dumps(data1, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="23.10.2026",
        time="14:00",
    )
    order_id2 = "history_cancelled"
    data2 = {"telegram_id": user_id, "cleaning_type": "Генеральная уборка", "flat_type": "2-комнатная", "address": "ул. Hist, 2"}
    await db_module.create_order(
        order_id2,
        "cleaning",
        json.dumps(data2, ensure_ascii=False),
        datetime.now().isoformat(),
        status="cancelled",
        date="24.10.2026",
        time="15:00",
    )
    order_id3 = "history_active"
    data3 = {"telegram_id": user_id, "cleaning_type": "После ремонта", "flat_type": "3-комнатная", "address": "ул. Hist, 3"}
    await db_module.create_order(
        order_id3,
        "cleaning",
        json.dumps(data3, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="25.10.2026",
        time="16:00",
    )

    cb = FakeCallback(user_id, user_id)
    cb.data = "my_orders:history"
    await main.my_orders_history(cb)
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert "История заказов" in response
    assert order_id1 in response
    assert order_id2 in response
    assert order_id3 not in response


@pytest.mark.asyncio
async def test_my_orders_repeat_creates_new_order(db_setup):
    user_id = 999608
    order_id = "repeat_base"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "metro": "Киевская",
        "address": "ул. Repeat, 1",
        "phone": "+79991234567",
        "name": "Иван",
        "comment": "Тест",
        "photos": ["file_1"],
        "price": "10 500–13 500 ₽",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="26.10.2026",
        time="17:00",
    )
    cb = FakeCallback(user_id, user_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"my_orders:repeat:{order_id}"
    fsm = FakeFSMContext()
    await main.my_orders_repeat(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state
    assert fsm.data.get("photos") == []
    assert fsm.data.get("executor_id") is None
    cb.data = "repeat:confirm"
    new_order_id = await main.confirm_order(cb, fsm, cb.bot)
    orders = await get_user_orders(user_id)
    assert len(orders) == 2
    assert new_order_id != order_id


@pytest.mark.asyncio
async def test_rating_flow_saves_rating_and_notifies_admin(db_setup):
    client_id = 999609
    executor_id = 799609
    order_id = "rating_order"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Rating, 1",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="27.10.2026",
        time="18:00",
    )
    await db_module.add_executor(
        executor_id,
        "Rating Exec",
        "+79990000909",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(client_id, client_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"rating:{order_id}:5"
    state = FakeFSMContext()
    await main.rating_callback(cb, state)
    assert state.state == RatingStates.comment.state

    msg = FakeMessage(client_id, client_id)
    msg.text = "Отлично"
    await main.rating_comment_handler(msg, state, cb.bot)
    rating = await db_module.get_order_rating(order_id)
    assert rating is not None
    assert rating[3] == 5
    assert rating[4] == "Отлично"
    assert cb.bot.send_message.called


@pytest.mark.asyncio
async def test_executor_avg_rating(db_setup):
    executor_id = 799610
    client_id = 111
    order_id1 = "rating_exec_1"
    order_id2 = "rating_exec_2"
    data1 = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Avg, 1",
        "executor_id": executor_id,
    }
    data2 = {
        "telegram_id": client_id,
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "address": "ул. Avg, 2",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id1,
        "cleaning",
        json.dumps(data1, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="28.10.2026",
        time="10:00",
        executor_id=executor_id,
    )
    await db_module.create_order(
        order_id2,
        "cleaning",
        json.dumps(data2, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="29.10.2026",
        time="11:00",
        executor_id=executor_id,
    )
    await db_module.save_rating(order_id1, client_id, executor_id, 5, "Отлично")
    await db_module.save_rating(order_id2, client_id, executor_id, 4, "Хорошо")
    avg = await db_module.get_executor_avg_rating(executor_id)
    assert avg["avg"] == 4.5
    assert avg["count"] == 2


@pytest.mark.asyncio
async def test_admin_assign_executor(db_setup):
    admin_id = 999612
    executor_id = 799612
    order_id = "assign_order"
    data = {
        "telegram_id": 9996120,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Assign, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="30.10.2026",
        time="12:00",
    )
    await db_module.add_executor(
        executor_id,
        "Assign Exec",
        "+79990001212",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:assign_executor:{order_id}:{executor_id}"
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_assign_executor(cb)

    order = await db_module.get_order(order_id)
    assert order["executor_id"] == executor_id
    assert order["status"] == "assigned"


@pytest.mark.asyncio
async def test_admin_show_assign_executor_lists_only_active(db_setup):
    admin_id = 999613
    active_id = 799613
    inactive_id = 799614
    order_id = "assign_list_order"
    data = {
        "telegram_id": 9996130,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. AssignList, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="31.10.2026",
        time="13:00",
    )
    await db_module.add_executor(active_id, "Active", "+79990001313", is_active=True, update_active=True)
    await db_module.add_executor(inactive_id, "Inactive", "+79990001414", is_active=False, update_active=True)

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:assign_executor:{order_id}"
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_show_assign_executor(cb)

    reply_markup = cb.message.edits[-1]["reply_markup"] if cb.message.edits else cb.message.answers[-1]["reply_markup"]
    button_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert "Active" in button_texts
    assert "Inactive" not in button_texts


@pytest.mark.asyncio
async def test_executor_upload_and_delete_result_photo(db_setup):
    executor_id = 799615
    client_id = 999615
    order_id = "executor_photo_order"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. ExecPhoto, 1",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="01.11.2026",
        time="14:00",
    )
    await db_module.add_executor(
        executor_id,
        "Photo Exec",
        "+79990001515",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(executor_id, executor_id)
    cb.data = f"exec:request_photo:{order_id}"
    fsm = FakeFSMContext()
    await main.executor_request_photo(cb, fsm)
    assert cb.message.edits or cb.message.answers

    msg = FakeMessage(executor_id, executor_id)
    msg.photo = [MagicMock(file_id="result_1")]
    msg.content_type = "photo"
    bot = AsyncMock(spec=Bot)
    await main.executor_upload_photo(msg, fsm, bot)
    photos = await get_order_photos(order_id)
    result_photos = [p for p in photos if p[2] == "result"]
    assert len(result_photos) == 1

    cb.data = f"exec:photo:delete:{order_id}:{result_photos[0][0]}"
    await main.executor_delete_result_photo(cb)
    photos = await get_order_photos(order_id)
    result_photos = [p for p in photos if p[2] == "result"]
    assert len(result_photos) == 0


@pytest.mark.asyncio
async def test_executor_complete_order(db_setup):
    executor_id = 799616
    client_id = 999616
    order_id = "complete_order"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Complete, 1",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="02.11.2026",
        time="15:00",
    )
    await db_module.add_executor(
        executor_id,
        "Complete Exec",
        "+79990001616",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(executor_id, executor_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"exec:status:{order_id}:completed"
    state = FakeFSMContext()
    await state.set_state(ExecutorStates.menu.state)
    await main.executor_update_status(cb, state)
    assert await db_module.get_status(order_id) == "completed"


@pytest.mark.asyncio
async def test_admin_order_flow(db_setup):
    admin_id = 999617
    order_id = "admin_flow_order"
    data = {
        "telegram_id": 9996170,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Admin, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="03.11.2026",
        time="16:00",
    )

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        cb.data = "admin:orders:new:0"
        await main.admin_orders_list(cb)
        cb.data = f"admin:order:{order_id}"
        await main.admin_order_detail(cb)
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert order_id in response
    assert "Поддерживающая уборка" in response


@pytest.mark.asyncio
async def test_admin_stats(db_setup):
    admin_id = 999618
    order_id = "stats_order"
    data = {
        "telegram_id": 9996180,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Stats, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="04.11.2026",
        time="17:00",
    )
    await db_module.add_executor(799618, "Stats Exec", "+79990001818", is_active=True, update_active=True)
    await db_module.save_rating("stats_order", 9996180, 799618, 5, "Супер")

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        cb.data = "admin:stats"
        await main.admin_stats(cb)
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert "Статистика FYN Clean" in response
    assert "1" in response


@pytest.mark.asyncio
async def test_services_info_text():
    msg = FakeMessage(999619, 999619)
    await main.services_info(msg)
    response = msg.answers[-1]["text"]
    assert "Поддерживающая уборка" in response
    assert "10 500" in response
    assert "Индивидуальная уборка" in response


@pytest.mark.asyncio
async def test_confirm_order_saves_price_in_db(db_setup):
    user_id = 999620
    fsm = FakeFSMContext()
    bot = AsyncMock(spec=Bot)
    cb = FakeCallback(user_id, user_id, "testuser620", "Test620", "User620")
    await fsm.set_state(CleaningOrder.preview.state)
    await fsm.update_data(
        cleaning_type="Поддерживающая уборка",
        flat_type="1-комнатная",
        date="15.10.2025",
        time="14:00",
        metro="Киевская",
        address="ул. Price, 1",
        phone="+79991234567",
        name="Иван",
        comment="Тест",
        photos=[],
    )
    cb.data = "order:preview:confirm"
    order_id = await main.confirm_order(cb, fsm, bot)
    order = await db_module.get_order(order_id)
    saved_data = order["data"]
    assert "price" in saved_data
    assert saved_data["price"] == "10 500–13 500 ₽"


@pytest.mark.asyncio
async def test_individual_cleaning_info_sends_text_and_photo():
    msg = FakeMessage(999621, 999621)
    await main.individual_cleaning_info(msg)
    assert msg.answer_photo_calls
    call = msg.answer_photo_calls[-1]
    assert call["caption"] is not None
    assert "Окна" in call["caption"]
    assert "Кухня" in call["caption"]


@pytest.mark.asyncio
async def test_backend_access_guards_reject_other_users_orders(db_setup):
    owner_id = 999622
    other_id = 999623
    order_id = "access_guard_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Access, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="05.11.2026",
        time="18:00",
    )
    assert await get_owned_order(order_id, other_id) is None
    assert await get_assigned_executor_order(order_id, other_id) is None


@pytest.mark.asyncio
async def test_order_persistence_all_fields(db_setup):
    user_id = 999624
    order_id = "persist_order"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "metro": "Киевская",
        "address": "ул. Persist, 1",
        "date": "20.10.2025",
        "time": "15:00",
        "phone": "+79991234567",
        "name": "Иван",
        "comment": "Комментарий",
        "photos": ["file_1"],
        "username": "@test",
        "executor_id": 799624,
        "price": "13 500–16 500 ₽",
        "rooms": "2",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="assigned",
        date="20.10.2025",
        time="15:00",
        executor_id=799624,
    )
    order = await db_module.get_order(order_id)
    saved = order["data"]
    assert saved["cleaning_type"] == "Генеральная уборка"
    assert saved["flat_type"] == "2-комнатная"
    assert saved["metro"] == "Киевская"
    assert saved["address"] == "ул. Persist, 1"
    assert saved["date"] == "20.10.2025"
    assert saved["time"] == "15:00"
    assert saved["phone"] == "+79991234567"
    assert saved["name"] == "Иван"
    assert saved["comment"] == "Комментарий"
    assert saved["photos"] == ["file_1"]
    assert saved["price"] == "13 500–16 500 ₽"
    assert order["status"] == "assigned"
    assert order["executor_id"] == 799624


@pytest.mark.asyncio
async def test_fsm_cleared_after_repeat_order(db_setup):
    user_id = 999625
    order_id = "repeat_fsm_order"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. RepeatFSM, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="06.11.2026",
        time="19:00",
    )

    cb = FakeCallback(user_id, user_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"my_orders:repeat:{order_id}"
    fsm = FakeFSMContext()
    await main.my_orders_repeat(cb, fsm)
    assert fsm.state == CleaningOrder.preview.state


@pytest.mark.asyncio
async def test_negative_cancel_completed_order_is_blocked(db_setup):
    user_id = 999626
    order_id = "negative_cancel"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Neg, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="07.11.2026",
        time="20:00",
    )

    cb = FakeCallback(user_id, user_id)
    cb.data = f"my_orders:cancel:{order_id}"
    await main.my_orders_cancel(cb, FakeFSMContext())
    assert "уже завершён" in cb.answer_alerts[0].lower() or "отменён" in cb.answer_alerts[0].lower()
    assert await db_module.get_status(order_id) == "completed"


@pytest.mark.asyncio
async def test_negative_duplicate_rating_blocked(db_setup):
    client_id = 999627
    executor_id = 799627
    order_id = "dup_rating"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. DupRating, 1",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="08.11.2026",
        time="21:00",
    )
    await db_module.save_rating(order_id, client_id, executor_id, 5, "Отлично")

    cb = FakeCallback(client_id, client_id)
    cb.data = f"rating:{order_id}:4"
    await main.rating_callback(cb, FakeFSMContext())
    assert "уже оценили" in cb.answer_alerts[0].lower()


@pytest.mark.asyncio
async def test_inactive_executor_not_in_assign_list(db_setup):
    admin_id = 999628
    active_executor_id = 799628
    inactive_executor_id = 799629
    order_id = "inactive_exec_order"
    data = {
        "telegram_id": 9996280,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. Inactive, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="new",
        date="09.11.2026",
        time="09:00",
    )
    await db_module.add_executor(active_executor_id, "Active", "+79990002828", is_active=True, update_active=True)
    await db_module.add_executor(inactive_executor_id, "Inactive", "+79990002929", is_active=False, update_active=True)

    cb = FakeCallback(admin_id, admin_id)
    cb.data = f"admin:assign_executor:{order_id}"
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_show_assign_executor(cb)

    reply_markup = cb.message.edits[-1]["reply_markup"] if cb.message.edits else cb.message.answers[-1]["reply_markup"]
    button_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert "Active" in button_texts
    assert "Inactive" not in button_texts


@pytest.mark.asyncio
async def test_admin_cancel_order(db_setup):
    admin_id = 999629
    order_id = "admin_cancel_order"
    data = {
        "telegram_id": 9996290,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. AdminCancel, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="accepted",
        date="10.11.2026",
        time="10:00",
    )

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:status:{order_id}:cancelled"
    state = FakeFSMContext()
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_status(cb, state)
    assert state.state == AdminStates.cancel_reason.state


@pytest.mark.asyncio
async def test_repeat_cancel_after_complete_is_blocked(db_setup):
    user_id = 999630
    order_id = "repeat_cancel_blocked"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. RepeatCancel, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="11.11.2026",
        time="11:00",
    )

    cb = FakeCallback(user_id, user_id)
    cb.data = f"my_orders:cancel:{order_id}"
    await main.my_orders_cancel(cb, FakeFSMContext())
    assert "уже завершён" in cb.answer_alerts[0].lower() or "отменён" in cb.answer_alerts[0].lower()
    assert await db_module.get_status(order_id) == "completed"


@pytest.mark.asyncio
async def test_client_can_view_result_photos(db_setup):
    owner_id = 999631
    executor_id = 799631
    order_id = "client_view_result_order"
    data = {
        "telegram_id": owner_id,
        "cleaning_type": "Генеральная уборка",
        "flat_type": "2-комнатная",
        "address": "ул. Result, 2",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="12.11.2026",
        time="12:00",
    )
    await db_module.add_executor(
        executor_id,
        "Result Exec",
        "+79990001313",
        is_active=True,
        update_active=True,
    )
    await save_order_photo(order_id, executor_id, "result_file_1", "result")

    cb = FakeCallback(owner_id, owner_id)
    cb.data = f"my_orders:photos:result:{order_id}"
    await main.my_orders_view_result_photos(cb)

    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert "Фотографии результата" in response
    assert cb.message.answer_media_group_calls
    sent_media = cb.message.answer_media_group_calls[-1]["media"]
    assert len(sent_media) == 1


@pytest.mark.asyncio
async def test_admin_view_result_photos(db_setup):
    admin_id = 999632
    executor_id = 799632
    order_id = "admin_view_result_order"
    data = {
        "telegram_id": 9996320,
        "cleaning_type": "После ремонта",
        "flat_type": "3-комнатная",
        "address": "ул. AdminResult, 3",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="13.11.2026",
        time="13:00",
    )
    await db_module.add_executor(
        executor_id,
        "AdminResult Exec",
        "+79990001313",
        is_active=True,
        update_active=True,
    )
    await save_order_photo(order_id, executor_id, "result_file_admin", "result")

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:photos:result:{order_id}"
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_view_result_photos(cb)

    assert cb.message.edits or cb.message.answers
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert "Фотографии результата" in response
    reply_markup = cb.message.edits[-1]["reply_markup"] if cb.message.edits else cb.message.answers[-1]["reply_markup"]
    button_texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert any("Удалить фото 1" in t for t in button_texts)


@pytest.mark.asyncio
async def test_bold_text_used_in_key_messages():
    assert "<b>" in SERVICES_TEXT
    assert "<b>" in INDIVIDUAL_CLEANING_TEXT
    assert "<b>" in ADMIN_STATS_TEXT
    assert "<b>" in EXECUTOR_ORDER_TEMPLATE
    assert "<b>" in ORDER_PREVIEW_TEXT


@pytest.mark.asyncio
async def test_ltv_messages_exist():
    assert "скидку" in LTV_AFTER_ORDER.lower() or "привилегии" in LTV_AFTER_ORDER.lower()
    assert "скидку" in LTV_REMINDER.lower() or "снова" in LTV_REMINDER.lower()


@pytest.mark.asyncio
async def test_ltv_after_order_sent_on_completed(db_setup):
    client_id = 999635
    order_id = "ltv_order"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. LTV, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="completed",
        date="16.11.2026",
        time="16:00",
    )
    bot = AsyncMock(spec=Bot)
    await notify_client_status(bot, order_id, data, "completed")
    texts = [call.args[1] for call in bot.send_message.call_args_list if call.args and call.args[0] == client_id]
    assert any(LTV_AFTER_ORDER in text for text in texts)


@pytest.mark.asyncio
async def test_ltv_reminder_sent_by_scheduler(db_setup):
    user_id = 999636
    await db_module.upsert_user(
        user_id=user_id,
        username="testuser636",
        full_name="Test636 User636",
    )
    order_id = "ltv_reminder_order"
    data = {
        "telegram_id": user_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. LTV Reminder, 1",
    }
    old_time = datetime.now().timestamp() - 31 * 24 * 60 * 60
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.fromtimestamp(old_time).isoformat(),
        status="completed",
        date="16.11.2026",
        time="16:00",
    )
    bot = AsyncMock(spec=Bot)
    await send_reminders(bot)
    assert bot.send_message.called
    sent_text = bot.send_message.call_args_list[-1].args[1]
    assert "скидку" in sent_text.lower() or "снова" in sent_text.lower() or "уборку" in sent_text.lower()


@pytest.mark.asyncio
async def test_admin_cancel_order_two_step(db_setup):
    admin_id = 999633
    order_id = "admin_cancel_two_step"
    data = {
        "telegram_id": 9996330,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. AdminCancel2, 1",
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="accepted",
        date="14.11.2026",
        time="14:00",
    )

    cb = FakeCallback(admin_id, admin_id)
    cb.bot = AsyncMock(spec=Bot)
    cb.data = f"admin:status:{order_id}:cancelled"
    state = FakeFSMContext()
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_status(cb, state)
    assert state.state == AdminStates.cancel_reason.state
    await state.update_data(admin_cancel_order_id=order_id)

    msg = FakeMessage(admin_id, admin_id)
    msg.text = "Клиент передумал"
    with patch("main.cfg") as mock_cfg:
        mock_cfg.admin_chat_id = admin_id
        await main.admin_cancel_reason_handler(msg, state, cb.bot)
    status = await db_module.get_status(order_id)
    print("CANCEL_STATUS", status)
    assert status == "cancelled"


@pytest.mark.asyncio
async def test_executor_order_flow(db_setup):
    executor_id = 799634
    client_id = 999634
    order_id = "executor_flow_order"
    data = {
        "telegram_id": client_id,
        "cleaning_type": "Поддерживающая уборка",
        "flat_type": "1-комнатная",
        "address": "ул. ExecFlow, 1",
        "executor_id": executor_id,
    }
    await db_module.create_order(
        order_id,
        "cleaning",
        json.dumps(data, ensure_ascii=False),
        datetime.now().isoformat(),
        status="in_work",
        date="15.11.2026",
        time="15:00",
    )
    await db_module.add_executor(
        executor_id,
        "Flow Exec",
        "+79990001313",
        is_active=True,
        update_active=True,
    )

    cb = FakeCallback(executor_id, executor_id)
    cb.bot = AsyncMock(spec=Bot)
    msg = FakeMessage(executor_id, executor_id)

    await main.executor_start(msg, FakeFSMContext())
    assert "Добро пожаловать" in msg.answers[-1]["text"]

    cb.data = "exec:orders"
    await main.executor_orders_list(msg, FakeFSMContext())
    response = msg.answers[-1]["text"]
    assert order_id in response

    cb.data = f"exec:order:{order_id}"
    await main._render_executor_order(cb, await get_order(order_id))
    response = cb.message.edits[-1]["text"] if cb.message.edits else cb.message.answers[-1]["text"]
    assert order_id in response

    cb.data = f"exec:request_photo:{order_id}"
    fsm = FakeFSMContext()
    await main.executor_request_photo(cb, fsm)
    assert cb.message.edits or cb.message.answers

    msg.photo = [MagicMock(file_id="flow_result")]
    msg.content_type = "photo"
    await main.executor_upload_photo(msg, fsm, cb.bot)
    photos = await get_order_photos(order_id)
    assert len([p for p in photos if p[2] == "result"]) == 1

    cb.data = f"exec:status:{order_id}:in_work"
    state = FakeFSMContext()
    await state.set_state(ExecutorStates.menu.state)
    await main.executor_update_status(cb, state)
    assert await db_module.get_status(order_id) == "in_work"

    cb.data = f"exec:status:{order_id}:completed"
    await main.executor_update_status(cb, state)
    assert await db_module.get_status(order_id) == "completed"

    user_id = 999611
    await db_module.upsert_user(user_id, "@testuser", "Test User", name="Иван")
    profile = await db_module.get_user_profile(user_id)
    assert profile["name"] == "Иван"
    await db_module.upsert_user(user_id, "@testuser", "Test User", name="Петр")
    profile = await db_module.get_user_profile(user_id)
    assert profile["name"] == "Петр"
    await db_module.upsert_user(user_id, "@testuser", "Test User", name="")
    profile = await db_module.get_user_profile(user_id)
    assert profile["name"] == "Петр"
