import sys
import os
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

import json
import uuid
import asyncio
import random
import string
import logging
from datetime import datetime, time, timedelta
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, Contact, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import load_config
from texts import (
    WELCOME, CONTACT, FAQ_SHORT, OUT_OF_HOURS, PRICE_CLEANING, PRICE_DRY,
    ABOUT_TEXT, REVIEWS_TEXT, CONTACT_TEXT, SUPPORT1, INDIVIDUAL_CLEANING_TEXT,
    STATUS_LABELS, ORDER_PREVIEW_TEXT, ORDER_CREATED_TEXT, ORDER_CANCELLED_TEXT,
    RATING_THANKS, MY_ORDERS_EMPTY, EXECUTOR_WELCOME, ADMIN_STATS_TEXT,
    EXECUTOR_ORDER_TEMPLATE, LTV_AFTER_ORDER, LTV_REMINDER, SERVICES_TEXT
)
from keyboards import (
    cleaning_type_kb, flat_type_kb, name_kb, main_menu, metro_lines_kb, metro_stations_kb,
    confirm_kb, admin_status_kb, order_cancel_kb, order_step_kb, date_selection_kb,
    time_selection_kb, phone_request_kb, photo_kb, preview_kb, edit_field_kb,
    my_orders_kb, order_detail_kb, executor_order_actions_kb, rating_kb,
    individual_cleaning_kb, admin_orders_filter_kb, executor_assign_kb,
    executor_menu_kb
)
from states import CleaningOrder, ExecutorStates, RatingStates
from metro_data import METRO
from db import (
    init_db, create_order, update_status, get_status, upsert_user,
    get_user_profile, inc_cleaning_orders, get_users_for_reminder,
    mark_reminder_sent, get_or_create_referral_code, save_abandoned_order,
    delete_abandoned_order, get_abandoned_orders, mark_abandoned_reminded,
    add_executor, get_all_executors, get_executor, toggle_executor_active,
    assign_executor, save_rating, get_order_rating, get_executor_avg_rating,
    get_executor_ratings, save_order_photo, get_order_photos, get_orders_by_status,
    get_orders_by_executor, get_test_reminder_users, get_user_orders
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.types import FSInputFile, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

telethon_client = None


def reminder_kb():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✨ Заказать уборку",
            callback_data="reminder:order"
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="❌ Отказаться",
            callback_data="reminder:cancel"
        )
    )
    return builder.as_markup()


async def send_reminders(bot: Bot):

    print("🔔 SCHEDULER: задача запущена!")

    users = await get_test_reminder_users(30)

    print(f"🔔 Найдено клиентов: {len(users)}")
    print(f"🔔 Клиенты: {users}")

    for user in users:

        telegram_id = user[0]
        full_name = user[1] if len(user) > 1 else None
        name = full_name.split()[0] if full_name else "друзья"

        try:
            if telethon_client:
                await telethon_client.send_message(
                    telegram_id,
                    LTV_REMINDER,
                    parse_mode="HTML",
                    buttons=reminder_kb()
                )
            else:
                await bot.send_message(
                    telegram_id,
                    LTV_REMINDER,
                    parse_mode="HTML",
                    reply_markup=reminder_kb()
                )

            await mark_reminder_sent(telegram_id)

            print(f"✅ Напоминание отправлено: {telegram_id}")

        except Exception as e:
            print(f"❌ Ошибка {telegram_id}: {e}")


# Путь к твоему фото (поменяй на реальный!)
INDIVIDUAL_CLEANING_PHOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "individual_cleaning.jpg")

PHOTO_FLAT_TYPES = {
    "Поддерживающая уборка": "https://i.ibb.co/MkFY1ZYh/out-clean.jpg",
    "Генеральная уборка": "https://i.ibb.co/N2hXVJDk/Chat-GPT-Image-17-2026-00-25-57.png",
    "После ремонта": "https://i.ibb.co/mFX6JpHN/Chat-GPT-Image-5-2026-23-46-52.png",
    "Химчистка мебели": "https://i.ibb.co/QFDRxV5j/ggd.png",
    "Индивидуальная уборка": INDIVIDUAL_CLEANING_PHOTO,
}

cfg = load_config()
dp = Dispatcher()
logger = logging.getLogger(__name__)

class PrivateChatFilter:
    async def __call__(self, handler, event, data):
        msg = getattr(event, "message", None)
        if msg is None:
            return await handler(event, data)
        chat = getattr(msg, "chat", None)
        if chat is None or str(getattr(chat, "type", "")) != "private":
            return
        user = getattr(msg, "from_user", None)
        if user is None or getattr(user, "is_bot", False):
            return
        return await handler(event, data)


dp.message.middleware(PrivateChatFilter())


# ================= HELPERS =================

def safe_str(value, default="—"):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def calc_price_estimate(flat_type: str, cleaning_type: str) -> str:
    if cleaning_type == "Химчистка мебели":
        return "По согласованию"
    if cleaning_type == "Индивидуальная уборка":
        return "По согласованию"
    estimates = {
        "1-комнатная": "10 500–13 500 ₽",
        "2-комнатная": "13 500–16 500 ₽",
        "3-комнатная": "16 500–20 500 ₽",
        "Студия / по площади": "от 10 500 ₽",
    }
    return estimates.get(flat_type, "от 10 500 ₽")


async def update_abandoned_order(telegram_id: int, state: FSMContext, step: str):
    try:
        data = await state.get_data()
        await save_abandoned_order(
            telegram_id=telegram_id,
            cleaning_type=data.get("cleaning_type"),
            step=step,
            flat_type=data.get("flat_type"),
            metro_line=data.get("metro_line"),
            metro=data.get("metro"),
            address=data.get("address"),
            phone=data.get("phone"),
            comment=data.get("comment"),
            date=data.get("date"),
            time=data.get("time"),
            name=data.get("name"),
        )
    except Exception as e:
            logger.warning("Operation failed", exc_info=True)


ORDER_BACK_MAP = {
    CleaningOrder.flat_type.state: CleaningOrder.cleaning_type.state,
    CleaningOrder.date.state: CleaningOrder.flat_type.state,
    CleaningOrder.time.state: CleaningOrder.date.state,
    CleaningOrder.metro_station.state: CleaningOrder.time.state,
    CleaningOrder.address.state: CleaningOrder.metro_station.state,
    CleaningOrder.phone.state: CleaningOrder.address.state,
    CleaningOrder.name.state: CleaningOrder.phone.state,
    CleaningOrder.comment.state: CleaningOrder.name.state,
    CleaningOrder.photos.state: CleaningOrder.comment.state,
    CleaningOrder.preview.state: CleaningOrder.photos.state,
}

ORDER_BACK_ACTIONS = {
    CleaningOrder.cleaning_type.state: ("Шаг 1. Выберите тип уборки 👇", cleaning_type_kb()),
    CleaningOrder.flat_type.state: ("Шаг 2. Выберите тип квартиры 👇", flat_type_kb()),
    CleaningOrder.date.state: ("📅 Шаг 3. Выберите дату:", date_selection_kb()),
    CleaningOrder.time.state: ("⏰ Шаг 4. Выберите время:", time_selection_kb(cfg.work_start, cfg.work_end)),
    CleaningOrder.metro_station.state: ("📍 Шаг 5. Выберите станцию метро:", metro_lines_kb("order")),
    CleaningOrder.address.state: ("📍 Шаг 6. Укажите адрес:", order_step_kb()),
    CleaningOrder.phone.state: ("📱 Шаг 7. Укажите номер телефона:", phone_request_kb()),
    CleaningOrder.name.state: ("👤 Шаг 8. Имя/ФИО (необязательно):", name_kb()),
    CleaningOrder.comment.state: ("💬 Шаг 9. Комментарий:", order_step_kb()),
    CleaningOrder.photos.state: ("📸 Шаг 10. Прикрепите фотографии:", photo_kb()),
}

REPLY_BACK_STATES = {
    CleaningOrder.address.state,
    CleaningOrder.phone.state,
    CleaningOrder.comment.state,
}


@dp.callback_query(F.data == "order:back")
async def order_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(cb.message, state)
        return
    current = await state.get_state()
    prev = ORDER_BACK_MAP.get(current)
    if not prev:
        await cb.answer("Вы уже на первом шаге", show_alert=True)
        return
    await state.set_state(prev)
    text, kb = ORDER_BACK_ACTIONS[prev]
    if prev in REPLY_BACK_STATES:
        await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")
    else:
        try:
            await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest:
            await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.message(F.text == "🔙 Назад")
async def order_back_text(msg: Message, state: FSMContext):
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    current = await state.get_state()
    if not current or not current.startswith("CleaningOrder"):
        await msg.answer("Вы не находитесь в процессе оформления заказа.")
        return
    prev = ORDER_BACK_MAP.get(current)
    if not prev:
        await msg.answer("Вы уже на первом шаге")
        return
    await state.set_state(prev)
    text, kb = ORDER_BACK_ACTIONS[prev]
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data == "order:cancel")
async def order_cancel_callback(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    current = await state.get_state()
    if current and current.startswith("CleaningOrder"):
        await state.clear()
        try:
            await delete_abandoned_order(cb.from_user.id)
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)
        try:
            await cb.message.edit_text(ORDER_CANCELLED_TEXT, parse_mode="HTML", reply_markup=main_menu())
        except TelegramBadRequest:
            await cb.message.answer(ORDER_CANCELLED_TEXT, parse_mode="HTML", reply_markup=main_menu())
    else:
        try:
            await cb.message.edit_text("❌ Нет активного заказа для отмены.", reply_markup=main_menu())
        except TelegramBadRequest:
            await cb.message.answer("❌ Нет активного заказа для отмены.", reply_markup=main_menu())


async def build_preview_text(data: dict) -> str:
    service = data.get("cleaning_type", "—")
    date = data.get("date", "—")
    time = data.get("time", "—")
    address = data.get("address", "—")
    phone = data.get("phone", "—")
    comment = data.get("comment") or "—"
    photos_count = len(data.get("photos", []))
    flat_type = data.get("flat_type", "")
    name = data.get("name") or "—"
    price = calc_price_estimate(flat_type, service)
    return ORDER_PREVIEW_TEXT.format(
        service=service,
        flat_type=flat_type,
        date=date,
        time=time,
        address=address,
        metro=data.get("metro", "—"),
        phone=phone,
        name=name,
        comment=comment,
        photos_count=photos_count,
        price=price
    )


async def show_preview(msg: Message, state: FSMContext):
    data = await state.get_data()
    text = await build_preview_text(data)
    await msg.answer(text, reply_markup=preview_kb(), parse_mode="HTML")
    await state.set_state(CleaningOrder.preview)


