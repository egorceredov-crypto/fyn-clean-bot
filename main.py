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
from html import escape
from urllib.parse import urlparse
import aiosqlite

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from pydantic_core import ValidationError
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, Contact, InputMediaPhoto
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import load_config
from texts import (
    WELCOME, CONTACT, FAQ_SHORT, OUT_OF_HOURS, PRICE_CLEANING, PRICE_DRY,
    ABOUT_TEXT, REVIEWS_TEXT, CONTACT_TEXT, SUPPORT1, INDIVIDUAL_CLEANING_TEXT,
    STATUS_LABELS, ORDER_PREVIEW_TEXT, ORDER_CREATED_TEXT, ORDER_CANCELLED_TEXT,
    ORDER_COMPLETED_TEXT, RATING_THANKS, MY_ORDERS_EMPTY, EXECUTOR_WELCOME, ADMIN_STATS_TEXT,
    EXECUTOR_ORDER_TEMPLATE, LTV_AFTER_ORDER, LTV_REMINDER, SERVICES_TEXT,
    INDIVIDUAL_CLEANING_WHAT_INCLUDED
)
from keyboards import (
    cleaning_type_kb, flat_type_kb, name_kb, main_menu, metro_lines_kb, metro_stations_kb,
    confirm_kb, admin_status_kb, order_cancel_kb, order_step_kb, date_selection_kb,
    time_selection_kb, phone_request_kb, photo_kb, preview_kb, edit_field_kb,
    my_orders_kb, order_detail_kb, executor_order_actions_kb, rating_kb,
    individual_cleaning_kb, admin_orders_filter_kb, executor_assign_kb,
    executor_menu_kb, admin_executor_manage_kb, admin_executor_add_kb,
    executor_edit_field_kb
)
from states import CleaningOrder, ExecutorStates, RatingStates, AdminStates

MENU_TEXTS = {
    "✨ Заказать уборку",
    "🧹 Услуги и цены",
    "📋 Мои заказы",
    "👤 Мой профиль",
    "✨ О сервисе",
    "🎁 Пригласить друга",
    "🌟 Отзывы",
    "💬 Поддержка",
    "📞 Связаться",
}

async def _process_menu_command(msg: "Message", state: "FSMContext"):
    text = msg.text
    if text not in MENU_TEXTS:
        return False
    current = await state.get_state()
    if current and _is_cleaning_state(current):
        await msg.answer("Завершите оформление или отмените его, чтобы вернуться в меню.")
        return True
    await state.clear()
    if text == "📋 Мои заказы":
        await my_orders(msg, state)
    elif text == "🧹 Услуги и цены":
        await services_info(msg)
    elif text == "✨ Заказать уборку":
        await start_order(msg, state)
    elif text == "👤 Мой профиль":
        await my_profile(msg)
    elif text == "✨ О сервисе":
        await about(msg)
    elif text == "🎁 Пригласить друга":
        await referral(msg)
    elif text == "🌟 Отзывы":
        await reviews(msg)
    elif text == "💬 Поддержка":
        await support(msg)
    elif text == "📞 Связаться":
        await contact(msg)
    return True
from metro_data import METRO
from db import (
    init_db, create_order, update_status, get_status, upsert_user,
    get_user_profile, inc_cleaning_orders, get_users_for_reminder,
    mark_reminder_sent, get_or_create_referral_code, save_abandoned_order,
    delete_abandoned_order, get_abandoned_orders, mark_abandoned_reminded,
    add_executor, get_all_executors, get_executor, toggle_executor_active,
    assign_executor, save_rating, get_order_rating, get_executor_avg_rating,
    get_executor_ratings, save_order_photo, get_order_photos, get_orders_by_status,
    get_orders_by_executor, get_user_orders, get_order,
    get_active_executors, unassign_executor, DB_PATH
)
from db import can_transition_status, transition_status
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
    users = await get_users_for_reminder(30)
    print(f"🔔 Найдено клиентов: {len(users)}")
    for user in users:
        telegram_id = user[0]
        sent = False
        try:
            await bot.send_message(
                telegram_id,
                LTV_REMINDER,
                parse_mode="HTML",
                reply_markup=reminder_kb(),
            )
            sent = True
        except Exception:
            if telethon_client:
                try:
                    # Telethon cannot consume aiogram callback markup, so the
                    # fallback is intentionally informational only.
                    await telethon_client.send_message(
                        telegram_id,
                        LTV_REMINDER,
                        parse_mode="HTML",
                    )
                    sent = True
                except Exception:
                    logger.warning(
                        "Failed to send reminder to %s",
                        telegram_id,
                        exc_info=True,
                    )
            else:
                logger.warning(
                    "Failed to send reminder to %s",
                    telegram_id,
                    exc_info=True,
                )
        if sent:
            await mark_reminder_sent(telegram_id)
            print(f"✅ Напоминание отправлено: {telegram_id}")


# Путь к твоему фото (поменяй на реальный!)
INDIVIDUAL_CLEANING_PHOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "individual_cleaning.jpg")

PHOTO_FLAT_TYPES = {
    "Поддерживающая уборка": "https://i.ibb.co/MkFY1ZYh/out-clean.jpg",
    "Генеральная уборка": "https://i.ibb.co/N2hXVJDk/Chat-GPT-Image-17-2026-00-25-57.png",
    "После ремонта": "https://i.ibb.co/mFX6JpHN/Chat-GPT-Image-5-2026-23-46-52.png",
    "Химчистка мебели": "https://i.ibb.co/QFDRxV5j/ggd.png",
    "Индивидуальная уборка": INDIVIDUAL_CLEANING_PHOTO,
}

SERVICE_INFO = {
    "Поддерживающая уборка": {
        "desc": "Регулярная уборка: пылесос, влажная уборка, кухня, санузел.",
        "price": "1-комнатная, 25–45 м² — от 10 500 до 13 500 ₽.",
    },
    "Генеральная уборка": {
        "desc": "Детальная уборка, включая окна, кухню, санузел, труднодоступные места.",
        "price": "Цена по согласованию.",
    },
    "После ремонта": {
        "desc": "Уборка пыли и следов работ, тщательная очистка поверхностей.",
        "price": "Цена по согласованию.",
    },
    "Химчистка мебели": {
        "desc": "Профессиональный Karcher + безопасная химия для ткани.",
        "price": "Цена по согласованию.",
    },
}

cfg = load_config()
dp = Dispatcher()
logger = logging.getLogger(__name__)

# Таймаут проверки доступности прокси перед стартом (секунды)
PROXY_PROBE_TIMEOUT = 5

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


def html_str(value, default="—"):
    return escape(safe_str(value, default), quote=False)


def normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return ""
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if digits.startswith("7") and len(digits) == 11:
        return f"+{digits}"
    if len(digits) >= 10:
        return f"+{digits[-10:]}"
    return f"+{digits}"


def is_valid_phone(value: str) -> bool:
    normalized = normalize_phone(value)
    return bool(normalized) and len(normalized) == 12 and normalized.startswith("+7")


def same_telegram_id(value, user_id) -> bool:
    return value is not None and str(value) == str(user_id)



async def get_owned_order(order_id: str, user_id: int):
    order = await get_order(order_id)
    if not order or not same_telegram_id(order["data"].get("telegram_id"), user_id):
        return None
    return order


async def get_assigned_executor_order(order_id: str, user_id: int):
    executor = await get_executor(user_id)
    if not executor or not executor[4]:
        return None
    order = await get_order(order_id)
    if not order:
        return None
    assigned_id = order["executor_id"] or order["data"].get("executor_id")
    if not same_telegram_id(assigned_id, user_id):
        return None
    return order


def is_order_modifiable(status: str) -> bool:
    return status not in {"completed", "cancelled"}


def _state_name(state) -> str | None:
    if state is None:
        return None
    return state.state if hasattr(state, "state") else str(state)


def _is_cleaning_state(current) -> bool:
    name = _state_name(current)
    return bool(name and name.startswith("CleaningOrder"))


async def get_order_for_mutation(order_id: str, cb=None):
    if cb and not hasattr(cb, "message"):
        return None
    order = await get_order(order_id)
    if not order:
        if cb:
            try:
                await cb.answer("Заказ не найден", show_alert=True)
            except Exception:
                pass
        return None
    if not is_order_modifiable(order["status"]):
        if cb:
            try:
                await cb.answer("Статус заказа уже окончательный", show_alert=True)
            except Exception:
                pass
        return None
    return order


async def assert_order_accessible(order_id: str, user_id: int, cb=None):
    if cb and not hasattr(cb, "message"):
        return None
    order = await get_order(order_id)
    if not order:
        if cb:
            try:
                await cb.answer("Заказ не найден", show_alert=True)
            except Exception:
                pass
        return None
    if not same_telegram_id(order["data"].get("telegram_id"), user_id):
        if cb:
            try:
                await cb.answer("Вы не можете открыть этот заказ", show_alert=True)
            except Exception:
                pass
        return None
    return order


async def get_order_updated_at(order_id: str) -> str | None:
    order = await get_order(order_id)
    return order.get("updated_at") if order else None