async def send_order_to_admin(bot: Bot, order_id: str, data: dict, service_title: str, status: str = "new"):
    executor_id = data.get("executor_id")
    if executor_id and not data.get("executor_name"):
        ex = await get_executor(executor_id)
        if ex:
            data = {**data, "executor_name": ex[1] or ex[0]}
    text = format_order_text(data, service_title, order_id, status)
    if telethon_client:
        try:
            await telethon_client.send_message(cfg.admin_chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)
            await bot.send_message(cfg.admin_chat_id, text, parse_mode="HTML")
    else:
        await bot.send_message(cfg.admin_chat_id, text, parse_mode="HTML")


async def notify_executor(bot: Bot, order_id: str, data: dict):
    executor_id = data.get("executor_id")
    if not executor_id:
        return
    text = (
        f"📋 <b>Вам назначен новый заказ</b>\n\n"
        f"<b>Услуга:</b> {data.get('cleaning_type')}\n"
        f"<b>Тип квартиры:</b> {data.get('flat_type', '—')}\n"
        f"<b>Адрес:</b> {data.get('address')}\n"
        f"<b>Метро:</b> {data.get('metro', '—')}\n"
        f"<b>Дата:</b> {data.get('date')}\n"
        f"<b>Время:</b> {data.get('time')}\n"
        f"<b>Телефон клиента:</b> {data.get('phone', '—')}\n"
        f"<b>Клиент:</b> {safe_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Telegram:</b> {safe_str(data.get('username'))}\n"
        f"<b>Комментарий:</b> {safe_str(data.get('comment'))}\n\n"
        f"<b>ID заказа:</b> {order_id}"
    )
    if telethon_client:
        try:
            await telethon_client.send_message(executor_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    else:
        try:
            await bot.send_message(executor_id, text, parse_mode="HTML")
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)


# ================= WORK TIME =================

def continue_order_kb():

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="✨ Продолжить заказ",
            callback_data="continue_order"
        )
    )

    return builder.as_markup()

def is_work_time(now: datetime) -> bool:
    ws_h, ws_m = map(int, cfg.work_start.split(":"))
    we_h, we_m = map(int, cfg.work_end.split(":"))
    start = time(ws_h, ws_m)
    end = time(we_h, we_m)
    return start <= now.time() <= end


def is_time_in_range(time_str: str, work_start: str, work_end: str) -> bool:
    t = datetime.strptime(time_str, "%H:%M").time()
    start = datetime.strptime(work_start, "%H:%M").time()
    end = datetime.strptime(work_end, "%H:%M").time()
    return start <= t < end


# ================= FORMAT ORDER =================

def support_kb():
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="💬 Написать в поддержку",
            url="https://t.me/fynclean"
        )
    )

    return builder.as_markup()

def format_order_text(data: dict, service_title: str, order_id: str, status: str = "NEW") -> str:
    date = data.get("date", data.get("datetime", "—"))
    time = data.get("time", "—")
    fio = data.get("fio") or data.get("full_name") or data.get("name") or "—"
    name = data.get("name")
    if name:
        fio = f"{fio} {name}" if fio != "—" else name
    status_label = STATUS_LABELS.get(status, status)
    headers = {
        "new": "🆕 <b>Новая заявка | FYN Clean</b>",
        "accepted": "✅ <b>Заказ принят | FYN Clean</b>",
        "assigned": "👤 <b>Исполнитель назначен | FYN Clean</b>",
        "in_work": "🔄 <b>Заказ в работе | FYN Clean</b>",
        "contacted": "📞 <b>Связь установлена | FYN Clean</b>",
        "completed": "🎯 <b>Заказ выполнен | FYN Clean</b>",
        "cancelled": "❌ <b>Заказ отменён | FYN Clean</b>",
    }
    header = headers.get(status, f"📋 <b>Заказ | FYN Clean</b>")
    return (
        f"{header}\n"
        f"<b>Статус:</b> {status_label}\n"
        f"<b>ID:</b> {order_id}\n\n"
        f"<b>Услуга:</b> {service_title}\n"
        f"<b>Детали:</b> {data.get('details','—')}\n"
        f"<b>Метро:</b> {data.get('metro','—')}\n"
        f"<b>Адрес:</b> {data.get('address','—')}\n"
        f"<b>Дата:</b> {date}\n"
        f"<b>Время:</b> {time}\n\n"
        f"<b>Контакт:</b> {fio}\n"
        f"<b>Телефон:</b> {data.get('phone','—')}\n"
        f"<b>Telegram:</b> {data.get('username','—')}\n"
        f"<b>Комментарий:</b> {data.get('comment','—')}\n"
    )


# ================= BASIC COMMANDS =================

@dp.callback_query(F.data == "reminder:order")
async def reminder_order(
    cb: CallbackQuery,
    state: FSMContext
):
    await cb.answer()

    await state.clear()
    await state.set_state(CleaningOrder.cleaning_type)

    await cb.message.answer(
        "Шаг 1. Выберите тип уборки 👇",
        reply_markup=cleaning_type_kb()
    )


@dp.callback_query(F.data == "reminder:cancel")
async def reminder_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    await cb.message.answer("Хорошо, если передумаете — всегда можете заказать уборку через меню.", reply_markup=main_menu())


@dp.callback_query(F.data == "continue_order")
async def continue_order_callback(
    cb: CallbackQuery,
    state: FSMContext
):
    await cb.answer()
    await state.clear()
    telegram_id = cb.from_user.id
    abandoned = None
    async with aiosqlite.connect("orders.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM abandoned_orders WHERE telegram_id = ? AND reminded = 1",
            (telegram_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                abandoned = dict(row)
    if abandoned:
        saved = {
            "cleaning_type": abandoned.get("cleaning_type"),
            "flat_type": abandoned.get("flat_type"),
            "metro_line": abandoned.get("metro_line"),
            "metro": abandoned.get("metro"),
            "address": abandoned.get("address"),
            "phone": abandoned.get("phone"),
            "comment": abandoned.get("comment"),
            "date": abandoned.get("date"),
            "time": abandoned.get("time"),
            "name": abandoned.get("name"),
        }
        saved = {k: v for k, v in saved.items() if v is not None}
        await state.update_data(**saved)
        await mark_abandoned_reminded(telegram_id)

    step_to_state = {
        "выбрал тип уборки": CleaningOrder.flat_type,
        "выбрал тип квартиры": CleaningOrder.date,
        "ввёл тип квартиры": CleaningOrder.date,
        "выбрал дату": CleaningOrder.time,
        "выбрал время": CleaningOrder.metro_station,
        "выбрал метро": CleaningOrder.address,
        "ввёл адрес": CleaningOrder.phone,
        "ввёл телефон": CleaningOrder.name,
        "ввёл имя/ФИО": CleaningOrder.comment,
        "ввёл комментарий": CleaningOrder.photos,
        "добавил фото": CleaningOrder.photos,
        "пропустил фото": CleaningOrder.preview,
    }
    next_state = step_to_state.get(abandoned.get("step", ""), CleaningOrder.cleaning_type) if abandoned else CleaningOrder.cleaning_type
    await state.set_state(next_state)
    if next_state == CleaningOrder.cleaning_type:
        text = "✨ Продолжаем оформление?\n\nШаг 1. Выберите тип уборки 👇"
        kb = cleaning_type_kb()
    elif next_state == CleaningOrder.flat_type:
        text = "🏠 Шаг 2. Выберите тип квартиры 👇"
        kb = flat_type_kb()
    elif next_state == CleaningOrder.date:
        text = "📅 Шаг 3. Выберите дату:"
        kb = date_selection_kb()
    elif next_state == CleaningOrder.time:
        text = "⏰ Шаг 4. Выберите время:"
        kb = time_selection_kb(cfg.work_start, cfg.work_end)
    elif next_state == CleaningOrder.metro_station:
        text = "📍 Шаг 5. Выберите станцию метро:"
        kb = metro_lines_kb("order")
    elif next_state == CleaningOrder.address:
        text = "📍 Шаг 6. Укажите адрес:"
        kb = order_step_kb()
    elif next_state == CleaningOrder.phone:
        text = "📱 Шаг 7. Укажите номер телефона:"
        kb = phone_request_kb()
    elif next_state == CleaningOrder.name:
        text = "👤 Шаг 8. Имя/ФИО (необязательно):"
        kb = name_kb()
    elif next_state == CleaningOrder.comment:
        text = "💬 Шаг 9. Комментарий:"
        kb = order_step_kb()
    elif next_state == CleaningOrder.photos:
        text = "📸 Шаг 10. Прикрепите фотографии:"
        kb = photo_kb()
    elif next_state == CleaningOrder.preview:
        await show_preview(cb.message, state)
        return
    else:
        text = "✨ Продолжаем оформление?\n\nШаг 1. Выберите тип уборки 👇"
        kb = cleaning_type_kb()
    try:
        await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")

@dp.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    print(f"[DEBUG] /start from {msg.from_user.id}")
    await state.clear()

    full_name = (
        f"{msg.from_user.first_name or ''} "
        f"{msg.from_user.last_name or ''}"
    ).strip() or None

    username = (
        f"@{msg.from_user.username}"
        if msg.from_user.username
        else None
    )


    # =========================
    # РЕФЕРАЛЬНАЯ СИСТЕМА
    # =========================

    referred_by = None

    if msg.text:
        args = msg.text.split()

        if len(args) > 1:
            ref = args[1]

            if ref:
                referred_by = ref


    await upsert_user(
        msg.from_user.id,
        username,
        full_name,
        invited_by=referred_by,
        name=None
    )


    await msg.answer(
        WELCOME,
        reply_markup=main_menu()
    )


def calc_reputation(orders_cleaning: int) -> str:
    # простая репутация по количеству заказов
    if orders_cleaning >= 10:
        return "🏆 Премиум клиент"
    if orders_cleaning >= 3:
        return "✅ Надёжный клиент"
    return "🆕 Новый клиент"

@dp.message(Command("order_clean"))
async def order_clean_command(
    msg: Message,
    state: FSMContext
):
    await state.clear()

    await state.set_state(
        CleaningOrder.cleaning_type
    )

    await msg.answer(
        "Шаг 1. Выберите тип уборки 👇",
        reply_markup=cleaning_type_kb()
    )

@dp.message(Command("order_dryclean"))
async def order_dryclean_command(msg: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CleaningOrder.cleaning_type)
    await msg.answer(
        "Шаг 1. Выберите тип уборки 👇",
        reply_markup=cleaning_type_kb()
    )


@dp.callback_query(F.data == "repeat:confirm")
async def repeat_confirm(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await show_preview(cb.message, state)


@dp.callback_query(F.data == "repeat:edit")
async def repeat_edit(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        await cb.message.edit_text("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())
    except TelegramBadRequest:
        await cb.message.answer("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())


@dp.message(F.text == "👤 Мой профиль")
async def my_profile(msg: Message):
    prof = await get_user_profile(msg.from_user.id)

    # если вдруг нет записи (например пользователь не нажимал /start)
    if not prof:
        full_name = f"{msg.from_user.first_name or ''} {msg.from_user.last_name or ''}".strip() or None
        username = f"@{msg.from_user.username}" if msg.from_user.username else None
        await upsert_user(msg.from_user.id, username, full_name, name=None)
        prof = await get_user_profile(msg.from_user.id)

    orders = prof["orders_cleaning"]
    rep = calc_reputation(orders)

    name = prof["full_name"] or "—"
    username = prof["username"] or "—"
    first_seen = prof["first_seen"] or "—"

    text = (
        "👤 <b>Мой профиль | FYN Clean</b>\n\n"
        f"<b>Имя:</b> {name}\n"
        f"<b>Telegram:</b> {username}\n\n"
        f"🧼 <b>Заказов на клининг:</b> {orders}\n"
        f"⭐ <b>Репутация:</b> {rep}\n\n"
        f"📅 <b>Дата регистрации:</b> {first_seen}"
    )

    await msg.answer(text)


@dp.message(F.text == "🎁 Реферальная программа")
async def price(msg: Message):
    await msg.answer(PRICE_CLEANING)
    await msg.answer(PRICE_DRY)


# ================= CLEANING ORDER =================
@dp.message(Command("price"))
async def price_command(msg: Message):

    await msg.answer_photo(
        photo="https://i.ibb.co/rGDLB4Nx/IMG-20260302-021957-940.jpg",
        caption=ABOUT_TEXT,
        parse_mode="HTML"
    )

@dp.message(F.text == "✨ О сервисе")
async def about(msg: Message):
    await msg.answer_photo(
        photo="https://i.ibb.co/rGDLB4Nx/IMG-20260302-021957-940.jpg",
        caption=ABOUT_TEXT,
        parse_mode="HTML"
    )


@dp.message(F.text == "🧹 Услуги и цены")
async def services_info(msg: Message):
    await msg.answer(SERVICES_TEXT, parse_mode="HTML")


@dp.message(Command("contact"))
async def contact_command(msg: Message):

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Смотреть отзывы и работы",
            url="https://t.me/fyncleanwork"
        )
    )

    await msg.answer_photo(
        photo="https://i.ibb.co/DPW9r8jb/IMG-20260302-023112-454.jpg",
        caption=REVIEWS_TEXT,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.message(F.text == "🌟 Отзывы")
async def reviews(msg: Message):

    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="⭐ Смотреть отзывы и работы",
            url="https://t.me/fyncleanwork"
        )
    )

    await msg.answer_photo(
        photo="https://i.ibb.co/DPW9r8jb/IMG-20260302-023112-454.jpg",
        caption=REVIEWS_TEXT,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.message(F.text == "💬 Поддержка")
async def support(msg: Message):
    await msg.answer_photo(
        photo="https://i.ibb.co/0R1h3349/Image.png",
        caption=SUPPORT1,
        parse_mode="HTML",
        reply_markup=support_kb()
    )

@dp.message(Command("help"))
async def help_command(msg: Message):

    await msg.answer_photo(
        photo="https://i.ibb.co/0R1h3349/Image.png",
        caption=SUPPORT1,
        parse_mode="HTML",
        reply_markup=support_kb()
    )

@dp.message(F.text == "📞 Связаться")
async def contact(msg: Message):
    await msg.answer_photo(
        photo="https://i.ibb.co/WvS8Btnb/contact-no-shadow.jpg",
        caption=CONTACT_TEXT,
        parse_mode="HTML"
    )

@dp.message(F.text == "🎁 Пригласить друга")
async def referral(msg: Message):

    code = await get_or_create_referral_code(
        msg.from_user.id
    )

    link = f"https://t.me/FYNclean_bot?start={code}"

    builder = InlineKeyboardBuilder()

    builder.button(
        text="📤 Пригласить друга",
        url=f"https://t.me/share/url?url={link}"
    )

    text = (
        "🎁 <b>Реферальная программа FYN Clean</b>\n\n"
        "Пригласите друга и получите бонус 💰\n\n"
        "Ваш друг получает качественный клининг,\n"
        "а вы получаете скидку 10% после его первого заказа.\n\n"
        f"🔗 Ваша персональная ссылка:\n"
        f"{link}"
    )

    await msg.answer(
        text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.message(F.text == "✨ Заказать уборку")
@dp.message(F.text == "Заказать клининг")
async def start_order(msg: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CleaningOrder.cleaning_type)

    await msg.answer(
        "✨ Шаг 1. Выберите тип уборки 👇",
        reply_markup=cleaning_type_kb()
    )


@dp.message(CleaningOrder.cleaning_type)
async def cleaning_type(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(cleaning_type=msg.text)
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.flat_type)
    await msg.answer(
        "Шаг 2. Выберите тип квартиры (1к / 2к / 3к или площадь м²):",
        reply_markup=flat_type_kb()
    )


@dp.message(CleaningOrder.flat_type)
async def flat_type(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(flat_type=msg.text)
    await update_abandoned_order(msg.from_user.id, state, "ввёл тип квартиры")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.date)
    await msg.answer("📅 Шаг 3. Выберите дату:", reply_markup=date_selection_kb())


@dp.callback_query(F.data.startswith("order:date:"), CleaningOrder.date)
async def date_callback(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("order:date:", 1)[1]
    if key == "manual":
        await cb.answer()
        await cb.message.answer("Введите дату в формате ДД.ММ.ГГГГ (например: 15.10.2026):")
        return
    try:
        d = datetime.strptime(key, "%Y-%m-%d").date()
        label = d.strftime("%d.%m.%Y")
        await state.update_data(date=label)
        await update_abandoned_order(cb.from_user.id, state, "выбрал дату")
        edit_data = await state.get_data()
        if edit_data.get("editing"):
            await state.update_data(editing=None)
            await show_preview(cb.message, state)
            await cb.answer()
            return
        await state.set_state(CleaningOrder.time)
        await cb.message.edit_text(f"📅 Выбрано: <b>{label}</b>\n\n⏰ Шаг 4. Выберите время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end), parse_mode="HTML")
        await cb.answer()
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await cb.answer("Ошибка даты", show_alert=True)


@dp.message(CleaningOrder.date)
async def date_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    text = msg.text.strip()
    try:
        d = datetime.strptime(text, "%d.%m.%Y").date()
        label = d.strftime("%d.%m.%Y")
        await state.update_data(date=label)
        await update_abandoned_order(msg.from_user.id, state, "выбрал дату")
        edit_data = await state.get_data()
        if edit_data.get("editing"):
            await state.update_data(editing=None)
            await show_preview(msg, state)
            return
        await state.set_state(CleaningOrder.time)
        await msg.answer(f"📅 Выбрано: <b>{label}</b>\n\n⏰ Шаг 4. Выберите время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end), parse_mode="HTML")
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await msg.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ (например: 15.10.2026):")


@dp.callback_query(F.data.startswith("order:time:"), CleaningOrder.time)
async def time_callback(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("order:time:", 1)[1]
    if key == "manual":
        await cb.answer()
        await cb.message.answer(f"Введите время в формате ЧЧ:ММ (например: 14:00).\nДоступное время: {cfg.work_start}–{cfg.work_end}:")
        return
    if not is_time_in_range(key, cfg.work_start, cfg.work_end):
        await cb.answer("Время вне рабочего диапазона", show_alert=True)
        return
    await state.update_data(time=key)
    await update_abandoned_order(cb.from_user.id, state, "выбрал время")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(cb.message, state)
        await cb.answer()
        return
    await state.set_state(CleaningOrder.metro_station)
    try:
        await cb.message.edit_text(f"⏰ Выбрано: <b>{key}</b>\n\n📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(f"⏰ Выбрано: <b>{key}</b>\n\n📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    await cb.answer()


@dp.message(CleaningOrder.time)
async def time_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    text = msg.text.strip()
    if not (len(text) == 5 and text[2] == ":"):
        await msg.answer("Неверный формат. Введите время как ЧЧ:ММ (например: 14:00):")
        return
    if not is_time_in_range(text, cfg.work_start, cfg.work_end):
        await msg.answer(f"Время должно быть в диапазоне {cfg.work_start}–{cfg.work_end}:")
        return
    await state.update_data(time=text)
    await update_abandoned_order(msg.from_user.id, state, "выбрал время")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.metro_station)
    await msg.answer(f"⏰ Выбрано: <b>{text}</b>\n\n📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")


@dp.message(CleaningOrder.metro_station)
async def metro(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(metro=msg.text)
    await update_abandoned_order(msg.from_user.id, state, "выбрал метро")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.address)
    await msg.answer(
        "📍 Шаг 6. Укажите адрес (улица, дом, подъезд, квартира, этаж):",
        reply_markup=order_step_kb()
    )


@dp.callback_query(F.data.startswith("order:line:"), CleaningOrder.metro_station)
async def metro_line_callback(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("order:line:", 1)[1]
    if key == "manual":
        await cb.answer()
        await cb.message.edit_text("📍 Введите станцию метро текстом:", parse_mode="HTML")
        return
    try:
        line_idx = int(key)
        line_name = list(METRO.keys())[line_idx]
    except (ValueError, IndexError):
        await cb.answer("Неверная линия метро", show_alert=True)
        return
    await state.update_data(metro_line=line_name)
    await update_abandoned_order(cb.from_user.id, state, "выбрал линию метро")
    await cb.answer()
    try:
        await cb.message.edit_text(f"📍 <b>{line_name}</b>\n\nВыберите станцию:", reply_markup=metro_stations_kb("order", line_name), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(f"📍 <b>{line_name}</b>\n\nВыберите станцию:", reply_markup=metro_stations_kb("order", line_name), parse_mode="HTML")


@dp.callback_query(F.data.startswith("order:st:"), CleaningOrder.metro_station)
async def metro_station_callback(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("order:st:", 1)[1]
    if key == "manual":
        await cb.answer()
        await cb.message.edit_text("📍 Введите станцию метро текстом:", parse_mode="HTML")
        return
    try:
        station_idx = int(key)
        data = await state.get_data()
        metro_line = data.get("metro_line")
        if metro_line is None:
            await cb.answer("Сначала выберите линию метро", show_alert=True)
            return
        stations = METRO.get(metro_line, [])
        station_name = stations[station_idx]
    except (ValueError, IndexError):
        await cb.answer("Неверная станция метро", show_alert=True)
        return
    await state.update_data(metro=station_name)
    await update_abandoned_order(cb.from_user.id, state, "выбрал метро")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await cb.answer()
        await show_preview(cb.message, state)
        return
    await state.set_state(CleaningOrder.address)
    await cb.answer()
    try:
        await cb.message.edit_text(f"📍 Выбрано: <b>{station_name}</b>\n\n📍 Шаг 6. Укажите адрес:", reply_markup=order_step_kb(), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(f"📍 Выбрано: <b>{station_name}</b>\n\n📍 Шаг 6. Укажите адрес:", reply_markup=order_step_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "order:back_lines", CleaningOrder.metro_station)
async def metro_back_lines(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.edit_text("📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")


@dp.message(CleaningOrder.address)
async def address(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(address=msg.text)
    await update_abandoned_order(msg.from_user.id, state, "ввёл адрес")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.phone)
    await msg.answer(
        "📱 Шаг 7. Укажите номер телефона:",
        reply_markup=phone_request_kb()
    )


@dp.message(CleaningOrder.phone, F.contact)
async def phone_contact(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.contact.phone_number)
    await update_abandoned_order(msg.from_user.id, state, "ввёл телефон")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.name)
    await msg.answer(reply_markup=ReplyKeyboardRemove())
    await msg.answer("👤 Шаг 8. Имя/ФИО (необязательно):\nЕсли нет — нажмите «Пропустить».", reply_markup=name_kb())


@dp.message(CleaningOrder.phone, F.text)
async def phone_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(phone=msg.text)
    await update_abandoned_order(msg.from_user.id, state, "ввёл телефон")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.name)
    await msg.answer(reply_markup=ReplyKeyboardRemove())
    await msg.answer("👤 Шаг 8. Имя/ФИО (необязательно):\nЕсли нет — нажмите «Пропустить».", reply_markup=name_kb())


@dp.callback_query(F.data == "order:name:skip", CleaningOrder.name)
async def name_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None, name="")
        await show_preview(cb.message, state)
        return
    await state.update_data(name="")
    await state.set_state(CleaningOrder.comment)
    await cb.message.edit_text("💬 Шаг 9. Комментарий (если нет — напишите '-'):", reply_markup=order_step_kb(), parse_mode="HTML")


@dp.message(CleaningOrder.name)
async def name_handler(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    text = msg.text.strip()
    if text.lower() in ("-", "пропустить", "skip"):
        text = ""
    await state.update_data(name=text)
    await update_abandoned_order(msg.from_user.id, state, "ввёл имя/ФИО")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.comment)
    await msg.answer("💬 Шаг 9. Комментарий (если нет — напишите '-'):", reply_markup=order_step_kb())


@dp.message(CleaningOrder.comment)
async def comment_edit_handler(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    data = await state.get_data()
    if data.get("editing") == "comment":
        await state.update_data(comment=msg.text.strip(), editing=None)
        await show_preview(msg, state)
        return
    await state.update_data(comment=msg.text.strip())
    await update_abandoned_order(msg.from_user.id, state, "ввёл комментарий")
    await state.set_state(CleaningOrder.photos)
    await msg.answer("📸 Прикрепите фотографии помещения (или нажмите «Пропустить»):", reply_markup=photo_kb())


@dp.message(CleaningOrder.photos, F.photo)
async def photos_handler(msg: Message, state: FSMContext):
    photo = msg.photo[-1]
    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(photo.file_id)
    await state.update_data(photos=photos)
    await update_abandoned_order(msg.from_user.id, state, "добавил фото")
    await msg.answer(f"✅ Фото добавлено ({len(photos)}). Добавьте ещё или нажмите «Пропустить».", reply_markup=photo_kb())


@dp.message(CleaningOrder.photos, F.text)
async def photos_text_handler(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    text = msg.text.strip()
    if text.lower() in ("пропустить", "skip", "-"):
        await update_abandoned_order(msg.from_user.id, state, "пропустил фото")
        await show_preview(msg, state)
        return
    await msg.answer("📸 Отправьте фотографии помещения или нажмите «Пропустить».", reply_markup=photo_kb())


@dp.callback_query(F.data == "order:photo:skip", CleaningOrder.photos)
async def photos_skip(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await update_abandoned_order(cb.from_user.id, state, "пропустил фото")
    await show_preview(cb.message, state)


@dp.callback_query(F.data == "order:photo:add", CleaningOrder.photos)
async def photos_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await cb.message.answer("Отправьте фотографии помещения:", reply_markup=photo_kb())


@dp.callback_query(F.data == "order:preview:confirm", CleaningOrder.preview)
async def confirm_order(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    data["username"] = f"@{cb.from_user.username}" if cb.from_user.username else "—"
    data["telegram_id"] = cb.from_user.id
    data["details"] = f"{data.get('cleaning_type')} | {data.get('flat_type')}"
    full_name = f"{cb.from_user.first_name or ''} {cb.from_user.last_name or ''}".strip() or None
    name = data.get("name") or ""
    if name:
        if full_name:
            full_name = f"{full_name} {name}"
        else:
            full_name = name
    data["fio"] = full_name or "—"

    flat_type = data.get("flat_type", "")
    data["price"] = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
    if flat_type in ("1-комнатная", "2-комнатная", "3-комнатная"):
        data["rooms"] = flat_type.split("-")[0]
    elif flat_type == "Студия / площадь":
        data["rooms"] = "студия"
    else:
        data["rooms"] = flat_type

    order_id = uuid.uuid4().hex[:10]

    try:
        await create_order(
            order_id,
            "cleaning",
            json.dumps(data, ensure_ascii=False),
            datetime.now().isoformat(),
            status="new",
            date=data.get("date"),
            time=data.get("time")
        )

        for photo_file_id in data.get("photos", []):
            await save_order_photo(order_id, cb.from_user.id, photo_file_id, "client")

        await delete_abandoned_order(cb.from_user.id)

        full_name = f"{cb.from_user.first_name or ''} {cb.from_user.last_name or ''}".strip() or None
        real_username = f"@{cb.from_user.username}" if cb.from_user.username else None
        await upsert_user(cb.from_user.id, real_username, full_name, name=data.get("name"))
        await inc_cleaning_orders(cb.from_user.id, 1)

        await send_order_to_admin(bot, order_id, data, "Уборка квартиры", status="new")

        await state.clear()
        await cb.message.answer(ORDER_CREATED_TEXT.format(order_id=order_id), reply_markup=main_menu())
        return order_id
    except Exception as e:
        await cb.message.answer(f"❌ Ошибка при создании заказа: {e}\nПопробуйте ещё раз.", reply_markup=main_menu())
        return None


@dp.callback_query(F.data == "order:preview:edit", CleaningOrder.preview)
async def preview_edit(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.edit_text("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())
    except TelegramBadRequest:
        await cb.message.answer("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())


@dp.callback_query(F.data == "order:preview:cancel", CleaningOrder.preview)
async def preview_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    try:
        await delete_abandoned_order(cb.from_user.id)
    except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    await cb.message.answer(ORDER_CANCELLED_TEXT, reply_markup=main_menu())


@dp.callback_query(F.data.startswith("order:edit:"), CleaningOrder.preview)
async def edit_field_select(cb: CallbackQuery, state: FSMContext):
    field = cb.data.split("order:edit:", 1)[1]
    await cb.answer()
    await state.update_data(editing=field)

    if field == "cleaning_type":
        await state.set_state(CleaningOrder.cleaning_type)
        try:
            await cb.message.edit_text("🪄 Выберите новый тип уборки:", reply_markup=cleaning_type_kb())
        except TelegramBadRequest:
            await cb.message.answer("🪄 Выберите новый тип уборки:", reply_markup=cleaning_type_kb())
    elif field == "flat_type":
        await state.set_state(CleaningOrder.flat_type)
        try:
            await cb.message.edit_text("🏠 Выберите тип квартиры:", reply_markup=flat_type_kb())
        except TelegramBadRequest:
            await cb.message.answer("🏠 Выберите тип квартиры:", reply_markup=flat_type_kb())
    elif field == "date":
        await state.set_state(CleaningOrder.date)
        try:
            await cb.message.edit_text("📅 Введите новую дату:", reply_markup=date_selection_kb())
        except TelegramBadRequest:
            await cb.message.answer("📅 Введите новую дату:", reply_markup=date_selection_kb())
    elif field == "time":
        await state.set_state(CleaningOrder.time)
        try:
            await cb.message.edit_text("⏰ Введите новое время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end))
        except TelegramBadRequest:
            await cb.message.answer("⏰ Введите новое время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end))
    elif field == "address":
        await state.set_state(CleaningOrder.address)
        try:
            await cb.message.edit_text("📍 Введите новый адрес:", reply_markup=order_step_kb())
        except TelegramBadRequest:
            await cb.message.answer("📍 Введите новый адрес:", reply_markup=order_step_kb())
    elif field == "metro":
        await state.set_state(CleaningOrder.metro_station)
        try:
            await cb.message.edit_text("📍 Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
        except TelegramBadRequest:
            await cb.message.answer("📍 Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    elif field == "phone":
        await state.set_state(CleaningOrder.phone)
        try:
            await cb.message.edit_text("📱 Введите новый номер телефона или отправьте контакт:", reply_markup=phone_request_kb())
        except TelegramBadRequest:
            await cb.message.answer("📱 Введите новый номер телефона или отправьте контакт:", reply_markup=phone_request_kb())
    elif field == "name":
        await state.set_state(CleaningOrder.name)
        try:
            await cb.message.edit_text("👤 Введите имя/ФИО (или нажмите «Пропустить»):", reply_markup=name_kb())
        except TelegramBadRequest:
            await cb.message.answer("👤 Введите имя/ФИО (или нажмите «Пропустить»):", reply_markup=name_kb())
    elif field == "comment":
        await state.set_state(CleaningOrder.comment)
        try:
            await cb.message.edit_text("💬 Введите новый комментарий:", reply_markup=order_step_kb())
        except TelegramBadRequest:
            await cb.message.answer("💬 Введите новый комментарий:", reply_markup=order_step_kb())
    elif field == "photos":
        await state.update_data(photos=[])
        await state.set_state(CleaningOrder.photos)
        try:
            await cb.message.edit_text("📸 Отправьте новые фотографии:", reply_markup=photo_kb())
        except TelegramBadRequest:
            await cb.message.answer("📸 Отправьте новые фотографии:", reply_markup=photo_kb())
    elif field == "back":
        await show_preview(cb.message, state)




@dp.callback_query(F.data.startswith("cleaning:type:"), CleaningOrder.cleaning_type)
async def pick_cleaning_type(cb: CallbackQuery, state: FSMContext, bot: Bot):
    key = cb.data.split("cleaning:type:", 1)[1]
    mapping = {
        "regular": "Поддерживающая уборка",
        "general": "Генеральная уборка",
        "after": "После ремонта",
        "hym2": "Химчистка мебели",
        "individual": "Индивидуальная уборка",
    }
    selected = mapping.get(key)

    if not selected:
        await cb.answer("Ошибка выбора", show_alert=True)
        return

    await state.update_data(cleaning_type=selected)
    await update_abandoned_order(
        cb.from_user.id,
        state,
        "выбрал тип уборки"
    )

    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(cb.message, state)
        await cb.answer()
        return

    if selected == "Индивидуальная уборка":
        await cb.answer()
        try:
            await bot.send_photo(
                chat_id=cb.message.chat.id,
                photo=FSInputFile(INDIVIDUAL_CLEANING_PHOTO),
                caption=INDIVIDUAL_CLEANING_TEXT,
                parse_mode="HTML"
            )
            try:
                await cb.message.delete()
            except Exception as e:
                logger.warning("Operation failed", exc_info=True)
        except Exception as e:
            await cb.message.answer(
                INDIVIDUAL_CLEANING_TEXT,
                parse_mode="HTML"
            )
            print(f"Ошибка фото индивидуальной уборки: {e}")
        await state.set_state(CleaningOrder.flat_type)
        await cb.message.answer("Шаг 2. Выберите тип квартиры (1к / 2к / 3к или площадь м²):", reply_markup=flat_type_kb())
        await cb.answer()
        return

    photo = PHOTO_FLAT_TYPES.get(selected)

    what_included_links = {
        "Поддерживающая": "https://t.me/stroiteli_v_msk/33690",
        "Генеральная уборка": "https://t.me/kristalclin/33",
        "После ремонта": "https://t.me/kristalclin/40",
        "Химчистка": "https://t.me/hiteczone/2106?single"
    }
    link = what_included_links.get(selected, "https://example.com/obshchaya-informaciya")

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Заполнить форму ✍🏻", callback_data="order:proceed"))
    builder.row(InlineKeyboardButton(text="Что входит в уборку", url=link))
    builder.row(InlineKeyboardButton(text="Поддержка", url="https://t.me/FYNclean"))
    kb = builder.as_markup()

    try:
        await bot.send_photo(
            chat_id=cb.message.chat.id,
            photo=photo,
            caption=(
                f"✨ <b>Вы выбрали:</b> {selected}\n\n"
                "Далее вам осталось заполнить форму заказа и наши клинеры свяжутся с Вами для согласования времени."
            ),
            parse_mode="HTML",
            reply_markup=kb,
        )
        try:
            await cb.message.delete()
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    except Exception as e:
        await cb.message.answer(
            f"✨ Вы выбрали: {selected}\n\n"
            "Шаг 2. Укажите тип квартиры\n\n"
            "Выберите действие ниже:",
            reply_markup=kb
        )
        print(f"Ошибка фото: {e}")

    await cb.answer()


@dp.callback_query(F.data == "individual:order")
async def individual_clean_order(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(cleaning_type="Индивидуальная уборка")
    await save_abandoned_order(
        telegram_id=cb.from_user.id,
        cleaning_type="Индивидуальная уборка",
        step="выбрал тип уборки"
    )
    await state.set_state(CleaningOrder.flat_type)
    try:
        await cb.message.edit_text(
            "✨ <b>Заказ индивидуальной уборки</b>\n\n"
            "Шаг 2. Выберите тип квартиры:",
            parse_mode="HTML",
            reply_markup=flat_type_kb()
        )
    except TelegramBadRequest:
        await cb.message.answer(
            "✨ <b>Заказ индивидуальной уборки</b>\n\n"
            "Шаг 2. Выберите тип квартиры:",
            parse_mode="HTML",
            reply_markup=flat_type_kb()
        )


@dp.callback_query(F.data == "individual:back")
async def individual_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.clear()
    try:
        await cb.message.edit_text("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb())
    except TelegramBadRequest:
        await cb.message.answer("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb())


@dp.callback_query(F.data == "info:what_included")
async def show_what_included(cb: CallbackQuery):
    text = (
        "Что входит в уборку:\n\n"
        "• Протирка пыли со всех поверхностей\n"
        "• Мытьё полов и плинтусов\n"
        "• Уборка ванной и санузла\n"
        "• Мытьё кухонной плиты, вытяжки\n"
        "• Вынос мусора\n"
        "• ... (добавь полный список под свой тип уборки)"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="back_to_step2"))
    await cb.message.edit_text(text, reply_markup=builder.as_markup())
    await cb.answer()


@dp.callback_query(F.data == "order:proceed")
async def start_flat_selection(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cleaning_type = data.get("cleaning_type", "Неизвестно")
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="1-комнатная", callback_data="flat:1k"),
        InlineKeyboardButton(text="2-комнатная", callback_data="flat:2k"),
    )
    builder.row(
        InlineKeyboardButton(text="3-комнатная", callback_data="flat:3k"),
        InlineKeyboardButton(text="Студия / площадь", callback_data="flat:other"),
    )
    kb = builder.as_markup()
    try:
        await cb.message.edit_text(
            f"Вы выбрали: {cleaning_type}\n\n"
            "Укажите тип квартиры 👇\n"
            "Выберите вариант или напишите площадь:",
            reply_markup=kb
        )
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await cb.message.answer(
            f"Вы выбрали: {cleaning_type}\n\n"
            "Укажите тип квартиры 👇\n"
            "Выберите вариант или напишите площадь:",
            reply_markup=kb
        )
    await state.set_state(CleaningOrder.flat_type)
    await cb.answer()


@dp.callback_query(F.data == "support:contact")
async def show_support(cb: CallbackQuery):
    text = (
        "Поддержка:\n\n"
        "Напишите нам @manager_username\n"
        "или позвоните: +7 (XXX) XXX-XX-XX\n"
        "Мы ответим в течение 5–15 минут"
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="back_to_step2"))
    try:
        await cb.message.edit_text(text, reply_markup=builder.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=builder.as_markup())
    await cb.answer()


@dp.callback_query(F.data == "back_to_step2")
async def back_to_step2(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = data.get("cleaning_type", "Неизвестно")
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Что входит в уборку", callback_data="info:what_included"))
    builder.row(InlineKeyboardButton(text="Заполнить форму ✍🏻", callback_data="order:proceed"))
    builder.row(InlineKeyboardButton(text="Поддержка", callback_data="support:contact"))
    try:
        await cb.message.edit_text(
            f"✨ <b>Вы выбрали:</b> {selected}\n\n"
            "Шаг 2. Укажите тип квартиры\n\n"
            "Выберите действие ниже:",
            reply_markup=builder.as_markup()
        )
    except TelegramBadRequest:
        await cb.message.answer(
            f"✨ <b>Вы выбрали:</b> {selected}\n\n"
            "Шаг 2. Укажите тип квартиры\n\n"
            "Выберите действие ниже:",
            reply_markup=builder.as_markup()
        )
    await cb.answer()


@dp.callback_query(F.data.startswith("flat:"), CleaningOrder.flat_type)
async def process_flat_type(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("flat:")[1]
    mapping = {
        "1k": "1-комнатная",
        "2k": "2-комнатная",
        "3k": "3-комнатная",
        "other": "Студия / по площади",
    }
    flat_type = mapping.get(key, "Неизвестно")
    await state.update_data(flat_type=flat_type)
    await update_abandoned_order(cb.from_user.id, state, "выбрал тип квартиры")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(cb.message, state)
        await cb.answer()
        return
    try:
        await cb.message.delete()
    except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    if key == "other":
        await state.set_state(CleaningOrder.flat_type)
        await cb.message.answer(
            "Укажите площадь квартиры в м² или описание\n"
            "(например: 42 м², студия, 58 м² с балконом):"
        )
    else:
        await state.set_state(CleaningOrder.date)
        await cb.message.answer("📅 Шаг 3. Выберите дату:", reply_markup=date_selection_kb())
    await cb.answer(f"Выбрано: {flat_type}")


# ================= MY ORDERS =================

@dp.message(F.text == "📋 Мои заказы")
async def my_orders(msg: Message, state: FSMContext):
    current = await state.get_state()
    if current == ExecutorStates.menu.state:
        executor = await get_executor(msg.from_user.id)
        if not executor or not executor[3]:
            await msg.answer("❌ У вас нет доступа.", reply_markup=main_menu())
            return
        orders = await get_orders_by_executor(msg.from_user.id)
        if not orders:
            await msg.answer("📭 <b>Нет назначенных заказов</b>", parse_mode="HTML", reply_markup=executor_menu_kb())
            return
        text = "📋 <b>Мои заказы</b>\n\n"
        b = InlineKeyboardBuilder()
        for o in orders:
            order_id, service, data_json, status, created_at, date, time = o
            data = json.loads(data_json)
            text += f"🆕 <b>{order_id}</b> | {service}\n"
            text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
            text += f"📍 {data.get('address', '—')}\n"
            text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
            b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
        b.button(text="🔙 В главное меню", callback_data="my_orders:back")
        b.adjust(1)
        await msg.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
        return
    await state.clear()
    await msg.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=my_orders_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "my_orders:back")
async def my_orders_back(cb: CallbackQuery):
    await cb.answer()
    executor = await get_executor(cb.from_user.id)
    if executor and executor[3]:
        await cb.message.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=executor_menu_kb(), parse_mode="HTML")
    else:
        await cb.message.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=my_orders_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "my_orders:active")
async def my_orders_active(cb: CallbackQuery):
    await cb.answer()
    user_id = cb.from_user.id
    orders = await get_user_orders(user_id)
    active_statuses = {"new", "accepted", "assigned", "in_work", "contacted"}
    active = [o for o in orders if o[3] in active_statuses]
    if not active:
        try:
            await cb.message.edit_text("📭 <b>Нет активных заказов</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет активных заказов</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        return
    text = "📌 <b>Активные заказы</b>\n\n"
    b = InlineKeyboardBuilder()
    for o in active:
        order_id, service, data_json, status, created_at, executor_id, date, time = o
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{order_id}</b> | {service}\n"
        text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
        text += f"📍 {data.get('address', '—')}\n"
        text += f"👤 {executor_name}\n"
        text += f"💰 {price}\n"
        text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
    b.button(text="📌 Активные", callback_data="my_orders:active")
    b.button(text="📜 История", callback_data="my_orders:history")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "my_orders:history")
async def my_orders_history(cb: CallbackQuery):
    await cb.answer()
    user_id = cb.from_user.id
    orders = await get_user_orders(user_id)
    history_statuses = {"completed", "cancelled"}
    history = [o for o in orders if o[3] in history_statuses]
    if not history:
        try:
            await cb.message.edit_text("📭 <b>История заказов пуста</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>История заказов пуста</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        return
    text = "📜 <b>История заказов</b>\n\n"
    b = InlineKeyboardBuilder()
    for o in history:
        order_id, service, data_json, status, created_at, executor_id, date, time = o
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{order_id}</b> | {service}\n"
        text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
        text += f"📍 {data.get('address', '—')}\n"
        text += f"👤 {executor_name}\n"
        text += f"💰 {price}\n"
        text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
    b.button(text="📌 Активные", callback_data="my_orders:active")
    b.button(text="📜 История", callback_data="my_orders:history")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("my_orders:cancel:"))
async def my_orders_cancel(cb: CallbackQuery, state: FSMContext):
    order_id = cb.data.split(":", 2)[2]
    data = None
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await cb.answer("Заказ не найден", show_alert=True)
                return
            data = json.loads(row[0])
        client_id = data.get("telegram_id")
        if client_id != cb.from_user.id:
            executor = await get_executor(cb.from_user.id)
            if executor and executor[3]:
                await cb.answer("Исполнитель не может отменить заказ", show_alert=True)
                return
            await cb.answer("Вы не можете отменить этот заказ", show_alert=True)
            return
    await cancel_order(order_id)
    await state.clear()
    if data:
        try:
            await send_order_to_admin(cb.bot, order_id, data, "Уборка квартиры", status="cancelled")
        except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    await cb.answer("Заказ отменён")
    user_id = cb.from_user.id
    orders = await get_user_orders(user_id)
    history_statuses = {"completed", "cancelled"}
    history = [o for o in orders if o[3] in history_statuses]
    text = "📜 <b>История заказов</b>\n\n"
    b = InlineKeyboardBuilder()
    for o in history:
        oid, service, data_json, status, created_at, executor_id, date, time = o
        data = json.loads(data_json)
        text += f"🆕 <b>{oid}</b> | {service}\n"
        text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
        text += f"📍 {data.get('address', '—')}\n"
        text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
        b.button(text=f"📋 Детали {oid}", callback_data=f"my_orders:detail:{oid}")
    b.button(text="📌 Активные", callback_data="my_orders:active")
    b.button(text="📜 История", callback_data="my_orders:history")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(1)
    try:
        await cb.message.edit_text("❌ <b>Заказ отменён</b>\n\n" + text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer("❌ <b>Заказ отменён</b>\n\n" + text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("my_orders:rate:"))
async def my_orders_rate(cb: CallbackQuery):
    order_id = cb.data.split(":", 2)[2]
    try:
        await cb.message.edit_text("⭐ <b>Оцените работу исполнителя</b>\n\nВыберите оценку:", reply_markup=rating_kb(order_id), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("⭐ <b>Оцените работу исполнителя</b>\n\nВыберите оценку:", reply_markup=rating_kb(order_id), parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data.startswith("rating:"))
async def rating_callback(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    order_id = parts[1]
    rating = int(parts[2])
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await cb.answer("Заказ не найден", show_alert=True)
                return
            data = json.loads(row[0])
        async with db.execute("""
            SELECT id FROM ratings WHERE order_id = ? AND client_id = ?
        """, (order_id, cb.from_user.id)) as cur:
            existing = await cur.fetchone()
        if existing:
            await cb.answer("Вы уже оценили этот заказ", show_alert=True)
            return
    executor_id = data.get("executor_id")
    if not executor_id:
        await cb.answer("У заказа пока нет исполнителя", show_alert=True)
        return
    await state.update_data(rating_order_id=order_id, rating=rating)
    await state.set_state(RatingStates.comment)
    try:
        await cb.message.edit_text("⭐ <b>Напишите текстовый отзыв о работе исполнителя</b>\n\nЕсли не хотите — отправьте «-»", parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("⭐ <b>Напишите текстовый отзыв о работе исполнителя</b>\n\nЕсли не хотите — отправьте «-»", parse_mode="HTML")
    await cb.answer()


@dp.message(RatingStates.comment)
async def rating_comment_handler(msg: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("rating_order_id")
    rating = data.get("rating")
    comment = (msg.text or "").strip()
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if row:
                order_data = json.loads(row[0])
                executor_id = order_data.get("executor_id")
                if executor_id:
                    await save_rating(order_id, msg.from_user.id, executor_id, rating, comment)
    await state.clear()
    await msg.answer(RATING_THANKS, parse_mode="HTML", reply_markup=main_menu())


@dp.callback_query(F.data.startswith("my_orders:repeat:"))
async def my_orders_repeat(cb: CallbackQuery, state: FSMContext):
    order_id = cb.data.split(":", 2)[2]
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await cb.answer("Заказ не найден", show_alert=True)
                return
            data = json.loads(row[0])
    await state.clear()
    await state.update_data(
        cleaning_type=data.get("cleaning_type"),
        flat_type=data.get("flat_type"),
        metro=data.get("metro"),
        address=data.get("address"),
        phone=data.get("phone"),
        comment=data.get("comment"),
        photos=[],
        name=data.get("name"),
        date=data.get("date"),
        time=data.get("time"),
    )
    await state.set_state(CleaningOrder.preview)
    text = await build_preview_text(await state.get_data())
    b = InlineKeyboardBuilder()
    b.button(text="✅ Продолжить с этими данными", callback_data="repeat:confirm")
    b.button(text="✏️ Изменить", callback_data="repeat:edit")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


# ================= EXECUTOR INTERFACE =================

@dp.message(Command("executor"))
@dp.message(F.text == "👷 Исполнитель")
async def executor_start(msg: Message, state: FSMContext):
    executor = await get_executor(msg.from_user.id)
    if not executor:
        await msg.answer(
            "👷 <b>Регистрация исполнителя</b>\n\n"
            "Введите ваше имя:",
            parse_mode="HTML"
        )
        await state.set_state(ExecutorStates.registration_name)
        return
    if not executor[3]:
        await msg.answer("❌ Ваша учетная запись исполнителя деактивирована. Обратитесь к администратору.", reply_markup=main_menu())
        return
    await state.clear()
    await msg.answer(EXECUTOR_WELCOME, reply_markup=executor_menu_kb(), parse_mode="HTML")
    await state.set_state(ExecutorStates.menu)


@dp.message(ExecutorStates.registration_name)
async def executor_registration_name(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    await state.update_data(executor_name=msg.text.strip())
    await state.set_state(ExecutorStates.registration_phone)
    await msg.answer("📱 Введите номер телефона:")


@dp.message(ExecutorStates.registration_phone)
async def executor_registration_phone(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    name = (await state.get_data()).get("executor_name", "")
    await add_executor(msg.from_user.id, name, msg.text.strip())
    await state.clear()
    await msg.answer("✅ <b>Регистрация завершена!</b>\n\nОжидайте активации администратором.", parse_mode="HTML", reply_markup=main_menu())


@dp.message(F.text == "📋 Мои заказы", ExecutorStates.menu)
async def executor_orders_list(msg: Message, state: FSMContext):
    executor = await get_executor(msg.from_user.id)
    if not executor or not executor[3]:
        await msg.answer("❌ У вас нет доступа.", reply_markup=main_menu())
        return
    orders = await get_orders_by_executor(msg.from_user.id)
    if not orders:
        await msg.answer("📭 <b>Нет назначенных заказов</b>", parse_mode="HTML", reply_markup=executor_menu_kb())
        return
    text = "📋 <b>Мои заказы</b>\n\n"
    b = InlineKeyboardBuilder()
    for o in orders:
        order_id, service, data_json, status, created_at, date, time = o
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        text += f"🆕 <b>{order_id}</b> | {service}\n"
        text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
        text += f"📍 {data.get('address', '—')}\n"
        text += f"👤 {data.get('fio') or data.get('full_name') or data.get('name') or '—'}\n"
        text += f"📞 {data.get('phone', '—')}\n"
        text += f"💬 {data.get('comment', '—') or '—'}\n"
        text += f"💰 {price}\n"
        text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
    b.button(text="🔙 В главное меню", callback_data="my_orders:back")
    b.adjust(1)
    await msg.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("exec:status:"))
async def executor_update_status(cb: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != ExecutorStates.menu.state:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    parts = cb.data.split(":")
    order_id = parts[2]
    new_status = parts[3]
    current = await get_status(order_id)
    if current in ("completed", "cancelled"):
        await cb.answer("Заказ завершён или отменён", show_alert=True)
        return
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT executor_id FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] != cb.from_user.id:
                await cb.answer("Нет доступа к этому заказу", show_alert=True)
                return
    await update_status(order_id, new_status)
    data = None
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if row:
                data = json.loads(row[0])
                if new_status == "completed":
                    client_id = data.get("telegram_id")
                    if client_id:
                        try:
                            await cb.bot.send_message(
                                client_id,
                                LTV_AFTER_ORDER,
                                parse_mode="HTML"
                            )
                        except Exception as e:
                            logger.warning("Operation failed", exc_info=True)
    if data:
        await send_order_to_admin(cb.bot, order_id, data, "Уборка квартиры", status=new_status)
    await cb.answer(f"Статус обновлён: {STATUS_LABELS.get(new_status, new_status)}")
    order = await get_status(order_id)
    if order:
        async with aiosqlite.connect("orders.db") as db:
            async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    data = json.loads(row[0])
                    text = EXECUTOR_ORDER_TEMPLATE.format(
                        service=data.get("cleaning_type", "—"),
                        flat_type=data.get("flat_type", "—"),
                        status=STATUS_LABELS.get(order, order),
                        order_id=order_id,
                        date=data.get("date", "—"),
                        time=data.get("time", "—"),
                        address=data.get("address", "—"),
                        metro=data.get("metro", "—"),
                        phone=data.get("phone", "—"),
                        fio=data.get("fio") or data.get("full_name") or data.get("name") or "—",
                        name=safe_str(data.get("name")),
                        comment=data.get("comment", "—") or "—"
                    )
                    photos = await get_order_photos(order_id)
                    try:
                        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=executor_order_actions_kb(order_id, order))
                        if photos:
                            media = []
                            for p in photos[:5]:
                                media.append(InputMediaPhoto(media=p[1]))
                            await cb.message.answer_media_group(media)
                    except Exception as e:
                        logger.warning("Operation failed", exc_info=True)
                        try:
                            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=executor_order_actions_kb(order_id, order))
                        except TelegramBadRequest:
                            await cb.message.answer(text, parse_mode="HTML", reply_markup=executor_order_actions_kb(order_id, order))
                        if photos:
                            for p in photos[:5]:
                                await cb.message.answer_photo(photo=p[1])


@dp.callback_query(F.data.startswith("exec:photo:"))
async def executor_request_photo(cb: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    if current_state != ExecutorStates.menu.state:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT executor_id FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] != cb.from_user.id:
                await cb.answer("Нет доступа к этому заказу", show_alert=True)
                return
    await state.update_data(uploading_photos_to=order_id)
    try:
        await cb.message.edit_text("📸 <b>Отправьте фотографии результата уборки</b>\n\nОтправляйте по одной или группой:", parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("📸 <b>Отправьте фотографии результата уборки</b>\n\nОтправляйте по одной или группой:", parse_mode="HTML")
    await cb.answer()


@dp.message(F.photo, ExecutorStates.menu)
async def executor_upload_photo(msg: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != ExecutorStates.menu.state:
        await msg.answer("Сначала войдите в панель исполнителя")
        return
    data = await state.get_data()
    order_id = data.get("uploading_photos_to")
    if not order_id:
        await msg.answer("❌ Сначала выберите заказ и нажмите «Отправить фото результата».")
        return
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT executor_id FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row or row[0] != msg.from_user.id:
                await msg.answer("❌ Нет доступа к этому заказу.")
                return
    photo = msg.photo[-1]
    await save_order_photo(order_id, msg.from_user.id, photo.file_id, "result")
    await msg.answer("✅ Фото сохранено. Отправьте ещё или нажмите кнопку ниже.", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад к заказам", callback_data="exec:orders:back")).as_markup())


@dp.callback_query(F.data == "exec:orders:back")
async def executor_orders_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(uploading_photos_to=None)
    executor = await get_executor(cb.from_user.id)
    if executor and executor[3]:
        await cb.message.answer(EXECUTOR_WELCOME, reply_markup=executor_menu_kb(), parse_mode="HTML")
    else:
        await cb.message.answer("Главное меню", reply_markup=main_menu())


# ================= MY ORDERS DETAIL =================

@dp.callback_query(F.data.startswith("my_orders:detail:"))
async def my_orders_detail(cb: CallbackQuery):
    order_id = cb.data.split(":", 2)[2]
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json, status FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await cb.answer("Заказ не найден", show_alert=True)
                return
            data = json.loads(row[0])
            status = row[1]
    has_rating = await get_order_rating(order_id) is not None
    flat_type = data.get("flat_type", "—")
    price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
    executor_id = data.get("executor_id")
    executor_name = "—"
    if executor_id:
        ex = await get_executor(executor_id)
        if ex:
            executor_name = ex[1] or ex[0]
    text = (
        f"📋 <b>Заказ | FYN Clean</b>\n\n"
        f"<b>Услуга:</b> {data.get('cleaning_type', '—')}\n"
        f"<b>Тип квартиры:</b> {flat_type}\n"
        f"<b>Статус:</b> {STATUS_LABELS.get(status, status)}\n"
        f"<b>ID:</b> {order_id}\n\n"
        f"<b>Дата:</b> {data.get('date', '—')}\n"
        f"<b>Время:</b> {data.get('time', '—')}\n"
        f"<b>Адрес:</b> {data.get('address', '—')}\n"
        f"<b>Метро:</b> {data.get('metro', '—')}\n"
        f"<b>Телефон:</b> {data.get('phone', '—')}\n"
        f"<b>Контакт:</b> {safe_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Имя/ФИО:</b> {safe_str(data.get('name'))}\n"
        f"<b>Комментарий:</b> {data.get('comment', '—') or '—'}\n"
        f"<b>Исполнитель:</b> {executor_name}\n"
        f"<b>Стоимость:</b> {price}\n"
    )
    photos = await get_order_photos(order_id)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=order_detail_kb(order_id, status, has_rating, executor_id))
        if photos:
            for p in photos[:5]:
                await cb.message.answer_photo(photo=p[1])
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await cb.message.answer(text, parse_mode="HTML", reply_markup=order_detail_kb(order_id, status, has_rating, executor_id))
        if photos:
            for p in photos[:5]:
                await cb.message.answer_photo(photo=p[1])
    await cb.answer()


@dp.callback_query(F.data.startswith("executor:rating:"))
async def executor_rating_view(cb: CallbackQuery):
    try:
        executor_id = int(cb.data.split(":", 2)[2])
    except (ValueError, IndexError):
        await cb.answer("Некорректный идентификатор исполнителя", show_alert=True)
        return
    avg_data = await get_executor_avg_rating(executor_id)
    avg = avg_data.get("avg") if avg_data else None
    executor = await get_executor(executor_id)
    name = executor[1] if executor else str(executor_id)
    reviews = await get_executor_ratings(executor_id)
    text = f"⭐ <b>Рейтинг исполнителя</b>\n\n👤 {name}\n📊 Средний балл: {avg if avg is not None else '—'}\n\n"
    if reviews:
        text += "<b>Отзывы:</b>\n\n"
        for r in reviews[:10]:
            rating, comment, created_at, data_json, client_name = r
            text += f"⭐ {rating}/5 — {client_name or 'Клиент'}\n"
            text += f"💬 {comment or '—'}\n"
            text += f"📅 {created_at}\n\n"
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


# ================= ADMIN PANEL =================

@dp.message(Command("admin"))
async def admin_panel(msg: Message):
    if msg.from_user.id != cfg.admin_chat_id:
        await msg.answer("❌ У вас нет доступа к админ-панели.")
        return
    await msg.answer("👑 <b>Админ-панель FYN Clean</b>\n\nВыберите раздел:", reply_markup=admin_orders_filter_kb(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin:orders:"))
async def admin_orders_list(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    status_filter = cb.data.split(":", 2)[2]
    status_map = {
        "new": "new",
        "active": "active",
        "completed": "completed",
        "cancelled": "cancelled"
    }
    target_status = status_map.get(status_filter)
    if target_status == "active":
        async with aiosqlite.connect("orders.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, service, data_json, status, created_at, executor_id, date, time
                FROM orders
                WHERE status IN ('accepted','assigned','in_work','contacted')
                ORDER BY created_at DESC
                LIMIT 50
            """) as cur:
                orders = await cur.fetchall()
    elif target_status:
        orders = await get_orders_by_status(target_status)
    else:
        async with aiosqlite.connect("orders.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, service, data_json, status, created_at FROM orders ORDER BY created_at DESC LIMIT 50") as cur:
                orders = await cur.fetchall()
    if not orders:
        try:
            await cb.message.edit_text("📭 <b>Нет заказов</b>", parse_mode="HTML", reply_markup=admin_orders_filter_kb())
        except TelegramBadRequest:
            pass
        return
    text = f"📋 <b>Заказы</b> ({status_filter})\n\n"
    b = InlineKeyboardBuilder()
    for o in orders:
        if isinstance(o, aiosqlite.Row):
            order_id = o["id"]
            data_json = o["data_json"]
            status = o["status"]
        else:
            order_id, service, data_json, status, created_at = o[:5]
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_id = data.get("executor_id")
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{order_id}</b> | {data.get('cleaning_type', '—')}\n"
        text += f"📅 {data.get('date', '—')} ⏰ {data.get('time', '—')}\n"
        text += f"📍 {data.get('address', '—')}\n"
        text += f"👤 {data.get('fio', '—')}\n"
        text += f"📞 {data.get('phone', '—')}\n"
        text += f"👷 {executor_name}\n"
        text += f"💰 {price}\n"
        text += f"📊 {STATUS_LABELS.get(status, status)}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"admin:order:{order_id}")
    b.button(text="🆕 Новые", callback_data="admin:orders:new")
    b.button(text="📌 Активные", callback_data="admin:orders:active")
    b.button(text="✅ Выполненные", callback_data="admin:orders:completed")
    b.button(text="❌ Отменённые", callback_data="admin:orders:cancelled")
    b.button(text="📊 Статистика", callback_data="admin:stats")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


async def _render_admin_order_detail(cb: CallbackQuery, order_id: str):
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json, status FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                return
            data = json.loads(row[0])
            status = row[1]
    flat_type = data.get("flat_type", "—")
    price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
    executor_id = data.get("executor_id")
    executor_name = "—"
    if executor_id:
        ex = await get_executor(executor_id)
        if ex:
            executor_name = ex[1] or ex[0]
    text = (
        f"📋 <b>Заказ | FYN Clean</b>\n\n"
        f"<b>Услуга:</b> {data.get('cleaning_type', '—')}\n"
        f"<b>Тип квартиры:</b> {flat_type}\n"
        f"<b>Статус:</b> {STATUS_LABELS.get(status, status)}\n"
        f"<b>ID:</b> {order_id}\n\n"
        f"<b>Дата:</b> {data.get('date', '—')}\n"
        f"<b>Время:</b> {data.get('time', '—')}\n"
        f"<b>Адрес:</b> {data.get('address', '—')}\n"
        f"<b>Метро:</b> {data.get('metro', '—')}\n"
        f"<b>Телефон:</b> {data.get('phone', '—')}\n"
        f"<b>Контакт:</b> {safe_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Имя/ФИО:</b> {safe_str(data.get('name'))}\n"
        f"<b>Комментарий:</b> {data.get('comment', '—') or '—'}\n"
        f"<b>Telegram:</b> {data.get('username', '—')}\n"
        f"<b>Исполнитель:</b> {executor_name}\n"
        f"<b>Стоимость:</b> {price}\n"
    )
    photos = await get_order_photos(order_id)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=admin_status_kb(order_id))
        if photos:
            for p in photos[:5]:
                await cb.message.answer_photo(photo=p[1])
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await cb.message.answer(text, parse_mode="HTML", reply_markup=admin_status_kb(order_id))
        if photos:
            for p in photos[:5]:
                await cb.message.answer_photo(photo=p[1])


@dp.callback_query(F.data.startswith("admin:order:"))
async def admin_order_detail(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json, status FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if not row:
                await cb.answer("Заказ не найден", show_alert=True)
                return
    await _render_admin_order_detail(cb, order_id)
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:assign_executor:"))
async def admin_show_assign_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    executors = await get_all_executors()
    if not executors:
        await cb.answer("Нет доступных исполнителей", show_alert=True)
        return
    try:
        await cb.message.edit_text("👤 <b>Выберите исполнителя:</b>", parse_mode="HTML", reply_markup=executor_assign_kb(order_id, executors))
    except TelegramBadRequest:
        await cb.message.answer("👤 <b>Выберите исполнителя:</b>", parse_mode="HTML", reply_markup=executor_assign_kb(order_id, executors))
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:status:"))
async def admin_status(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    order_id = parts[2]
    status = parts[3]
    await update_status(order_id, status)
    data = None
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if row:
                data = json.loads(row[0])
                if status == "completed":
                    client_id = data.get("telegram_id")
                    if client_id:
                        try:
                            await cb.bot.send_message(client_id, LTV_AFTER_ORDER, parse_mode="HTML")
                        except Exception as e:
                            logger.warning("Operation failed", exc_info=True)
                elif status == "cancelled":
                    client_id = data.get("telegram_id")
                    if client_id:
                        try:
                            await cb.bot.send_message(client_id, ORDER_CANCELLED_TEXT, parse_mode="HTML")
                        except Exception as e:
                            logger.warning("Operation failed", exc_info=True)
    if data:
        await send_order_to_admin(cb.bot, order_id, data, "Уборка квартиры", status=status)
    await _render_admin_order_detail(cb, order_id)
    await cb.answer(f"Статус: {STATUS_LABELS.get(status, status)}")


@dp.callback_query(F.data.startswith("admin:assign:"))
async def admin_assign_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    order_id = parts[2]
    executor_id = int(parts[3])
    current = await get_status(order_id)
    if current in ("completed", "cancelled"):
        await cb.answer("Нельзя назначить исполнителя на завершённый или отменённый заказ", show_alert=True)
        return
    await assign_executor(order_id, executor_id)
    executor = await get_executor(executor_id)
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT data_json FROM orders WHERE id = ?", (order_id,)) as cur:
            row = await cur.fetchone()
            if row:
                data = json.loads(row[0])
                await notify_executor(cb.bot, order_id, {**data, "executor_id": executor_id})
                await send_order_to_admin(cb.bot, order_id, data, "Уборка квартиры", status="assigned")
    await _render_admin_order_detail(cb, order_id)
    await cb.answer(f"Исполнитель назначен: {executor[1] if executor else executor_id}")


@dp.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    async with aiosqlite.connect("orders.db") as db:
        async with db.execute("SELECT COUNT(*) FROM orders") as cur:
            total = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'new'") as cur:
            new = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM orders WHERE status IN ('accepted', 'assigned', 'in_work', 'contacted')") as cur:
            active = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'completed'") as cur:
            completed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM orders WHERE status = 'cancelled'") as cur:
            cancelled = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM executors") as cur:
            executors = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM ratings") as cur:
            ratings = (await cur.fetchone())[0]
        async with db.execute("SELECT AVG(rating) FROM ratings") as cur:
            avg_rating_row = await cur.fetchone()
            avg_rating = round(avg_rating_row[0], 1) if avg_rating_row and avg_rating_row[0] else "—"
    text = ADMIN_STATS_TEXT.format(
        total=total,
        new=new,
        active=active,
        completed=completed,
        cancelled=cancelled,
        executors=executors,
        ratings=ratings,
        avg_rating=avg_rating
    )
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=admin_orders_filter_kb())
    except TelegramBadRequest:
        pass
    await cb.answer()


@dp.callback_query(F.data == "admin:executors")
async def admin_executors_list(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    executors = await get_all_executors()
    if not executors:
        await cb.message.edit_text("👥 <b>Нет исполнителей</b>", parse_mode="HTML", reply_markup=admin_orders_filter_kb())
        await cb.answer()
        return
    text = "👥 <b>Исполнители</b>\n\n"
    b = InlineKeyboardBuilder()
    for ex in executors:
        telegram_id, full_name, phone, is_active = ex
        status = "✅ Активен" if is_active else "❌ Деактивирован"
        text += f"👤 <b>{full_name or telegram_id}</b>\n"
        text += f"📞 {phone or '—'}\n"
        text += f"📊 {status}\n\n"
        b.button(text=f"{'❌ Деактивировать' if is_active else '✅ Активировать'} {full_name or telegram_id}", callback_data=f"admin:toggle_executor:{telegram_id}")
        b.button(text=f"⭐ Рейтинг {full_name or telegram_id}", callback_data=f"admin:executor:rating:{telegram_id}")
    b.button(text="🔙 Назад", callback_data="admin:orders:new")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:toggle_executor:"))
async def admin_toggle_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    executor_id = int(cb.data.split(":", 2)[2])
    executor = await get_executor(executor_id)
    if not executor:
        await cb.answer("Исполнитель не найден", show_alert=True)
        return
    new_status = not bool(executor[3])
    await toggle_executor_active(executor_id, new_status)
    executors = await get_all_executors()
    text = "👥 <b>Исполнители</b>\n\n"
    b = InlineKeyboardBuilder()
    for ex in executors:
        telegram_id, full_name, phone, is_active = ex
        status = "✅ Активен" if is_active else "❌ Деактивирован"
        text += f"👤 <b>{full_name or telegram_id}</b>\n"
        text += f"📞 {phone or '—'}\n"
        text += f"📊 {status}\n\n"
        b.button(text=f"{'❌ Деактивировать' if is_active else '✅ Активировать'} {full_name or telegram_id}", callback_data=f"admin:toggle_executor:{telegram_id}")
        b.button(text=f"⭐ Рейтинг {full_name or telegram_id}", callback_data=f"admin:executor:rating:{telegram_id}")
    b.button(text="🔙 Назад", callback_data="admin:orders:new")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer(f"Статус обновлён: {'Активен' if new_status else 'Деактивирован'}")


@dp.callback_query(F.data.startswith("admin:executor:rating:"))
async def admin_executor_rating(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    executor_id = int(cb.data.split(":", 3)[3])
    avg = await get_executor_avg_rating(executor_id)
    executor = await get_executor(executor_id)
    name = executor[1] if executor else executor_id
    text = f"⭐ <b>Рейтинг исполнителя</b>\n\n👤 {name}\n📊 Средний балл: {avg if avg is not None else '—'}"
    b = InlineKeyboardBuilder()
    b.button(text="💬 Отзывы", callback_data=f"admin:executor:reviews:{executor_id}")
    b.button(text="🔙 Назад", callback_data="admin:executors")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:executor:reviews:"))
async def admin_executor_reviews(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    executor_id = int(cb.data.split(":", 3)[3])
    reviews = await get_executor_ratings(executor_id)
    executor = await get_executor(executor_id)
    name = executor[1] if executor else executor_id
    if not reviews:
        text = f"💬 <b>Отзывы исполнителя</b>\n\n👤 {name}\nПока нет отзывов."
    else:
        text = f"💬 <b>Отзывы исполнителя</b>\n\n👤 {name}\n\n"
        for r in reviews[:20]:
            rating, comment, created_at, data_json, client_name = r
            text += f"⭐ {rating}/5\n"
            text += f"💬 {comment or '—'}\n"
            text += f"📅 {created_at}\n\n"
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data=f"admin:executor:rating:{executor_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.message(F.text == "🔙 В главное меню")
async def back_to_main_menu(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Главное меню", reply_markup=main_menu())


@dp.message(F.text == "❌ Отменить заказ")
async def cancel_order_handler(msg: Message, state: FSMContext):
    current = await state.get_state()
    if current and current.startswith("CleaningOrder"):
        await state.clear()
        await msg.answer(ORDER_CANCELLED_TEXT, reply_markup=main_menu())
    elif current == ExecutorStates.menu.state:
        await state.clear()
        await msg.answer("Возврат в главное меню", reply_markup=main_menu())
    else:
        await msg.answer("❌ Нет активного заказа для отмены.", reply_markup=main_menu())


@dp.message(F.text == "🏠 Индивидуальная уборка")
async def individual_cleaning_info(msg: Message):
    try:
        await msg.answer_photo(
            photo=FSInputFile(INDIVIDUAL_CLEANING_PHOTO),
            caption=INDIVIDUAL_CLEANING_TEXT,
            parse_mode="HTML",
            reply_markup=individual_cleaning_kb()
        )
    except Exception as e:
        await msg.answer(INDIVIDUAL_CLEANING_TEXT, parse_mode="HTML", reply_markup=individual_cleaning_kb())
        print(f"Ошибка фото индивидуальной уборки: {e}")


# ================= MAIN =================
async def check_abandoned_orders(bot):

    orders = await get_abandoned_orders()

    print(
        f"🕓 Брошенных заявок найдено: {len(orders)}"
    )

    for order in orders:

        try:
            if telethon_client:
                await telethon_client.send_message(
                    order["telegram_id"],
                    "👋 Вы начали оформление уборки, "
                    "но не завершили заявку.\n\n"
                    f"Вы выбирали: {order['cleaning_type']} 🧹\n\n"
                    "Продолжить оформление?",
                    buttons=continue_order_kb()
                )
            else:
                await bot.send_message(
                    order["telegram_id"],
                    "👋 Вы начали оформление уборки, "
                    "но не завершили заявку.\n\n"
                    f"Вы выбирали: {order['cleaning_type']} 🧹\n\n"
                    "Продолжить оформление?",
                    reply_markup=continue_order_kb()
                )

            await mark_abandoned_reminded(
                order["telegram_id"]
            )

            print(
                f"✅ Напоминание отправлено {order['telegram_id']}"
            )

        except Exception as e:

            print(
                f"❌ Ошибка отправки: {e}"
            )


async def main():
    global telethon_client

    print("⚙️ Запуск бота...")
    print("⚙️ Инициализация базы данных...")
    await init_db()

    bot = Bot(
        token=cfg.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    telethon_client = None
    if cfg.session_string and cfg.telegram_api_id and cfg.telegram_api_hash:
        try:
            telethon_client = TelegramClient(
                StringSession(cfg.session_string),
                cfg.telegram_api_id,
                cfg.telegram_api_hash
            )
            await telethon_client.start()
            print("✅ Telethon клиент запущен")
        except Exception as e:
            print(f"❌ Ошибка запуска Telethon: {e}")
            telethon_client = None
    else:
        print("ℹ️ Telethon не настроен")

    print("🚀 FYN Clean Bot запущен")
    print("🔍 Проверяю подключение к Telegram...")

    try:
        me = await bot.get_me()
        print(f"✅ Подключен бот: @{me.username}")
        print("🚀 Запускаю polling...")
    except Exception as e:
        print(f"❌ Ошибка подключения Telegram: {e}")
        await bot.session.close()
        return


    scheduler = AsyncIOScheduler()


    scheduler.add_job(
        check_abandoned_orders,
        trigger="interval",
        minutes=3,
        args=[bot],
        id="abandoned_orders",
        replace_existing=True
    )


    scheduler.add_job(
        send_reminders,
        trigger="interval",
        hours=24,
        args=[bot],
        id="test_reminder",
        replace_existing=True
    )


    scheduler.start()

    print("🔔 Автонапоминания включены")
    print("🚀 Запускаю polling...")

    try:
        await dp.start_polling(bot)

    finally:
        print("🛑 Остановка бота")

        scheduler.shutdown()

        await bot.session.close()

        if telethon_client:
            try:
                await telethon_client.disconnect()
            except Exception as e:
                logger.warning("Operation failed", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