async def notify_client_status(bot: Bot, order_id: str, data: dict, status: str, reason: str = None):
    client_id = data.get("telegram_id")
    if not client_id:
        return
    try:
        if status == "completed":
            await bot.send_message(
                client_id,
                ORDER_COMPLETED_TEXT,
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
            await bot.send_message(
                client_id,
                LTV_AFTER_ORDER,
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        elif status == "cancelled":
            text = ORDER_CANCELLED_TEXT
            if reason:
                text += f"\n\n<b>Причина отмены:</b> {html_str(reason)}"
            await bot.send_message(
                client_id,
                text,
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        else:
            text = (
                f"📊 <b>Статус заказа {html_str(order_id)}</b>\n\n"
                f"{STATUS_LABELS.get(status, html_str(status))}"
            )
            await bot.send_message(
                client_id,
                text,
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
    except Exception:
        logger.warning("Failed to notify client about order status", exc_info=True)


async def notify_result_photo(
    bot: Bot,
    order_id: str,
    data: dict,
    photo_file_id: str,
):
    recipients = []
    for recipient in (data.get("telegram_id"), cfg.admin_chat_id):
        if recipient and recipient not in recipients:
            recipients.append(recipient)
    caption = f"📸 <b>Фото результата по заказу {html_str(order_id)}</b>"
    for recipient in recipients:
        try:
            await bot.send_photo(
                recipient,
                photo=photo_file_id,
                caption=caption,
                parse_mode="HTML",
            )
        except Exception:
            logger.warning(
                "Failed to send result photo to %s",
                recipient,
                exc_info=True,
            )


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
        photos = data.get("photos")
        if isinstance(photos, list):
            photos = json.dumps(photos, ensure_ascii=False)
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
            photos=photos,
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
    if not current:
        await cb.answer("Вы не находитесь в процессе оформления заказа.", show_alert=True)
        return
    if not _is_cleaning_state(current):
        await cb.answer("Вы не находитесь в процессе оформления заказа.", show_alert=True)
        return
    current_state = _state_name(current)
    if current_state == CleaningOrder.cleaning_type.state:
        await cb.message.answer("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb(), parse_mode="HTML")
        return
    prev = ORDER_BACK_MAP.get(current_state)
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
    if not current:
        await msg.answer("Вы не находитесь в процессе оформления заказа.")
        return
    if not _is_cleaning_state(current):
        await msg.answer("Вы не находитесь в процессе оформления заказа.")
        return
    current_state = _state_name(current)
    if current_state == CleaningOrder.cleaning_type.state:
        await msg.answer("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb(), parse_mode="HTML")
        return
    prev = ORDER_BACK_MAP.get(current_state)
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
        except Exception:
            logger.warning("Operation failed", exc_info=True)
        await cb.message.answer(ORDER_CANCELLED_TEXT, parse_mode="HTML", reply_markup=main_menu())
    else:
        await cb.message.answer("❌ Нет активного заказа для отмены.", reply_markup=main_menu())


async def build_preview_text(data: dict) -> str:
    raw_service = data.get("cleaning_type")
    raw_flat_type = data.get("flat_type")
    service = html_str(raw_service)
    date = html_str(data.get("date"))
    time = html_str(data.get("time"))
    address = html_str(data.get("address"))
    phone = html_str(data.get("phone"))
    comment = html_str(data.get("comment"))
    photos = data.get("photos") or []
    photos_count = len(photos) if isinstance(photos, (list, tuple)) else 0
    flat_type = html_str(raw_flat_type)
    name = html_str(data.get("name"))
    price = calc_price_estimate(raw_flat_type or "", raw_service or "")
    return ORDER_PREVIEW_TEXT.format(
        service=service,
        flat_type=flat_type,
        date=date,
        time=time,
        address=address,
        metro=html_str(data.get("metro")),
        phone=phone,
        name=name,
        comment=comment,
        photos_count=photos_count,
        price=price
    )


async def show_preview(msg: Message, state: FSMContext):
    data = await state.get_data()
    text = await build_preview_text(data)
    photos = data.get("photos") or []
    if photos:
        b = InlineKeyboardBuilder()
        for i in range(len(photos)):
            b.button(text=f"🗑 Удалить фото {i + 1}", callback_data=f"order:preview:photo:delete:{i}")
        b.button(text="✅ Подтвердить заказ", callback_data="order:preview:confirm")
        b.button(text="✏️ Редактировать", callback_data="order:preview:edit")
        b.button(text="❌ Отменить", callback_data="order:preview:cancel")
        b.adjust(1)
        kb = b.as_markup()
    else:
        kb = preview_kb()
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")
    if photos:
        try:
            await msg.answer_media_group(
                [InputMediaPhoto(media=photo) for photo in photos[:10]]
            )
        except Exception:
            logger.warning("Failed to send preview photos", exc_info=True)
    await state.set_state(CleaningOrder.preview)


async def send_order_to_admin(bot: Bot, order_id: str, data: dict, service_title: str, status: str = "new"):
    executor_id = data.get("executor_id")
    if executor_id and not data.get("executor_name"):
        ex = await get_executor(executor_id)
        if ex:
            data = {**data, "executor_name": ex[1] or ex[0]}
    text = format_order_text(data, service_title, order_id, status)
    sent = False
    try:
        await bot.send_message(cfg.admin_chat_id, text, parse_mode="HTML")
        sent = True
    except Exception:
        if telethon_client:
            try:
                await telethon_client.send_message(
                    cfg.admin_chat_id,
                    text,
                    parse_mode="HTML",
                )
                sent = True
            except Exception:
                logger.warning("Failed to notify admin", exc_info=True)
        else:
            logger.warning("Failed to notify admin", exc_info=True)


async def notify_executor(bot: Bot, order_id: str, data: dict):
    executor_id = data.get("executor_id")
    if not executor_id:
        return
    text = (
        f"📋 <b>Вам назначен новый заказ</b>\n\n"
        f"<b>Услуга:</b> {html_str(data.get('cleaning_type'))}\n"
        f"<b>Тип квартиры:</b> {html_str(data.get('flat_type'))}\n"
        f"<b>Адрес:</b> {html_str(data.get('address'))}\n"
        f"<b>Метро:</b> {html_str(data.get('metro'))}\n"
        f"<b>Дата:</b> {html_str(data.get('date'))}\n"
        f"<b>Время:</b> {html_str(data.get('time'))}\n"
        f"<b>Телефон клиента:</b> {html_str(data.get('phone'))}\n"
        f"<b>Клиент:</b> {html_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Telegram:</b> {html_str(data.get('username'))}\n"
        f"<b>Комментарий:</b> {html_str(data.get('comment'))}\n\n"
        f"<b>ID заказа:</b> {html_str(order_id)}"
    )
    try:
        await bot.send_message(executor_id, text, parse_mode="HTML")
    except Exception:
        if telethon_client:
            try:
                await telethon_client.send_message(
                    executor_id,
                    text,
                    parse_mode="HTML",
                )
            except Exception:
                logger.warning("Failed to notify executor", exc_info=True)
        else:
            logger.warning("Failed to notify executor", exc_info=True)


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
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        start = datetime.strptime(work_start, "%H:%M").time()
        end = datetime.strptime(work_end, "%H:%M").time()
    except (TypeError, ValueError):
        return False
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
    completed_at = data.get("completed_at")
    completed_line = f"<b>Завершено:</b> {html_str(completed_at)}\n" if completed_at else ""
    return (
        f"{header}\n"
        f"<b>Статус:</b> {html_str(status_label)}\n"
        f"<b>ID:</b> {html_str(order_id)}\n\n"
        f"<b>Услуга:</b> {html_str(service_title)}\n"
        f"<b>Детали:</b> {html_str(data.get('details'))}\n"
        f"<b>Метро:</b> {html_str(data.get('metro'))}\n"
        f"<b>Адрес:</b> {html_str(data.get('address'))}\n"
        f"<b>Дата:</b> {html_str(date)}\n"
        f"<b>Время:</b> {html_str(time)}\n\n"
        f"<b>Контакт:</b> {html_str(fio)}\n"
        f"<b>Телефон:</b> {html_str(data.get('phone'))}\n"
        f"<b>Telegram:</b> {html_str(data.get('username'))}\n"
        f"<b>Комментарий:</b> {html_str(data.get('comment'))}\n"
        f"{completed_line}"
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


@dp.callback_query(F.data == "reminder:later")
async def reminder_later(cb: CallbackQuery, state: FSMContext):
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
    async with aiosqlite.connect(DB_PATH) as db:
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
        photos_json = abandoned.get("photos")
        if photos_json:
            try:
                saved["photos"] = json.loads(photos_json)
            except (TypeError, json.JSONDecodeError):
                saved["photos"] = []
        saved = {k: v for k, v in saved.items() if v is not None}
        await state.update_data(**saved)
        await delete_abandoned_order(telegram_id)

    step_to_state = {
        "выбрал тип уборки": CleaningOrder.flat_type,
        "выбрал тип квартиры": CleaningOrder.date,
        "ввёл тип квартиры": CleaningOrder.date,
        "выбрал дату": CleaningOrder.time,
        "выбрал время": CleaningOrder.metro_station,
        "выбрал линию метро": CleaningOrder.metro_station,
        "выбрал метро": CleaningOrder.address,
        "ввёл адрес": CleaningOrder.phone,
        "ввёл телефон": CleaningOrder.name,
        "ввёл имя/ФИО": CleaningOrder.comment,
        "ввёл комментарий": CleaningOrder.photos,
        "добавил фото": CleaningOrder.photos,
        "пропустил фото": CleaningOrder.preview,
    }
    next_state = step_to_state.get(abandoned.get("step", "") if abandoned else "", CleaningOrder.cleaning_type) if abandoned else CleaningOrder.cleaning_type
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
        restored = await state.get_data()
        metro_line = restored.get("metro_line")
        if metro_line and metro_line in METRO:
            text = f"📍 <b>{metro_line}</b>\n\nВыберите станцию:"
            kb = metro_stations_kb("order", metro_line)
        else:
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

    existing_name = None
    existing_profile = await get_user_profile(msg.from_user.id)
    if existing_profile:
        existing_name = existing_profile.get("name")

    await upsert_user(
        msg.from_user.id,
        username,
        full_name,
        invited_by=referred_by,
        name=existing_name
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


@dp.callback_query(F.data == "repeat:confirm", CleaningOrder.preview)
async def repeat_confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    await confirm_order(cb, state, bot)


@dp.callback_query(F.data == "repeat:edit", CleaningOrder.preview)
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
    patronymic = prof.get("name")
    username = prof["username"] or "—"
    first_seen = prof["first_seen"] or "—"

    text = (
        "👤 <b>Мой профиль | FYN Clean</b>\n\n"
        f"<b>Имя:</b> {name}\n"
    )
    if patronymic:
        text += f"<b>Отчество:</b> {patronymic}\n"
    text += (
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
    if await _process_menu_command(msg, state):
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
    if await _process_menu_command(msg, state):
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
    await cb.answer()
    key = cb.data.split("order:date:", 1)[1]
    if key == "manual":
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
            return
        await state.set_state(CleaningOrder.time)
        try:
            await cb.message.edit_text(f"📅 Выбрано: <b>{label}</b>\n\n⏰ Шаг 4. Выберите время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end), parse_mode="HTML")
        except TelegramBadRequest:
            await cb.message.answer(f"📅 Выбрано: <b>{label}</b>\n\n⏰ Шаг 4. Выберите время:", reply_markup=time_selection_kb(cfg.work_start, cfg.work_end), parse_mode="HTML")
    except Exception as e:
        logger.warning("Operation failed", exc_info=True)
        await cb.answer("Ошибка даты", show_alert=True)


@dp.message(CleaningOrder.date)
async def date_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
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
    await cb.answer()
    key = cb.data.split("order:time:", 1)[1]
    if key == "manual":
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
        return
    await state.set_state(CleaningOrder.metro_station)
    try:
        await cb.message.edit_text(f"⏰ Выбрано: <b>{key}</b>\n\n📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(f"⏰ Выбрано: <b>{key}</b>\n\n📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")


@dp.message(CleaningOrder.time)
async def time_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
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
    if await _process_menu_command(msg, state):
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
        await cb.message.answer("📍 Введите станцию метро текстом:", parse_mode="HTML")
        await state.set_state(CleaningOrder.metro_station)
        await cb.answer()
        return
    try:
        line_idx = int(key)
        line_name = list(METRO.keys())[line_idx]
    except (ValueError, IndexError):
        await cb.answer("Неверная линия метро", show_alert=True)
        return
    await state.update_data(metro_line=line_name, metro_prefix="order")
    await update_abandoned_order(cb.from_user.id, state, "выбрал линию метро")
    try:
        await cb.message.edit_text(f"📍 <b>{line_name}</b>\n\nВыберите станцию:", reply_markup=metro_stations_kb("order", line_name), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer(f"📍 <b>{line_name}</b>\n\nВыберите станцию:", reply_markup=metro_stations_kb("order", line_name), parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data.startswith("order:st:"), CleaningOrder.metro_station)
async def metro_station_callback(cb: CallbackQuery, state: FSMContext):
    key = cb.data.split("order:st:", 1)[1]
    if key == "manual":
        await cb.answer()
        try:
            await cb.message.edit_text("📍 Введите станцию метро текстом:", parse_mode="HTML")
        except TelegramBadRequest:
            await cb.message.answer("📍 Введите станцию метро текстом:", parse_mode="HTML")
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
    await cb.answer()
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None, metro=station_name)
        await show_preview(cb.message, state)
        return
    await state.update_data(metro=station_name, metro_prefix="order")
    await update_abandoned_order(cb.from_user.id, state, "выбрал метро")
    await state.set_state(CleaningOrder.address)
    await cb.message.answer(f"📍 Выбрано: <b>{station_name}</b>\n\n📍 Шаг 6. Укажите адрес:", reply_markup=order_step_kb(), parse_mode="HTML")


@dp.callback_query(F.data.endswith(":back_lines"), CleaningOrder.metro_station)
async def metro_back_lines(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    prefix = data.get("metro_prefix", "order")
    try:
        await cb.message.edit_text("📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb(prefix), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("📍 Шаг 5. Выберите станцию метро:", reply_markup=metro_lines_kb(prefix), parse_mode="HTML")


@dp.message(CleaningOrder.address)
async def address(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
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
    phone = msg.contact.phone_number
    if not is_valid_phone(phone):
        await msg.answer("Введите корректный номер телефона в формате +7XXXXXXXXXX или 8XXXXXXXXXX:")
        return
    await state.update_data(phone=normalize_phone(phone))
    await update_abandoned_order(msg.from_user.id, state, "ввёл телефон")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.name)
    await msg.answer("👤 Шаг 8. Имя/ФИО (необязательно):\nЕсли нет — нажмите «Пропустить».", reply_markup=name_kb())


@dp.message(CleaningOrder.phone, F.text)
async def phone_text(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
        return
    text = msg.text.strip()
    if not is_valid_phone(text):
        await msg.answer("Введите корректный номер телефона в формате +7XXXXXXXXXX или 8XXXXXXXXXX:")
        return
    await state.update_data(phone=normalize_phone(text))
    await update_abandoned_order(msg.from_user.id, state, "ввёл телефон")
    edit_data = await state.get_data()
    if edit_data.get("editing"):
        await state.update_data(editing=None)
        await show_preview(msg, state)
        return
    await state.set_state(CleaningOrder.name)
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
    await cb.message.answer("💬 Шаг 9. Комментарий (если нет — напишите '-'):", reply_markup=order_step_kb(), parse_mode="HTML")


@dp.message(CleaningOrder.name)
async def name_handler(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
        return
    text = msg.text.strip()
    if text.lower() in ("-", "пропустить", "skip"):
        text = ""
    await state.update_data(name=text)
    await update_abandoned_order(msg.from_user.id, state, "ввёл имя/ФИО")
    current = await state.get_state()
    if not current or not _is_cleaning_state(current):
        await msg.answer("Нет активного заказа для продолжения.", reply_markup=main_menu())
        return
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
    if await _process_menu_command(msg, state):
        return
    data = await state.get_data()
    if data.get("editing") == "comment":
        await state.update_data(comment=msg.text.strip(), editing=None)
        await show_preview(msg, state)
        return
    await state.update_data(comment=msg.text.strip())
    await update_abandoned_order(msg.from_user.id, state, "ввёл комментарий")
    current = await state.get_state()
    if not current or not _is_cleaning_state(current):
        await msg.answer("Нет активного заказа для продолжения.", reply_markup=main_menu())
        return
    await state.set_state(CleaningOrder.photos)
    await msg.answer("📸 Прикрепите фотографии помещения (или нажмите «Пропустить»):", reply_markup=photo_kb())


@dp.message(CleaningOrder.photos, F.photo)
async def photos_handler(msg: Message, state: FSMContext):
    photo = msg.photo[-1]
    data = await state.get_data()
    photos = data.get("photos", [])
    if len(photos) >= 10:
        await msg.answer("⚠️ Можно загрузить не более 10 фотографий.", reply_markup=photo_kb())
        return
    if photo.file_id in photos:
        await msg.answer(f"ℹ️ Это фото уже добавлено ({len(photos)}/10).", reply_markup=photo_kb())
        return
    photos.append(photo.file_id)
    await state.update_data(photos=photos)
    await update_abandoned_order(msg.from_user.id, state, "добавил фото")
    current = await state.get_state()
    if not current or not _is_cleaning_state(current):
        await msg.answer("Нет активного заказа для продолжения.", reply_markup=main_menu())
        return
    await msg.answer(f"✅ Фото добавлено ({len(photos)}/10). Добавьте ещё или нажмите «Пропустить».", reply_markup=photo_kb())


@dp.message(CleaningOrder.photos, F.text)
async def photos_text_handler(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        return
    if await _process_menu_command(msg, state):
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
    await cb.answer("Отправьте фото directamente", show_alert=False)


@dp.callback_query(F.data == "order:photo:clear", CleaningOrder.photos)
async def photos_clear(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(photos=[])
    await update_abandoned_order(cb.from_user.id, state, "добавил фото")
    current = await state.get_state()
    if not current or not _is_cleaning_state(current):
        await cb.message.answer("Нет активного заказа для продолжения.", reply_markup=main_menu())
        return
    await cb.message.answer("🗑 <b>Все фотографии удалены</b>\n\nОтправьте новые фотографии или нажмите «Пропустить».", parse_mode="HTML", reply_markup=photo_kb())


@dp.callback_query(F.data.startswith("order:photo:delete:"), CleaningOrder.photos)
async def photos_delete(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        idx = int(cb.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await cb.answer("Некорректный индекс фото", show_alert=True)
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    if 0 <= idx < len(photos):
        photos.pop(idx)
        await state.update_data(photos=photos)
        await update_abandoned_order(cb.from_user.id, state, "добавил фото")
        current = await state.get_state()
        if not current or not current.startswith("CleaningOrder"):
            await cb.message.answer("Нет активного заказа для продолжения.", reply_markup=main_menu())
            return
        await cb.message.answer(f"🗑 Фото удалено. Осталось: {len(photos)}")
    else:
        await cb.answer("Фото не найдено", show_alert=True)


@dp.callback_query(F.data == "order:photo:list", CleaningOrder.photos)
async def photos_list(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    photos = data.get("photos", [])
    if not photos:
        await cb.message.answer("📭 Нет загруженных фотографий.", reply_markup=photo_kb())
        return
    text = f"📸 <b>Фотографии ({len(photos)}):</b>\n\n"
    for i, file_id in enumerate(photos):
        text += f"{i + 1}. Фото {i + 1}\n"
    b = InlineKeyboardBuilder()
    for i in range(len(photos)):
        b.button(text=f"🗑 Удалить фото {i + 1}", callback_data=f"order:photo:delete:{i}")
    b.button(text="🔙 Назад", callback_data="order:photo:list:back")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data == "order:photo:list:back", CleaningOrder.photos)
async def photos_list_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        await cb.message.edit_text("📸 Прикрепите фотографии помещения (или нажмите «Пропустить»):", reply_markup=photo_kb(), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("📸 Прикрепите фотографии помещения (или нажмите «Пропустить»):", reply_markup=photo_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "order:preview:confirm", CleaningOrder.preview)
async def confirm_order(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await cb.answer()
    data = await state.get_data()
    if data.get("confirm_in_progress"):
        await cb.message.answer("⏳ Заказ уже создаётся, подождите.")
        return None
    required_fields = {
        "cleaning_type": "услугу",
        "flat_type": "тип квартиры/площадь",
        "date": "дату",
        "time": "время",
        "metro": "станцию метро",
        "address": "адрес",
        "phone": "телефон",
    }
    missing = [
        label for field, label in required_fields.items()
        if not data.get(field)
    ]
    if missing:
        await cb.message.answer(
            f"❌ Не заполнено: {', '.join(missing)}.",
            reply_markup=preview_kb(),
        )
        return None
    data = await state.get_data()
    if data.get("confirm_in_progress"):
        await cb.message.answer("⏳ Заказ уже создаётся, подождите.")
        return None
    await state.update_data(confirm_in_progress=True)
    data = await state.get_data()
    data.pop("confirm_in_progress", None)
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
            if not await save_order_photo(
                order_id,
                cb.from_user.id,
                photo_file_id,
                "client",
            ):
                raise RuntimeError("не удалось сохранить фотографию")

    except Exception:
        logger.warning("Failed to persist order %s", order_id, exc_info=True)
        await state.update_data(confirm_in_progress=False)
        await cb.message.answer(
            "❌ Не удалось создать заказ. Проверьте данные и попробуйте ещё раз.",
            reply_markup=preview_kb(),
        )
        return None

    try:
        await delete_abandoned_order(cb.from_user.id)
    except Exception:
        logger.warning("Failed to delete abandoned order after confirmation", exc_info=True)
    try:
        full_name = f"{cb.from_user.first_name or ''} {cb.from_user.last_name or ''}".strip() or None
        real_username = f"@{cb.from_user.username}" if cb.from_user.username else None
        await upsert_user(
            cb.from_user.id,
            real_username,
            full_name,
            name=data.get("name"),
        )
        await inc_cleaning_orders(cb.from_user.id, 1)
    except Exception:
        logger.warning(
            "Order %s was created but ancillary user data failed",
            order_id,
            exc_info=True,
        )

    # Notification failure must not turn a persisted order into a failed
    # checkout or leave the user trapped in the preview state.
    try:
        await send_order_to_admin(bot, order_id, data, "Уборка квартиры", status="new")
    except Exception:
        logger.warning("Order %s was created but admin notification failed", order_id, exc_info=True)

    await state.clear()
    await cb.message.answer(
        ORDER_CREATED_TEXT.format(order_id=order_id),
        reply_markup=main_menu(),
    )
    return order_id


@dp.callback_query(F.data == "order:preview:edit", CleaningOrder.preview)
async def preview_edit(cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.edit_text("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())
    except TelegramBadRequest:
        await cb.message.answer("✏️ Что вы хотите изменить?", reply_markup=edit_field_kb())


@dp.callback_query(F.data.startswith("order:preview:photo:delete:"), CleaningOrder.preview)
async def preview_photo_delete(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    try:
        idx = int(cb.data.split(":", 4)[4])
    except (ValueError, IndexError):
        await cb.answer("Некорректный индекс фото", show_alert=True)
        return
    data = await state.get_data()
    photos = data.get("photos", [])
    if 0 <= idx < len(photos):
        photos.pop(idx)
        await state.update_data(photos=photos)
        await show_preview(cb.message, state)
    else:
        await cb.answer("Фото не найдено", show_alert=True)


@dp.callback_query(F.data == "order:preview:cancel", CleaningOrder.preview)
async def preview_cancel(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    current = await state.get_state()
    if not current:
        await cb.answer("Вы не находитесь на шаге предпросмотра", show_alert=True)
        return
    if _state_name(current) != CleaningOrder.preview.state:
        await cb.answer("Вы не находитесь на шаге предпросмотра", show_alert=True)
        return
    await state.clear()
    try:
        await delete_abandoned_order(cb.from_user.id)
    except Exception as e:
            logger.warning("Operation failed", exc_info=True)
    await cb.message.answer(ORDER_CANCELLED_TEXT, reply_markup=main_menu())


@dp.callback_query(F.data.startswith("order:edit:"), CleaningOrder.preview)
async def edit_field_select(cb: CallbackQuery, state: FSMContext):
    field = cb.data.split("order:edit:", 1)[1]
    if field == "back":
        await cb.answer()
        await state.update_data(editing=None)
        await show_preview(cb.message, state)
        return
    if field not in {
        "cleaning_type",
        "flat_type",
        "date",
        "time",
        "metro",
        "address",
        "phone",
        "name",
        "comment",
        "photos",
    }:
        await cb.answer("Некорректное поле", show_alert=True)
        return
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
        await cb.message.answer("📍 Введите новый адрес:", reply_markup=order_step_kb())
    elif field == "metro":
        await state.set_state(CleaningOrder.metro_station)
        try:
            await cb.message.edit_text("📍 Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
        except TelegramBadRequest:
            await cb.message.answer("📍 Выберите станцию метро:", reply_markup=metro_lines_kb("order"), parse_mode="HTML")
    elif field == "phone":
        await state.set_state(CleaningOrder.phone)
        await cb.message.answer("📱 Введите новый номер телефона или отправьте контакт:", reply_markup=phone_request_kb())
    elif field == "name":
        await state.set_state(CleaningOrder.name)
        try:
            await cb.message.edit_text("👤 Введите имя/ФИО (или нажмите «Пропустить»):", reply_markup=name_kb())
        except TelegramBadRequest:
            await cb.message.answer("👤 Введите имя/ФИО (или нажмите «Пропустить»):", reply_markup=name_kb())
    elif field == "comment":
        await state.set_state(CleaningOrder.comment)
        await cb.message.answer("💬 Введите новый комментарий:", reply_markup=order_step_kb())
    elif field == "photos":
        data = await state.get_data()
        current_photos = data.get("photos", [])
        await state.update_data(photos=list(current_photos))
        await state.set_state(CleaningOrder.photos)
        if current_photos:
            text = f"📸 <b>Текущие фотографии:</b> {len(current_photos)} шт.\n\n"
            text += "Отправьте новые фотографии для замены/добавления, нажмите «Удалить все» или «Пропустить»."
            try:
                await cb.message.edit_text(text, parse_mode="HTML", reply_markup=photo_kb())
            except TelegramBadRequest:
                await cb.message.answer(text, parse_mode="HTML", reply_markup=photo_kb())
        else:
            try:
                await cb.message.edit_text("📸 Отправьте фотографии помещения:", reply_markup=photo_kb())
            except TelegramBadRequest:
                await cb.message.answer("📸 Отправьте фотографии помещения:", reply_markup=photo_kb())




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
        caption = (
            f"✨ <b>Вы выбрали:</b> {selected}\n\n"
            f"{INDIVIDUAL_CLEANING_WHAT_INCLUDED}\n\n"
            f"<b>Стоимость:</b> По согласованию\n\n"
            "Нажмите «Заказать уборку», чтобы перейти к оформлению, "
            "или «Поддержка», если остались вопросы."
        )
        builder = InlineKeyboardBuilder()
        builder.row(InlineKeyboardButton(text="✨ Заказать уборку", callback_data="individual:order"))
        builder.row(InlineKeyboardButton(text="💬 Поддержка", url="https://t.me/FYNclean"))
        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="order:back"))
        builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="order:cancel"))
        kb = builder.as_markup()
        try:
            await bot.send_photo(
                chat_id=cb.message.chat.id,
                photo=FSInputFile(INDIVIDUAL_CLEANING_PHOTO),
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
            try:
                await cb.message.delete()
            except Exception:
                pass
        except Exception:
            await cb.message.answer(
                caption,
                parse_mode="HTML",
                reply_markup=kb,
            )
        return

    photo = PHOTO_FLAT_TYPES.get(selected)

    what_included_links = {
        "Поддерживающая": "https://t.me/stroiteli_v_msk/33690",
        "Генеральная уборка": "https://t.me/kristalclin/33",
        "После ремонта": "https://t.me/kristalclin/40",
        "Химчистка": "https://t.me/hiteczone/2106?single"
    }
    link = what_included_links.get(selected, "https://example.com/obshchaya-informaciya")

    info = SERVICE_INFO.get(selected, {})
    service_desc = info.get("desc", "")
    service_price = info.get("price", "")
    caption = (
        f"✨ <b>Вы выбрали:</b> {selected}\n\n"
        f"<b>Описание:</b> {service_desc}\n"
        f"<b>Стоимость:</b> {service_price}\n\n"
        "Далее вам осталось заполнить форму заказа и наши клинеры свяжутся с Вами для согласования времени."
    )

    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Заполнить форму ✍🏻", callback_data="order:proceed"))
    builder.row(InlineKeyboardButton(text="Что входит в уборку", url=link))
    builder.row(InlineKeyboardButton(text="Поддержка", url="https://t.me/FYNclean"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="order:back"))
    builder.row(InlineKeyboardButton(text="❌ Отменить", callback_data="order:cancel"))
    kb = builder.as_markup()

    try:
        await bot.send_photo(
            chat_id=cb.message.chat.id,
            photo=photo,
            caption=caption,
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
        logger.warning("Operation failed", exc_info=True)

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
    await state.set_state(CleaningOrder.cleaning_type)
    try:
        await cb.message.edit_text("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb())
    except TelegramBadRequest:
        await cb.message.answer("✨ Шаг 1. Выберите тип уборки 👇", reply_markup=cleaning_type_kb())


@dp.callback_query(F.data == "info:what_included")
async def show_what_included(cb: CallbackQuery):
    text = (
        "Что входит в уборку:\n\n"
        "• Обеспыливание доступных поверхностей\n"
        "• Пылесос и влажная уборка пола\n"
        "• Уборка кухни и санузла\n"
        "• Очистка сантехники и рабочих поверхностей\n"
        "• Вынос мусора\n\n"
        "Точный объём работ менеджер согласует после заявки."
    )
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="← Назад", callback_data="back_to_step2"))
    await cb.message.edit_text(text, reply_markup=builder.as_markup())
    await cb.answer()


@dp.callback_query(F.data == "order:proceed", CleaningOrder.cleaning_type)
async def start_flat_selection(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    cleaning_type = data.get("cleaning_type")
    if not cleaning_type:
        await cb.answer("Сначала выберите услугу", show_alert=True)
        return
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
    flat_type = mapping.get(key)
    if not flat_type:
        await cb.answer("Некорректный тип квартиры", show_alert=True)
        return
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
        if not executor or not executor[4]:
            await msg.answer("❌ У вас нет доступа.", reply_markup=main_menu())
            return
        await state.clear()
        orders = await get_orders_by_executor(msg.from_user.id)
        if not orders:
            await msg.answer("📭 <b>Нет назначенных заказов</b>", parse_mode="HTML", reply_markup=executor_menu_kb())
            return
        text = "📋 <b>Мои заказы</b>\n\n"
        b = InlineKeyboardBuilder()
        for o in orders:
            order_id, service, data_json, status, created_at, date, time = o
            data = json.loads(data_json or "{}")
            text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(service)}\n"
            text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
            text += f"📍 {html_str(data.get('address'))}\n"
            text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
            b.button(text=f"📋 Детали {order_id}", callback_data=f"exec:order:{order_id}")
        b.button(text="🔙 В главное меню", callback_data="my_orders:back")
        b.adjust(1)
        await msg.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
        return
    await state.clear()
    try:
        await msg.delete_reply_markup()
    except Exception:
        pass
    await msg.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=my_orders_kb(), parse_mode="HTML")


@dp.callback_query(F.data == "my_orders:back")
async def my_orders_back(cb: CallbackQuery):
    await cb.answer()
    executor = await get_executor(cb.from_user.id)
    if executor and executor[4]:
        await cb.message.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=executor_menu_kb(), parse_mode="HTML")
    else:
        await cb.message.delete_reply_markup()
        await cb.message.answer("📋 <b>Мои заказы</b>\n\nВыберите раздел:", reply_markup=my_orders_kb(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("my_orders:active"))
async def my_orders_active(cb: CallbackQuery):
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    await _render_my_active(cb, page=page)


async def _render_my_active(cb: CallbackQuery, page: int = 0):
    await cb.answer()
    user_id = cb.from_user.id
    orders = await get_user_orders(user_id)
    active_statuses = {"new", "accepted", "assigned", "in_work", "contacted"}
    active = [o for o in orders if o[3] in active_statuses]
    per_page = 10
    offset = page * per_page
    page_active = active[offset:offset + per_page]
    if not active:
        try:
            await cb.message.edit_text("📭 <b>Нет активных заказов</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет активных заказов</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        return
    total_pages = max(1, (len(active) + per_page - 1) // per_page)
    text = f"📌 <b>Активные заказы</b> — {len(active)}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for o in page_active:
        order_id, service, data_json, status, created_at, executor_id, date, time = o
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(service)}\n"
        text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
        text += f"📍 {html_str(data.get('address'))}\n"
        text += f"👤 {html_str(executor_name)}\n"
        text += f"💰 {html_str(price)}\n"
        text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"my_orders:active:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"my_orders:active:{page + 1}")
    b.button(text="📜 История", callback_data="my_orders:history")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


@dp.callback_query(F.data.startswith("my_orders:history"))
async def my_orders_history(cb: CallbackQuery):
    parts = cb.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    await _render_my_history(cb, page=page)


@dp.callback_query(F.data.startswith("my_orders:cancel:"))
async def my_orders_cancel(cb: CallbackQuery, state: FSMContext):
    order_id = cb.data.split(":", 2)[2]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете отменить этот заказ", show_alert=True)
        return
    if not is_order_modifiable(order["status"]):
        await cb.answer("Этот заказ уже завершён или отменён", show_alert=True)
        return
    data = order["data"]
    current_status = order["status"]
    if not await transition_status(order_id, "cancelled"):
        await cb.answer("Заказ нельзя отменить в текущем статусе", show_alert=True)
        return
    await state.clear()
    updated = await get_order(order_id)
    if updated:
        data = updated["data"]
        try:
            await notify_client_status(cb.bot, order_id, data, "cancelled")
        except Exception:
            logger.warning("Failed to notify client about cancellation", exc_info=True)
        try:
            await send_order_to_admin(cb.bot, order_id, data, "Уборка квартиры", status="cancelled")
        except Exception:
            logger.warning("Failed to notify admin about cancellation", exc_info=True)
        if updated.get("executor_id"):
            try:
                await notify_executor_status(cb.bot, order_id, data, "cancelled", executor_id=updated["executor_id"])
            except Exception:
                logger.warning("Failed to notify executor about cancellation", exc_info=True)
    await cb.answer("Заказ отменён")
    await _render_my_history(cb, page=0)


async def _render_my_history(cb: CallbackQuery, page: int = 0):
    user_id = cb.from_user.id
    orders = await get_user_orders(user_id)
    history_statuses = {"completed", "cancelled"}
    history = [o for o in orders if o[3] in history_statuses]
    per_page = 10
    offset = page * per_page
    page_history = history[offset:offset + per_page]
    if not history:
        try:
            await cb.message.edit_text("📭 <b>История заказов пуста</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>История заказов пуста</b>", parse_mode="HTML", reply_markup=my_orders_kb())
        await cb.answer()
        return
    total_pages = max(1, (len(history) + per_page - 1) // per_page)
    text = f"📜 <b>История заказов</b> — {len(history)}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for o in page_history:
        order_id, service, data_json, status, created_at, executor_id, date, time = o
        data = json.loads(data_json)
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(service)}\n"
        text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
        text += f"📍 {html_str(data.get('address'))}\n"
        text += f"👤 {html_str(executor_name)}\n"
        text += f"💰 {html_str(price)}\n"
        text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"my_orders:detail:{order_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"my_orders:history:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"my_orders:history:{page + 1}")
    b.button(text="📌 Активные", callback_data="my_orders:active")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("my_orders:rate:"))
async def my_orders_rate(cb: CallbackQuery):
    order_id = cb.data.split(":", 2)[2]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете оценить этот заказ", show_alert=True)
        return
    order_data = order["data"]
    executor_id = order["executor_id"]
    if order["status"] != "completed" or not executor_id:
        await cb.answer("Оценка доступна после выполнения заказа", show_alert=True)
        return
    if await get_order_rating(order_id):
        await cb.answer("Вы уже оценили этот заказ", show_alert=True)
        return
    try:
        await cb.message.edit_text("⭐ <b>Оцените работу исполнителя</b>\n\nВыберите оценку:", reply_markup=rating_kb(order_id), parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("⭐ <b>Оцените работу исполнителя</b>\n\nВыберите оценку:", reply_markup=rating_kb(order_id), parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data.startswith("rating:"))
async def rating_callback(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    order_id = parts[1]
    try:
        rating = int(parts[2])
    except ValueError:
        await cb.answer("Некорректная оценка", show_alert=True)
        return
    if not 1 <= rating <= 5:
        await cb.answer("Оценка должна быть от 1 до 5", show_alert=True)
        return
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    if order["status"] != "completed":
        await cb.answer("Оценка доступна после выполнения заказа", show_alert=True)
        return
    if await get_order_rating(order_id):
        await cb.answer("Вы уже оценили этот заказ", show_alert=True)
        return
    data = order["data"]
    executor_id = order["executor_id"]
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


async def notify_admin_rating(bot: Bot, order_id: str, rating: int, executor_id: int):
    try:
        avg_data = await get_executor_avg_rating(executor_id)
        text = (
            f"⭐ <b>Новая оценка</b>\n\n"
            f"<b>Заказ:</b> {html_str(order_id)}\n"
            f"<b>Оценка:</b> {rating}/5\n"
            f"<b>Средний рейтинг исполнителя:</b> {avg_data.get('avg', 0)} ({avg_data.get('count', 0)} отзывов)"
        )
        await bot.send_message(cfg.admin_chat_id, text, parse_mode="HTML")
    except Exception:
        logger.warning("Failed to notify admin about new rating", exc_info=True)


@dp.message(RatingStates.comment)
async def rating_comment_handler(msg: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    order_id = data.get("rating_order_id")
    rating = data.get("rating")
    comment = (msg.text or "").strip()
    order = await get_order(order_id) if order_id else None
    saved = False
    if (
        order
        and same_telegram_id(order["data"].get("telegram_id"), msg.from_user.id)
        and order["status"] == "completed"
        and order["executor_id"]
        and rating
    ):
        saved = await save_rating(
            order_id,
            msg.from_user.id,
            order["executor_id"],
            rating,
            "" if comment in {"-", "—"} else comment,
        ) is not None
    await state.clear()
    if saved:
        await notify_admin_rating(bot, order_id, rating, order["executor_id"])
        await msg.answer(RATING_THANKS, parse_mode="HTML", reply_markup=main_menu())
    else:
        await msg.answer(
            "❌ Не удалось сохранить оценку. Заказ уже оценён или больше недоступен.",
            reply_markup=main_menu(),
        )


@dp.callback_query(F.data.startswith("my_orders:repeat:"))
async def my_orders_repeat(cb: CallbackQuery, state: FSMContext):
    order_id = cb.data.split(":", 2)[2]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете открыть этот заказ", show_alert=True)
        return
    if order["status"] != "completed":
        await cb.answer("Повторить можно только завершённый заказ", show_alert=True)
        return
    data = order["data"]
    await state.clear()
    repeat_data = {k: v for k, v in data.items() if k not in {"telegram_id", "photos", "executor_id", "status", "completed_at"}}
    repeat_data["photos"] = []
    if order.get("date"):
        repeat_data["date"] = order["date"]
    if order.get("time"):
        repeat_data["time"] = order["time"]
    await state.update_data(**repeat_data)
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
@dp.message(F.text == "⭐ Мой рейтинг", ExecutorStates.menu)
async def executor_my_rating(msg: Message, state: FSMContext):
    executor = await get_executor(msg.from_user.id)
    if not executor or not executor[4]:
        await msg.answer("❌ У вас нет доступа.", reply_markup=main_menu())
        return
    await state.clear()
    await state.set_state(ExecutorStates.menu)
    avg_data = await get_executor_avg_rating(msg.from_user.id)
    avg = avg_data.get("avg") if avg_data else None
    reviews = await get_executor_ratings(msg.from_user.id)
    text = f"⭐ <b>Мой рейтинг</b>\n\n"
    text += f"📊 Средний балл: {avg if avg is not None else '—'}\n\n"
    if reviews:
        text += "<b>Отзывы:</b>\n\n"
        for r in reviews[:10]:
            rating, comment, created_at, data_json, client_name = r
            text += f"⭐ {rating}/5 — {client_name or 'Клиент'}\n"
            text += f"💬 {comment or '—'}\n"
            text += f"📅 {created_at}\n\n"
    await msg.answer(text, parse_mode="HTML", reply_markup=executor_menu_kb())


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
    if not executor[4]:
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
async def executor_orders_list(msg: Message, state: FSMContext, page: int = 0):
    executor = await get_executor(msg.from_user.id)
    if not executor or not executor[4]:
        await msg.answer("❌ У вас нет доступа.", reply_markup=main_menu())
        return
    await _render_executor_orders(msg, state, page=page)


async def _render_executor_orders(msg_or_cb, state: FSMContext, page: int = 0):
    executor = await get_executor(msg_or_cb.from_user.id)
    if not executor or not executor[4]:
        if hasattr(msg_or_cb, "message"):
            await msg_or_cb.message.answer("❌ У вас нет доступа.", reply_markup=main_menu())
        else:
            await msg_or_cb.answer("❌ У вас нет доступа.", reply_markup=main_menu())
        return
    orders = await get_orders_by_executor(msg_or_cb.from_user.id)
    if not orders:
        text = "📭 <b>Нет назначенных заказов</b>"
        reply_markup = executor_menu_kb()
        if hasattr(msg_or_cb, "message"):
            await msg_or_cb.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        else:
            await msg_or_cb.answer(text, parse_mode="HTML", reply_markup=reply_markup)
        return
    per_page = 10
    offset = page * per_page
    page_orders = orders[offset:offset + per_page]
    total_pages = max(1, (len(orders) + per_page - 1) // per_page)
    text = f"📋 <b>Мои заказы</b> — {len(orders)}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for o in page_orders:
        order_id, service, data_json, status, created_at, date, time = o
        try:
            data = json.loads(data_json or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(service)}\n"
        text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
        text += f"📍 {html_str(data.get('address'))}\n"
        text += f"👤 {html_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        text += f"📞 {html_str(data.get('phone'))}\n"
        text += f"💬 {html_str(data.get('comment'))}\n"
        text += f"💰 {html_str(price)}\n"
        text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"exec:order:{order_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"exec:orders:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"exec:orders:{page + 1}")
    b.button(text="🔙 В главное меню", callback_data="my_orders:back")
    b.adjust(2)
    reply_markup = b.as_markup()
    if hasattr(msg_or_cb, "message"):
        try:
            await msg_or_cb.message.edit_text(text, parse_mode="HTML", reply_markup=reply_markup)
        except TelegramBadRequest:
            await msg_or_cb.message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
    else:
        await msg_or_cb.answer(text, parse_mode="HTML", reply_markup=reply_markup)


@dp.callback_query(F.data.startswith("exec:orders:"))
async def executor_orders_page(cb: CallbackQuery, state: FSMContext):
    if await state.get_state() != ExecutorStates.menu.state:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    page = int(cb.data.split(":", 2)[2])
    await _render_executor_orders(cb, state, page=page)
    await cb.answer()


async def _render_executor_order(cb: CallbackQuery, order: dict):
    data = order["data"]
    order_id = order["id"]
    status = order["status"]
    text = EXECUTOR_ORDER_TEMPLATE.format(
        service=html_str(data.get("cleaning_type")),
        flat_type=html_str(data.get("flat_type")),
        status=html_str(STATUS_LABELS.get(status, status)),
        order_id=html_str(order_id),
        date=html_str(data.get("date")),
        time=html_str(data.get("time")),
        address=html_str(data.get("address")),
        metro=html_str(data.get("metro")),
        phone=html_str(data.get("phone")),
        fio=html_str(data.get("fio") or data.get("full_name") or data.get("name")),
        name=html_str(data.get("name")),
        comment=html_str(data.get("comment")),
        price=html_str(calc_price_estimate(data.get("flat_type", ""), data.get("cleaning_type", ""))),
    )
    if order.get("completed_at"):
        text += f"<b>Завершено:</b> {html_str(order['completed_at'])}\n"
    photos = await get_order_photos(order_id)
    client_photos = [photo for photo in photos if photo[2] == "client"]
    result_photos = [photo for photo in photos if photo[2] == "result"]
    try:
        await cb.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=executor_order_actions_kb(order_id, status),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=executor_order_actions_kb(order_id, status),
        )


@dp.callback_query(F.data.startswith("exec:order:"))
async def executor_order_detail(cb: CallbackQuery, state: FSMContext):
    order_id = cb.data.split(":", 2)[2]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    if await state.get_state() != ExecutorStates.menu.state:
        await state.set_state(ExecutorStates.menu)
    await _render_executor_order(cb, order)
    await cb.answer()


@dp.callback_query(F.data.startswith("exec:status:"))
async def executor_update_status(cb: CallbackQuery, state: FSMContext):
    executor = await get_executor(cb.from_user.id)
    if not executor or not executor[4]:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[2]
    new_status = parts[3]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    if await state.get_state() != ExecutorStates.menu.state:
        await state.set_state(ExecutorStates.menu)
    if order["status"] in {"completed", "cancelled"}:
        await cb.answer("Статус заказа уже окончательный", show_alert=True)
        return
    if not can_transition_status(order["status"], new_status):
        await cb.answer("Недопустимый переход статуса", show_alert=True)
        return
    if not await transition_status(order_id, new_status):
        await cb.answer("Статус уже изменён или переход недоступен", show_alert=True)
        return
    updated = await get_order(order_id)
    if updated:
        await notify_client_status(cb.bot, order_id, updated["data"], new_status)
        await send_order_to_admin(
            cb.bot,
            order_id,
            updated["data"],
            "Уборка квартиры",
            status=new_status,
        )
        await _render_executor_order(cb, updated)
    await cb.answer(f"Статус обновлён: {STATUS_LABELS.get(new_status, new_status)}")


@dp.callback_query(F.data.startswith("exec:photo:"))
async def executor_request_photo(cb: CallbackQuery, state: FSMContext):
    executor = await get_executor(cb.from_user.id)
    if not executor or not executor[4]:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    if order["status"] not in {"in_work", "completed"}:
        await cb.answer("Фото результата доступны в работе или после выполнения", show_alert=True)
        return
    if await state.get_state() != ExecutorStates.menu.state:
        await state.set_state(ExecutorStates.menu)
    await state.update_data(uploading_photos_to=order_id)
    try:
        await cb.message.edit_text("📸 <b>Отправьте фотографии результата уборки</b>\n\nОтправляйте по одной или группой:", parse_mode="HTML")
    except TelegramBadRequest:
        await cb.message.answer("📸 <b>Отправьте фотографии результата уборки</b>\n\nОтправляйте по одной или группой:", parse_mode="HTML")
    await cb.answer()


@dp.callback_query(F.data.startswith("exec:photos:client:"))
async def executor_view_client_photos(cb: CallbackQuery):
    executor = await get_executor(cb.from_user.id)
    if not executor or not executor[4]:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    order_id = cb.data.split(":", 3)[3]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    client_photos = [photo for photo in photos if photo[2] == "client"]
    if not client_photos:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад к заказу", callback_data=f"exec:order:{order_id}")
        b.adjust(1)
        try:
            await cb.message.edit_text("📭 <b>Нет фотографий помещения</b>", parse_mode="HTML", reply_markup=b.as_markup())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет фотографий помещения</b>", parse_mode="HTML", reply_markup=b.as_markup())
        return
    text = f"📸 <b>Фотографии помещения</b> — {len(client_photos)}\n\n"
    b = InlineKeyboardBuilder()
    for idx, photo in enumerate(client_photos):
        text += f"{idx + 1}. Фото {idx + 1}\n"
    b.button(text="🔙 Назад к заказу", callback_data=f"exec:order:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    try:
        await cb.message.answer_media_group(
            [InputMediaPhoto(media=p[1]) for p in client_photos[:5]]
        )
    except Exception:
        logger.warning("Failed to send client photos to executor", exc_info=True)
    await cb.answer()


@dp.callback_query(F.data.startswith("exec:photos:result:"))
async def executor_view_result_photos(cb: CallbackQuery):
    executor = await get_executor(cb.from_user.id)
    if not executor or not executor[4]:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    order_id = cb.data.split(":", 3)[3]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    result_photos = [photo for photo in photos if photo[2] == "result"]
    if not result_photos:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад к заказу", callback_data=f"exec:order:{order_id}")
        b.adjust(1)
        try:
            await cb.message.edit_text("📭 <b>Нет фотографий результата</b>", parse_mode="HTML", reply_markup=b.as_markup())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет фотографий результата</b>", parse_mode="HTML", reply_markup=b.as_markup())
        return
    text = f"📸 <b>Фотографии результата</b> — {len(result_photos)}\n\n"
    b = InlineKeyboardBuilder()
    for idx, photo in enumerate(result_photos):
        text += f"{idx + 1}. Фото {idx + 1}\n"
        b.button(text=f"🗑 Удалить фото {idx + 1}", callback_data=f"exec:photo:delete:{order_id}:{photo[0]}")
    b.button(text="🔙 Назад к заказу", callback_data=f"exec:order:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("exec:photo:delete:"))
async def executor_delete_result_photo(cb: CallbackQuery):
    executor = await get_executor(cb.from_user.id)
    if not executor or not executor[4]:
        await cb.answer("Сначала войдите в панель исполнителя", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[3]
    photo_id = parts[4]
    order = await get_assigned_executor_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Нет доступа к этому заказу", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT photo_type FROM order_photos WHERE id = ? AND order_id = ?", (photo_id, order_id)) as cur:
            row = await cur.fetchone()
        if not row or row[0] != "result":
            await db.rollback()
            await cb.answer("Нет доступа к этому фото", show_alert=True)
            return
        await db.execute("DELETE FROM order_photos WHERE id = ?", (photo_id,))
        await db.execute("UPDATE orders SET updated_at = datetime('now') WHERE id = ?", (order_id,))
        await db.commit()
    await cb.answer("✅ Фото удалено")
    await executor_view_result_photos(cb)


@dp.message(F.photo, ExecutorStates.menu)
async def executor_upload_photo(msg: Message, state: FSMContext, bot: Bot):
    executor = await get_executor(msg.from_user.id)
    if not executor or not executor[4]:
        await msg.answer("❌ У вас нет доступа к панели исполнителя.", reply_markup=main_menu())
        return
    data = await state.get_data()
    order_id = data.get("uploading_photos_to")
    if not order_id:
        await msg.answer("❌ Сначала выберите заказ и нажмите «Отправить фото результата».")
        return
    order = await get_assigned_executor_order(order_id, msg.from_user.id)
    if not order or order["status"] not in {"in_work", "completed"}:
        await msg.answer("❌ Нет доступа к этому заказу или фото пока недоступно.")
        return
    photo = msg.photo[-1]
    photos = await get_order_photos(order_id)
    result_photos = [p for p in photos if p[2] == "result"]
    if len(result_photos) >= 10:
        await msg.answer("⚠️ Можно загрузить не более 10 фотографий результата.")
        return
    if photo.file_id in [p[1] for p in result_photos]:
        await msg.answer(f"ℹ️ Это фото уже добавлено ({len(result_photos)}/10).")
        return
    photo_id = await save_order_photo(order_id, msg.from_user.id, photo.file_id, "result")
    if not photo_id:
        await msg.answer("❌ Не удалось сохранить фото.")
        return
    await notify_result_photo(bot, order_id, order["data"], photo.file_id)
    await msg.answer("✅ Фото сохранено. Отправьте ещё или нажмите кнопку ниже.", reply_markup=InlineKeyboardBuilder().row(InlineKeyboardButton(text="🔙 Назад к заказам", callback_data="exec:orders:back")).as_markup())


@dp.callback_query(F.data == "exec:orders:back")
async def executor_orders_back(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.update_data(uploading_photos_to=None)
    executor = await get_executor(cb.from_user.id)
    if executor and executor[4]:
        await state.set_state(ExecutorStates.menu)
        await cb.message.answer(EXECUTOR_WELCOME, reply_markup=executor_menu_kb(), parse_mode="HTML")
    else:
        await state.clear()
        await cb.message.answer("Главное меню", reply_markup=main_menu())


# ================= MY ORDERS DETAIL =================

@dp.callback_query(F.data.startswith("my_orders:detail:"))
async def my_orders_detail(cb: CallbackQuery):
    order_id = cb.data.split(":", 2)[2]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете открыть этот заказ", show_alert=True)
        return
    data = order["data"]
    status = order["status"]
    has_rating = await get_order_rating(order_id) is not None
    flat_type = data.get("flat_type", "—")
    price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
    executor_id = order["executor_id"]
    executor_name = "—"
    if executor_id:
        ex = await get_executor(executor_id)
        if ex:
            executor_name = ex[1] or ex[0]
    text = (
        f"📋 <b>Заказ | FYN Clean</b>\n\n"
        f"<b>Услуга:</b> {html_str(data.get('cleaning_type'))}\n"
        f"<b>Тип квартиры:</b> {html_str(flat_type)}\n"
        f"<b>Статус:</b> {html_str(STATUS_LABELS.get(status, status))}\n"
        f"<b>ID:</b> {html_str(order_id)}\n\n"
        f"<b>Дата:</b> {html_str(data.get('date'))}\n"
        f"<b>Время:</b> {html_str(data.get('time'))}\n"
        f"<b>Адрес:</b> {html_str(data.get('address'))}\n"
        f"<b>Метро:</b> {html_str(data.get('metro'))}\n"
        f"<b>Телефон:</b> {html_str(data.get('phone'))}\n"
        f"<b>Контакт:</b> {html_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Имя/ФИО:</b> {html_str(data.get('name'))}\n"
        f"<b>Комментарий:</b> {html_str(data.get('comment'))}\n"
        f"<b>Исполнитель:</b> {html_str(executor_name)}\n"
        f"<b>Стоимость:</b> {html_str(price)}\n"
    )
    if order.get("completed_at"):
        text += f"<b>Завершено:</b> {html_str(order['completed_at'])}\n"
    photos = await get_order_photos(order_id)
    try:
        await cb.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=order_detail_kb(order_id, status, has_rating, executor_id),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=order_detail_kb(order_id, status, has_rating, executor_id),
        )
    await cb.answer()


@dp.callback_query(F.data.startswith("my_orders:photos:client:"))
async def my_orders_view_client_photos(cb: CallbackQuery):
    order_id = cb.data.split(":", 3)[3]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете открыть этот заказ", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    client_photos = [photo for photo in photos if photo[2] == "client"]
    if not client_photos:
        await cb.message.answer(
            "📭 <b>Нет фотографий помещения</b>",
            parse_mode="HTML",
            reply_markup=order_detail_kb(order_id, order["status"], await get_order_rating(order_id) is not None, order["executor_id"]),
        )
        return
    text = f"📸 <b>Фотографии помещения</b> — {len(client_photos)}\n\n"
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад к заказу", callback_data=f"my_orders:detail:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    try:
        await cb.message.answer_media_group(
            [InputMediaPhoto(media=p[1]) for p in client_photos[:10]]
        )
    except Exception:
        logger.warning("Failed to send client photos to client", exc_info=True)


@dp.callback_query(F.data.startswith("my_orders:photos:result:"))
async def my_orders_view_result_photos(cb: CallbackQuery):
    order_id = cb.data.split(":", 3)[3]
    order = await get_owned_order(order_id, cb.from_user.id)
    if not order:
        await cb.answer("Вы не можете открыть этот заказ", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    result_photos = [photo for photo in photos if photo[2] == "result"]
    if not result_photos:
        await cb.message.answer(
            "📭 <b>Нет фотографий результата</b>",
            parse_mode="HTML",
            reply_markup=order_detail_kb(order_id, order["status"], await get_order_rating(order_id) is not None, order["executor_id"]),
        )
        return
    text = f"📸 <b>Фотографии результата</b> — {len(result_photos)}\n\n"
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад к заказу", callback_data=f"my_orders:detail:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    try:
        await cb.message.answer_media_group(
            [InputMediaPhoto(media=p[1]) for p in result_photos[:10]]
        )
    except Exception:
        logger.warning("Failed to send result photos to client", exc_info=True)


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
async def admin_panel(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        await msg.answer("❌ У вас нет доступа к админ-панели.")
        return
    await state.clear()
    await msg.answer("👑 <b>Админ-панель FYN Clean</b>\n\nВыберите раздел:", reply_markup=admin_orders_filter_kb(page=0), parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin:orders:"))
async def admin_orders_list(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Некорректный фильтр", show_alert=True)
        return
    status_filter = parts[2]
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    status_map = {
        "new": "new",
        "accepted": "accepted",
        "assigned": "assigned",
        "in_work": "in_work",
        "active": "active",
        "completed": "completed",
        "cancelled": "cancelled"
    }
    target_status = status_map.get(status_filter)
    per_page = 10
    offset = page * per_page
    if target_status == "active":
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, service, data_json, status, created_at, executor_id, date, time
                FROM orders
                WHERE status IN ('accepted','assigned','in_work','contacted')
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (per_page, offset)) as cur:
                orders = await cur.fetchall()
            async with db.execute("""
                SELECT COUNT(*) FROM orders
                WHERE status IN ('accepted','assigned','in_work','contacted')
            """) as cur:
                total = (await cur.fetchone())[0]
    elif target_status:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, service, data_json, status, created_at, executor_id, date, time
                FROM orders
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (target_status, per_page, offset)) as cur:
                orders = await cur.fetchall()
            async with db.execute("""
                SELECT COUNT(*) FROM orders WHERE status = ?
            """, (target_status,)) as cur:
                total = (await cur.fetchone())[0]
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT id, service, data_json, status, created_at, executor_id, date, time FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset)) as cur:
                orders = await cur.fetchall()
            async with db.execute("SELECT COUNT(*) FROM orders") as cur:
                total = (await cur.fetchone())[0]
    if not orders:
        text = "📭 <b>Нет заказов</b>"
        try:
            await cb.message.edit_text(text, parse_mode="HTML", reply_markup=admin_orders_filter_kb(page))
        except TelegramBadRequest:
            await cb.message.answer(text, parse_mode="HTML", reply_markup=admin_orders_filter_kb(page))
        return
    total_pages = max(1, (total + per_page - 1) // per_page)
    text = f"📋 <b>Заказы</b> ({status_filter}) — {total}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for o in orders:
        if isinstance(o, aiosqlite.Row):
            order_id = o["id"]
            data_json = o["data_json"]
            status = o["status"]
            executor_id = o["executor_id"]
        else:
            order_id, service, data_json, status, created_at = o[:5]
            executor_id = o[5] if len(o) > 5 else None
        try:
            data = json.loads(data_json or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        executor_id = executor_id or data.get("executor_id")
        executor_name = "—"
        if executor_id:
            ex = await get_executor(executor_id)
            if ex:
                executor_name = ex[1] or ex[0]
        text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(data.get('cleaning_type'))}\n"
        text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
        text += f"📍 {html_str(data.get('address'))}\n"
        text += f"👤 {html_str(data.get('fio'))}\n"
        text += f"📞 {html_str(data.get('phone'))}\n"
        text += f"👷 {html_str(executor_name)}\n"
        text += f"💰 {html_str(price)}\n"
        text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
        b.button(text=f"📋 {order_id}", callback_data=f"admin:order:{order_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"admin:orders:{status_filter}:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"admin:orders:{status_filter}:{page + 1}")
    b.button(text="🆕 Новые", callback_data=f"admin:orders:new:0")
    b.button(text="📌 Активные", callback_data=f"admin:orders:active:0")
    b.button(text="✅ Выполненные", callback_data=f"admin:orders:completed:0")
    b.button(text="❌ Отменённые", callback_data=f"admin:orders:cancelled:0")
    b.button(text="👥 Исполнители", callback_data="admin:executors")
    b.button(text="📊 Статистика", callback_data="admin:stats")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())


async def _render_admin_order_detail(cb: CallbackQuery, order_id: str):
    order = await get_order(order_id)
    if not order:
        return False
    data = order["data"]
    status = order["status"]
    flat_type = data.get("flat_type", "—")
    price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
    executor_id = order["executor_id"]
    executor_name = "—"
    if executor_id:
        ex = await get_executor(executor_id)
        if ex:
            executor_name = ex[1] or ex[0]
    text = (
        f"📋 <b>Заказ | FYN Clean</b>\n\n"
        f"<b>Услуга:</b> {html_str(data.get('cleaning_type'))}\n"
        f"<b>Тип квартиры:</b> {html_str(flat_type)}\n"
        f"<b>Статус:</b> {html_str(STATUS_LABELS.get(status, status))}\n"
        f"<b>ID:</b> {html_str(order_id)}\n\n"
        f"<b>Дата:</b> {html_str(data.get('date'))}\n"
        f"<b>Время:</b> {html_str(data.get('time'))}\n"
        f"<b>Адрес:</b> {html_str(data.get('address'))}\n"
        f"<b>Метро:</b> {html_str(data.get('metro'))}\n"
        f"<b>Телефон:</b> {html_str(data.get('phone'))}\n"
        f"<b>Контакт:</b> {html_str(data.get('fio') or data.get('full_name') or data.get('name'))}\n"
        f"<b>Имя/ФИО:</b> {html_str(data.get('name'))}\n"
        f"<b>Комментарий:</b> {html_str(data.get('comment'))}\n"
        f"<b>Telegram:</b> {html_str(data.get('username'))}\n"
        f"<b>Исполнитель:</b> {html_str(executor_name)}\n"
        f"<b>Стоимость:</b> {html_str(price)}\n"
    )
    if order.get("completed_at"):
        text += f"<b>Завершено:</b> {html_str(order['completed_at'])}\n"
    photos = await get_order_photos(order_id)
    try:
        await cb.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_status_kb(order_id, status, executor_id),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            text,
            parse_mode="HTML",
            reply_markup=admin_status_kb(order_id, status, executor_id),
        )
    return True


@dp.callback_query(F.data.startswith("admin:order:"))
async def admin_order_detail(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    if not await _render_admin_order_detail(cb, order_id):
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:assign_executor:"))
async def admin_show_assign_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 2)[2]
    order = await get_order_for_mutation(order_id, cb)
    if not order:
        return
    executors = await get_active_executors()
    if not executors:
        await cb.answer("Нет активных исполнителей", show_alert=True)
        return
    try:
        await cb.message.edit_text("👤 <b>Выберите исполнителя:</b>", parse_mode="HTML", reply_markup=executor_assign_kb(order_id, executors))
    except TelegramBadRequest:
        await cb.message.answer("👤 <b>Выберите исполнителя:</b>", parse_mode="HTML", reply_markup=executor_assign_kb(order_id, executors))
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:status:"))
async def admin_status(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[2]
    status = parts[3]
    order = await get_order_for_mutation(order_id, cb)
    if not order:
        return
    if status == "cancelled":
        await state.update_data(admin_cancel_order_id=order_id)
        await state.set_state(AdminStates.cancel_reason)
        await cb.message.edit_text(
            "❌ <b>Отмена заказа</b>\n\n"
            "Укажите причину отмены. "
            "Клиент увидит её в сообщении об отмене:",
            parse_mode="HTML",
        )
        await cb.answer()
        return
    if status in {"assigned", "in_work", "completed"} and not order["executor_id"]:
        await cb.answer("Сначала назначьте исполнителя", show_alert=True)
        return
    if not can_transition_status(order["status"], status):
        await cb.answer("Недопустимый переход статуса", show_alert=True)
        return
    if not await transition_status(order_id, status):
        await cb.answer("Статус уже изменён или переход недоступен", show_alert=True)
        return
    updated = await get_order(order_id)
    if updated:
        await notify_client_status(cb.bot, order_id, updated["data"], status)
        if updated["executor_id"]:
            await notify_executor_status(cb.bot, order_id, updated["data"], status, executor_id=updated["executor_id"])
        await send_order_to_admin(
            cb.bot,
            order_id,
            updated["data"],
            "Уборка квартиры",
            status=status,
        )
    if not await _render_admin_order_detail(cb, order_id):
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await cb.answer(f"Статус: {STATUS_LABELS.get(status, status)}")


@dp.callback_query(F.data.startswith("admin:photos:client:"))
async def admin_view_client_photos(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 3)[3]
    order = await get_order(order_id)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    client_photos = [photo for photo in photos if photo[2] == "client"]
    if not client_photos:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад к заказу", callback_data=f"admin:order:{order_id}")
        b.adjust(1)
        try:
            await cb.message.edit_text("📭 <b>Нет фотографий помещения</b>", parse_mode="HTML", reply_markup=b.as_markup())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет фотографий помещения</b>", parse_mode="HTML", reply_markup=b.as_markup())
        return
    text = f"📸 <b>Фотографии помещения</b> — {len(client_photos)}\n\n"
    b = InlineKeyboardBuilder()
    for idx, photo in enumerate(client_photos):
        text += f"{idx + 1}. Фото {idx + 1}\n"
        b.button(text=f"🗑 Удалить фото {idx + 1}", callback_data=f"admin:photo:delete:{order_id}:{photo[0]}")
    b.button(text="🔙 Назад к заказу", callback_data=f"admin:order:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:photos:result:"))
async def admin_view_result_photos(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    order_id = cb.data.split(":", 3)[3]
    order = await get_order(order_id)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await cb.answer()
    photos = await get_order_photos(order_id)
    result_photos = [photo for photo in photos if photo[2] == "result"]
    if not result_photos:
        b = InlineKeyboardBuilder()
        b.button(text="🔙 Назад к заказу", callback_data=f"admin:order:{order_id}")
        b.adjust(1)
        try:
            await cb.message.edit_text("📭 <b>Нет фотографий результата</b>", parse_mode="HTML", reply_markup=b.as_markup())
        except TelegramBadRequest:
            await cb.message.answer("📭 <b>Нет фотографий результата</b>", parse_mode="HTML", reply_markup=b.as_markup())
        return
    text = f"📸 <b>Фотографии результата</b> — {len(result_photos)}\n\n"
    b = InlineKeyboardBuilder()
    for idx, photo in enumerate(result_photos):
        text += f"{idx + 1}. Фото {idx + 1}\n"
        b.button(text=f"🗑 Удалить фото {idx + 1}", callback_data=f"admin:photo:delete:{order_id}:{photo[0]}")
    b.button(text="🔙 Назад к заказу", callback_data=f"admin:order:{order_id}")
    b.adjust(1)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:photo:delete:"))
async def admin_delete_photo(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[3]
    photo_id = parts[4]
    order = await get_order(order_id)
    if not order:
        await cb.answer("Заказ не найден", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT photo_type FROM order_photos WHERE id = ? AND order_id = ?", (photo_id, order_id)) as cur:
            row = await cur.fetchone()
        if not row:
            await db.rollback()
            await cb.answer("Фото не найдено", show_alert=True)
            return
        await db.execute("DELETE FROM order_photos WHERE id = ?", (photo_id,))
        await db.execute("UPDATE orders SET updated_at = datetime('now') WHERE id = ?", (order_id,))
        await db.commit()
    await cb.answer("✅ Фото удалено")
    photo_type = row[0]
    if photo_type == "client":
        await admin_view_client_photos(cb)
    else:
        await admin_view_result_photos(cb)


@dp.message(AdminStates.cancel_reason)
async def admin_cancel_reason_handler(msg: Message, state: FSMContext, bot: Bot):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    reason = (msg.text or "").strip()
    if not reason:
        await msg.answer("Укажите причину отмены:")
        return
    data = await state.get_data()
    order_id = data.get("admin_cancel_order_id")
    if not order_id:
        await state.clear()
        await msg.answer("❌ Не удалось определить заказ для отмены.", reply_markup=admin_orders_filter_kb(page=0))
        return
    order = await get_order(order_id)
    if not order:
        await state.clear()
        await msg.answer("❌ Заказ не найден.", reply_markup=admin_orders_filter_kb(page=0))
        return
    if not is_order_modifiable(order["status"]):
        await state.clear()
        await msg.answer("❌ Нельзя отменить заказ в текущем статусе.", reply_markup=admin_orders_filter_kb(page=0))
        return
    order_data = order["data"]
    if not await transition_status(order_id, "cancelled"):
        await state.clear()
        await msg.answer("❌ Не удалось отменить заказ.", reply_markup=admin_orders_filter_kb(page=0))
        return
    updated = await get_order(order_id)
    if updated:
        updated["data"]["cancellation_reason"] = reason
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute("SELECT status FROM orders WHERE id = ?", (order_id,)) as cur:
                row = await cur.fetchone()
            if row and row[0] == "cancelled":
                await db.execute(
                    "UPDATE orders SET data_json = ?, updated_at = datetime('now') WHERE id = ?",
                    (json.dumps(updated["data"], ensure_ascii=False), order_id),
                )
            await db.commit()
        await notify_client_status(bot, order_id, updated["data"], "cancelled", reason=reason)
        if updated.get("executor_id"):
            await notify_executor_status(bot, order_id, updated["data"], "cancelled", executor_id=updated["executor_id"])
        await send_order_to_admin(
            bot,
            order_id,
            updated["data"],
            "Уборка квартиры",
            status="cancelled",
        )
    await state.clear()
    await msg.answer("✅ Заказ отменён.", reply_markup=admin_orders_filter_kb(page=0))


async def notify_executor_status(bot: Bot, order_id: str, data: dict, status: str, executor_id: int = None):
    executor_id = executor_id or data.get("executor_id")
    if not executor_id:
        return
    status_label = STATUS_LABELS.get(status, status)
    text = (
        f"📋 <b>Статус вашего заказа изменён</b>\n\n"
        f"<b>Заказ:</b> {html_str(order_id)}\n"
        f"<b>Новый статус:</b> {html_str(status_label)}\n\n"
        f"<b>Услуга:</b> {html_str(data.get('cleaning_type'))}\n"
        f"<b>Адрес:</b> {html_str(data.get('address'))}\n"
        f"<b>Дата:</b> {html_str(data.get('date'))}\n"
        f"<b>Время:</b> {html_str(data.get('time'))}\n"
    )
    try:
        await bot.send_message(executor_id, text, parse_mode="HTML")
    except Exception:
        if telethon_client:
            try:
                await telethon_client.send_message(executor_id, text, parse_mode="HTML")
            except Exception:
                logger.warning("Failed to notify executor about status", exc_info=True)
        else:
            logger.warning("Failed to notify executor about status", exc_info=True)


@dp.callback_query(F.data.startswith("admin:unassign:"))
async def admin_unassign_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[2]
    order = await get_order_for_mutation(order_id, cb)
    if not order:
        return
    if not order["executor_id"]:
        await cb.answer("Исполнитель не назначен", show_alert=True)
        return
    previous_executor_id = order["executor_id"]
    if not await unassign_executor(order_id):
        await cb.answer("Не удалось снять назначение", show_alert=True)
        return
    updated = await get_order(order_id)
    if updated:
        try:
            await notify_executor_status(cb.bot, order_id, updated["data"], "accepted", executor_id=previous_executor_id)
        except Exception:
            logger.warning("Failed to notify executor about unassign", exc_info=True)
        try:
            await notify_client_status(cb.bot, order_id, updated["data"], "accepted")
        except Exception:
            logger.warning("Failed to notify client about unassign", exc_info=True)
        try:
            await send_order_to_admin(cb.bot, order_id, updated["data"], "Уборка квартиры", status="accepted")
        except Exception:
            logger.warning("Failed to notify admin about unassign", exc_info=True)
    if not await _render_admin_order_detail(cb, order_id):
        await cb.answer("Заказ не найден", show_alert=True)
        return
    await cb.answer("Назначение снято")


@dp.callback_query(F.data.startswith("admin:assign:"))
async def admin_assign_executor(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 4:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    order_id = parts[2]
    try:
        executor_id = int(parts[3])
    except ValueError:
        await cb.answer("Некорректный исполнитель", show_alert=True)
        return
    order = await get_order_for_mutation(order_id, cb)
    if not order:
        return
    if not await assign_executor(order_id, executor_id):
        await cb.answer("Исполнитель не найден, неактивен или назначение недоступно", show_alert=True)
        return
    executor = await get_executor(executor_id)
    updated = await get_order(order_id)
    if updated:
        await notify_executor(cb.bot, order_id, updated["data"])
        await notify_client_status(cb.bot, order_id, updated["data"], "assigned")
        await send_order_to_admin(
            cb.bot,
            order_id,
            updated["data"],
            "Уборка квартиры",
            status="assigned",
        )
    await _render_admin_order_detail(cb, order_id)
    await cb.answer(f"Исполнитель назначен: {executor[1] if executor else executor_id}")


@dp.callback_query(F.data == "admin:stats")
async def admin_stats(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
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
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=admin_orders_filter_kb(page=0))
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=admin_orders_filter_kb(page=0))
    await cb.answer()


@dp.callback_query(F.data == "admin:executors")
async def admin_executors_list(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _render_admin_executors(cb, page=0)


async def _render_admin_executors(cb: CallbackQuery, page: int = 0):
    per_page = 10
    offset = page * per_page
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT telegram_id, full_name, phone, bio, is_active FROM executors ORDER BY created_at DESC LIMIT ? OFFSET ?", (per_page, offset)) as cur:
            executors = await cur.fetchall()
        async with db.execute("SELECT COUNT(*) FROM executors") as cur:
            total = (await cur.fetchone())[0]
    if not executors:
        try:
            await cb.message.edit_text(
                "👥 <b>Нет исполнителей</b>",
                parse_mode="HTML",
                reply_markup=admin_executor_manage_kb(page=0),
            )
        except TelegramBadRequest:
            await cb.message.answer(
                "👥 <b>Нет исполнителей</b>",
                parse_mode="HTML",
                reply_markup=admin_executor_manage_kb(page=0),
            )
        await cb.answer()
        return
    total_pages = max(1, (total + per_page - 1) // per_page)
    text = f"👥 <b>Исполнители</b> — {total}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for ex in executors:
        telegram_id = ex["telegram_id"]
        full_name = ex["full_name"]
        phone = ex["phone"]
        bio = ex.get("bio")
        is_active = ex["is_active"]
        status = "✅ Активен" if is_active else "❌ Деактивирован"
        display_name = full_name or str(telegram_id)
        text += f"👤 <b>{html_str(display_name)}</b>\n"
        text += f"📞 {html_str(phone)}\n"
        if bio:
            text += f"📝 {html_str(bio)}\n"
        avg_data = await get_executor_avg_rating(telegram_id)
        avg = avg_data.get("avg") if avg_data else None
        orders_count = len(await get_orders_by_executor(telegram_id))
        text += f"⭐ {html_str(avg) if avg is not None else '—'} | 📋 {orders_count} заказов\n"
        text += f"📊 {status}\n\n"
        b.button(text=f"{'❌ Деактивировать' if is_active else '✅ Активировать'} {display_name}", callback_data=f"admin:toggle_executor:{telegram_id}")
        b.button(text=f"✏️ Редактировать {display_name}", callback_data=f"admin:edit_executor:{telegram_id}")
        b.button(text=f"📋 Заказы {display_name}", callback_data=f"admin:executor_orders:{telegram_id}")
        b.button(text=f"⭐ Рейтинг {display_name}", callback_data=f"admin:executor:rating:{telegram_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"admin:executors:page:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"admin:executors:page:{page + 1}")
    b.button(text="➕ Добавить исполнителя", callback_data="admin:executor:add")
    b.button(text="🔙 Назад", callback_data="admin:orders:new:0")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data.startswith("admin:executors:page:"))
async def admin_executors_page(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    page = int(cb.data.split(":", 3)[3])
    await _render_admin_executors(cb, page=page)


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
    new_status = not bool(executor[4])
    await toggle_executor_active(executor_id, new_status)
    executors = await get_all_executors()
    text = "👥 <b>Исполнители</b>\n\n"
    b = InlineKeyboardBuilder()
    for ex in executors:
        telegram_id, full_name, phone, bio, is_active = ex
        status = "✅ Активен" if is_active else "❌ Деактивирован"
        display_name = full_name or str(telegram_id)
        text += f"👤 <b>{html_str(display_name)}</b>\n"
        text += f"📞 {html_str(phone)}\n"
        if bio:
            text += f"📝 {html_str(bio)}\n"
        text += f"📊 {status}\n\n"
        b.button(text=f"{'❌ Деактивировать' if is_active else '✅ Активировать'} {display_name}", callback_data=f"admin:toggle_executor:{telegram_id}")
        b.button(text=f"✏️ Редактировать {display_name}", callback_data=f"admin:edit_executor:{telegram_id}")
        b.button(text=f"📋 Заказы {display_name}", callback_data=f"admin:executor_orders:{telegram_id}")
        b.button(text=f"⭐ Рейтинг {display_name}", callback_data=f"admin:executor:rating:{telegram_id}")
    b.button(text="➕ Добавить исполнителя", callback_data="admin:executor:add")
    b.button(text="🔙 Назад", callback_data="admin:orders:new")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer(f"Статус обновлён: {'Активен' if new_status else 'Деактивирован'}")


@dp.callback_query(F.data.startswith("admin:edit_executor:"))
async def admin_edit_executor_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    executor_id = int(cb.data.split(":", 2)[2])
    executor = await get_executor(executor_id)
    if not executor:
        await cb.answer("Исполнитель не найден", show_alert=True)
        return
    await state.update_data(editing_executor_id=executor_id)
    await state.set_state(ExecutorStates.admin_edit_select)
    await cb.answer()
    try:
        await cb.message.edit_text(
            f"✏️ <b>Редактирование исполнителя</b>\n\n"
            f"<b>Имя:</b> {html_str(executor[1])}\n"
            f"<b>Телефон:</b> {html_str(executor[2])}\n"
            f"<b>Описание:</b> {html_str(executor[3] or '—')}\n\n"
            "Выберите поле для изменения:",
            parse_mode="HTML",
            reply_markup=executor_edit_field_kb(executor_id),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            f"✏️ <b>Редактирование исполнителя</b>\n\n"
            f"<b>Имя:</b> {html_str(executor[1])}\n"
            f"<b>Телефон:</b> {html_str(executor[2])}\n"
            f"<b>Описание:</b> {html_str(executor[3] or '—')}\n\n"
            "Выберите поле для изменения:",
            parse_mode="HTML",
            reply_markup=executor_edit_field_kb(executor_id),
        )


@dp.callback_query(F.data.startswith("admin:executor:edit:"))
async def admin_executor_edit_field(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) != 5:
        await cb.answer("Некорректное действие", show_alert=True)
        return
    field = parts[3]
    executor_id = int(parts[4])
    executor = await get_executor(executor_id)
    if not executor:
        await cb.answer("Исполнитель не найден", show_alert=True)
        return
    data = await state.get_data()
    if data.get("editing_executor_id") != executor_id:
        await state.update_data(editing_executor_id=executor_id)
    if field == "name":
        await state.set_state(ExecutorStates.admin_edit_name)
        current = executor[1] or "—"
        try:
            await cb.message.edit_text(
                f"✏️ <b>Изменение имени</b>\n\n"
                f"Текущее имя: {html_str(current)}\n\n"
                "Введите новое имя:",
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await cb.message.answer(
                f"✏️ <b>Изменение имени</b>\n\n"
                f"Текущее имя: {html_str(current)}\n\n"
                "Введите новое имя:",
                parse_mode="HTML",
            )
    elif field == "phone":
        await state.set_state(ExecutorStates.admin_edit_phone)
        current = executor[2] or "—"
        try:
            await cb.message.edit_text(
                f"✏️ <b>Изменение телефона</b>\n\n"
                f"Текущий телефон: {html_str(current)}\n\n"
                "Введите новый номер телефона:",
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await cb.message.answer(
                f"✏️ <b>Изменение телефона</b>\n\n"
                f"Текущий телефон: {html_str(current)}\n\n"
                "Введите новый номер телефона:",
                parse_mode="HTML",
            )
    elif field == "bio":
        await state.set_state(ExecutorStates.admin_edit_bio)
        current = executor[3] or "—"
        try:
            await cb.message.edit_text(
                f"✏️ <b>Изменение описания</b>\n\n"
                f"Текущее описание: {html_str(current)}\n\n"
                "Введите новое описание:",
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            await cb.message.answer(
                f"✏️ <b>Изменение описания</b>\n\n"
                f"Текущее описание: {html_str(current)}\n\n"
                "Введите новое описание:",
                parse_mode="HTML",
            )
    else:
        await cb.answer("Некорректное поле", show_alert=True)
        return
    await cb.answer()


@dp.message(ExecutorStates.admin_edit_name)
async def admin_edit_executor_name(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("Имя не должно быть пустым.")
        return
    data = await state.get_data()
    executor_id = data.get("editing_executor_id")
    if executor_id is None:
        await state.clear()
        await msg.answer("❌ Не удалось определить исполнителя.", reply_markup=admin_orders_filter_kb(page=0))
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE executors SET full_name = ? WHERE telegram_id = ?",
            (name, executor_id),
        )
        await db.commit()
    await state.clear()
    await msg.answer(
        "✅ Имя исполнителя обновлено.",
        reply_markup=admin_orders_filter_kb(page=0),
    )


@dp.message(ExecutorStates.admin_edit_phone)
async def admin_edit_executor_phone(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    phone = (msg.text or "").strip()
    if not phone:
        await msg.answer("Телефон не должен быть пустым.")
        return
    data = await state.get_data()
    executor_id = data.get("editing_executor_id")
    if executor_id is None:
        await state.clear()
        await msg.answer("❌ Не удалось определить исполнителя.", reply_markup=admin_orders_filter_kb(page=0))
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE executors SET phone = ? WHERE telegram_id = ?",
            (phone, executor_id),
        )
        await db.commit()
    await state.clear()
    await msg.answer(
        "✅ Телефон исполнителя обновлён.",
        reply_markup=admin_orders_filter_kb(page=0),
    )


@dp.message(ExecutorStates.admin_edit_bio)
async def admin_edit_executor_bio(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    bio = (msg.text or "").strip()
    if not bio:
        await msg.answer("Описание не должно быть пустым.")
        return
    data = await state.get_data()
    executor_id = data.get("editing_executor_id")
    if executor_id is None:
        await state.clear()
        await msg.answer("❌ Не удалось определить исполнителя.", reply_markup=admin_orders_filter_kb(page=0))
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE executors SET bio = ? WHERE telegram_id = ?",
            (bio, executor_id),
        )
        await db.commit()
    await state.clear()
    await msg.answer(
        "✅ Описание исполнителя обновлено.",
        reply_markup=admin_orders_filter_kb(page=0),
    )


@dp.callback_query(F.data.startswith("admin:executor_orders:"))
async def admin_executor_orders(cb: CallbackQuery):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    if len(parts) < 3:
        await cb.answer("Некорректный запрос", show_alert=True)
        return
    executor_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
    await _render_admin_executor_orders(cb, executor_id, page=page)


async def _render_admin_executor_orders(cb: CallbackQuery, executor_id: int, page: int = 0):
    per_page = 10
    offset = page * per_page
    orders = await get_orders_by_executor(executor_id)
    page_orders = orders[offset:offset + per_page]
    executor = await get_executor(executor_id)
    display_name = executor[1] or str(executor_id) if executor else str(executor_id)
    if not orders:
        try:
            await cb.message.edit_text(
                f"📭 <b>Нет заказов у {html_str(display_name)}</b>",
                parse_mode="HTML",
                reply_markup=admin_executor_manage_kb(page=0),
            )
        except TelegramBadRequest:
            await cb.message.answer(
                f"📭 <b>Нет заказов у {html_str(display_name)}</b>",
                parse_mode="HTML",
                reply_markup=admin_executor_manage_kb(page=0),
            )
        await cb.answer()
        return
    total_pages = max(1, (len(orders) + per_page - 1) // per_page)
    text = f"📋 <b>Заказы {html_str(display_name)}</b> — {len(orders)}\n"
    if total_pages > 1:
        text += f"Страница {page + 1}/{total_pages}\n\n"
    else:
        text += "\n"
    b = InlineKeyboardBuilder()
    for o in page_orders:
        order_id, service, data_json, status, created_at, date, time = o
        try:
            data = json.loads(data_json or "{}")
        except (TypeError, json.JSONDecodeError):
            data = {}
        flat_type = data.get("flat_type", "—")
        price = calc_price_estimate(flat_type, data.get("cleaning_type", ""))
        text += f"🆕 <b>{html_str(order_id)}</b> | {html_str(service)}\n"
        text += f"📅 {html_str(data.get('date'))} ⏰ {html_str(data.get('time'))}\n"
        text += f"📍 {html_str(data.get('address'))}\n"
        text += f"💰 {html_str(price)}\n"
        text += f"📊 {html_str(STATUS_LABELS.get(status, status))}\n\n"
        b.button(text=f"📋 Детали {order_id}", callback_data=f"admin:order:{order_id}")
    if page > 0:
        b.button(text="⬅️ Назад", callback_data=f"admin:executor_orders:{executor_id}:{page - 1}")
    if (page + 1) < total_pages:
        b.button(text="➡️ Далее", callback_data=f"admin:executor_orders:{executor_id}:{page + 1}")
    b.button(text="🔙 К исполнителю", callback_data=f"admin:edit_executor:{executor_id}")
    b.adjust(2)
    try:
        await cb.message.edit_text(text, parse_mode="HTML", reply_markup=b.as_markup())
    except TelegramBadRequest:
        await cb.message.answer(text, parse_mode="HTML", reply_markup=b.as_markup())
    await cb.answer()


@dp.callback_query(F.data == "admin:executor:add")
async def admin_add_executor_start(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id != cfg.admin_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    await cb.answer()
    await state.clear()
    await state.set_state(ExecutorStates.admin_add_id)
    try:
        await cb.message.edit_text(
            "➕ <b>Добавление исполнителя</b>\n\n"
            "Введите Telegram ID исполнителя:",
            parse_mode="HTML",
            reply_markup=admin_executor_add_kb(),
        )
    except TelegramBadRequest:
        await cb.message.answer(
            "➕ <b>Добавление исполнителя</b>\n\n"
            "Введите Telegram ID исполнителя:",
            parse_mode="HTML",
            reply_markup=admin_executor_add_kb(),
        )


@dp.message(ExecutorStates.admin_add_id)
async def admin_add_executor_id(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    try:
        executor_id = int((msg.text or "").strip())
        if executor_id <= 0:
            raise ValueError
    except ValueError:
        await msg.answer("Введите положительный числовой Telegram ID.")
        return
    await state.update_data(admin_executor_id=executor_id)
    await state.set_state(ExecutorStates.admin_add_name)
    await msg.answer("Введите имя исполнителя:")


@dp.message(ExecutorStates.admin_add_name)
async def admin_add_executor_name(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("Имя не должно быть пустым.")
        return
    await state.update_data(admin_executor_name=name)
    await state.set_state(ExecutorStates.admin_add_phone)
    await msg.answer("Введите номер телефона исполнителя:")


@dp.message(ExecutorStates.admin_add_phone)
async def admin_add_executor_phone(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    phone = (msg.text or "").strip()
    if not phone:
        await msg.answer("Телефон не должен быть пустым.")
        return
    await state.update_data(admin_executor_phone=phone)
    await state.set_state(ExecutorStates.admin_add_bio)
    await msg.answer("Введите описание/направление исполнителя (или «-» чтобы пропустить):")


@dp.message(ExecutorStates.admin_add_bio)
async def admin_add_executor_bio(msg: Message, state: FSMContext):
    if msg.from_user.id != cfg.admin_chat_id:
        return
    bio = (msg.text or "").strip()
    if not bio or bio == "-":
        bio = None
    data = await state.get_data()
    executor_id = data.get("admin_executor_id")
    await add_executor(
        executor_id,
        data.get("admin_executor_name", ""),
        data.get("admin_executor_phone", ""),
        bio=bio,
        is_active=True,
        update_active=True,
    )
    await state.clear()
    await msg.answer(
        f"✅ Исполнитель {html_str(data.get('admin_executor_name'))} добавлен и активирован.",
        parse_mode="HTML",
        reply_markup=admin_orders_filter_kb(page=0),
    )


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
    try:
        await delete_abandoned_order(msg.from_user.id)
    except Exception:
        logger.warning("Failed to delete abandoned order on navigation", exc_info=True)
    await state.clear()
    await msg.answer("Главное меню", reply_markup=main_menu())


@dp.message(F.text == "❌ Отменить заказ")
async def cancel_order_handler(msg: Message, state: FSMContext):
    current = await state.get_state()
    if current and _is_cleaning_state(current):
        await state.clear()
        try:
            await delete_abandoned_order(msg.from_user.id)
        except Exception:
            logger.warning("Failed to delete abandoned order on cancellation", exc_info=True)
        await msg.answer(ORDER_CANCELLED_TEXT, reply_markup=main_menu())
    elif current and _state_name(current) == ExecutorStates.menu.state:
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


async def _build_bot_session(proxy_url: str | None) -> "AiohttpSession | None":
    """Создаёт сессию с прокси, только если прокси задан и реально доступен.

    Прокси — необязательная настройка. Если он не указан, недоступен или
    настроить его не удалось, возвращается None и aiogram использует
    обычное прямое подключение.
    """
    if not proxy_url:
        print("🔌 Подключение без прокси")
        return None

    raw = proxy_url.strip()
    parsed = urlparse(raw if "://" in raw else f"socks5://{raw}")
    host = parsed.hostname
    default_port = 1080 if parsed.scheme.startswith("socks") else 8080
    port = parsed.port or default_port

    if not host:
        print(f"⚠️ Некорректный TELEGRAM_PROXY: {raw} — подключаюсь без прокси")
        return None

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=PROXY_PROBE_TIMEOUT
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️ Прокси {host}:{port} недоступен ({e}) — подключаюсь без прокси")
        return None

    try:
        return AiohttpSession(proxy=raw)
    except Exception as e:
        print(f"⚠️ Не удалось настроить прокси {raw} ({e}) — подключаюсь без прокси")
        return None


async def main():
    global telethon_client

    print("⚙️ Запуск бота...")
    print("⚙️ Инициализация базы данных...")
    await init_db()

    session = await _build_bot_session(cfg.telegram_proxy)

    bot = Bot(
        token=cfg.bot_token,
        session=session,
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
        id="ltv_reminders",
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
