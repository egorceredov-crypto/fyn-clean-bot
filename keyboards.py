from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from metro_data import METRO

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✨ Заказать уборку", style="success")],
            [KeyboardButton(text="🧹 Услуги и цены"), KeyboardButton(text="📋 Мои заказы")],
            [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="✨ О сервисе")],
            [KeyboardButton(text="🎁 Пригласить друга"), KeyboardButton(text="🌟 Отзывы")],
            [KeyboardButton(text="💬 Поддержка"), KeyboardButton(text="📞 Связаться")],
        ],
        resize_keyboard=True
    )

def order_cancel_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить заказ")],
        ],
        resize_keyboard=True
    )


def order_step_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить заказ")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True
    )

def executor_menu_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Мои заказы")],
            [KeyboardButton(text="⭐ Мой рейтинг")],
            [KeyboardButton(text="🔙 В главное меню")],
        ],
        resize_keyboard=True
    )

def metro_lines_kb(prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for idx, line in enumerate(METRO.keys()):
        b.button(text=line, callback_data=f"{prefix}:line:{idx}")
    b.button(text="✍️ Другое метро (ввести)", callback_data=f"{prefix}:line:manual")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(1)
    return b.as_markup()


def metro_stations_kb(prefix: str, line: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    stations = METRO.get(line, [])
    for idx, st in enumerate(stations):
        b.button(text=st, callback_data=f"{prefix}:st:{idx}")
    b.button(text="⬅️ Назад к линиям", callback_data=f"{prefix}:back_lines")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="✍️ Другое метро (ввести)", callback_data=f"{prefix}:st:manual")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(2)
    return b.as_markup()

def date_selection_kb() -> InlineKeyboardMarkup:
    from datetime import datetime, timedelta
    b = InlineKeyboardBuilder()
    today = datetime.now().date()
    for i in range(7):
        d = today + timedelta(days=i)
        label = "Сегодня" if i == 0 else "Завтра" if i == 1 else d.strftime("%d.%m")
        b.button(text=label, callback_data=f"order:date:{d.isoformat()}")
    b.button(text="✍️ Другая дата", callback_data="order:date:manual")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(3)
    return b.as_markup()

def time_selection_kb(work_start: str = "09:00", work_end: str = "21:00") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    start_hour = int(work_start.split(":")[0])
    end_hour = int(work_end.split(":")[0])
    for hour in range(start_hour, end_hour):
        slot = f"{hour:02d}:00"
        b.button(text=slot, callback_data=f"order:time:{slot}")
    b.button(text="✍️ Другое время", callback_data="order:time:manual")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(4)
    return b.as_markup()

def phone_request_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить номер телефона", request_contact=True)],
            [KeyboardButton(text="❌ Отменить заказ")],
            [KeyboardButton(text="🔙 Назад")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def photo_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📸 Добавить фото", callback_data="order:photo:add")
    b.button(text="🗑 Удалить все фото", callback_data="order:photo:clear")
    b.button(text="⏭ Пропустить", callback_data="order:photo:skip")
    b.button(text="📋 Список фото", callback_data="order:photo:list")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(2)
    return b.as_markup()


def photo_delete_kb(photo_idx: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 Удалить это фото", callback_data=f"order:photo:delete:{photo_idx}")
    b.button(text="🔙 Назад к списку", callback_data="order:photo:list")
    b.adjust(1)
    return b.as_markup()

def preview_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Подтвердить заказ", callback_data="order:preview:confirm")
    b.button(text="✏️ Редактировать", callback_data="order:preview:edit")
    b.button(text="❌ Отменить", callback_data="order:preview:cancel")
    b.adjust(1)
    return b.as_markup()

def edit_field_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🪄 Услуга", callback_data="order:edit:cleaning_type")
    b.button(text="🏠 Тип квартиры", callback_data="order:edit:flat_type")
    b.button(text="📅 Дата", callback_data="order:edit:date")
    b.button(text="⏰ Время", callback_data="order:edit:time")
    b.button(text="📍 Метро", callback_data="order:edit:metro")
    b.button(text="📍 Адрес", callback_data="order:edit:address")
    b.button(text="📱 Телефон", callback_data="order:edit:phone")
    b.button(text="👤 Имя/ФИО", callback_data="order:edit:name")
    b.button(text="💬 Комментарий", callback_data="order:edit:comment")
    b.button(text="📸 Фото", callback_data="order:edit:photos")
    b.button(text="🔙 Назад к preview", callback_data="order:edit:back")
    b.adjust(2)
    return b.as_markup()

def my_orders_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📌 Активные", callback_data="my_orders:active:0")
    b.button(text="📜 История", callback_data="my_orders:history:0")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(2)
    return b.as_markup()

def order_detail_kb(order_id: str, status: str, has_rating: bool = False, executor_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status not in ("completed", "cancelled"):
        b.button(text="❌ Отменить заказ", callback_data=f"my_orders:cancel:{order_id}")
    if status == "completed" and not has_rating:
        b.button(text="⭐ Оценить", callback_data=f"my_orders:rate:{order_id}")
    if status == "completed" and executor_id:
        b.button(text="📊 Рейтинг исполнителя", callback_data=f"executor:rating:{executor_id}")
    if status == "completed":
        b.button(text="🔄 Повторить заказ", callback_data=f"my_orders:repeat:{order_id}")
    b.button(text="📸 Фото помещения", callback_data=f"my_orders:photos:client:{order_id}")
    b.button(text="📸 Фото результата", callback_data=f"my_orders:photos:result:{order_id}")
    b.button(text="🔙 Назад", callback_data="my_orders:back")
    b.adjust(1)
    return b.as_markup()

def admin_status_kb(
    order_id: str,
    status: str | None = None,
    executor_id: int | None = None,
    back_callback: str = "admin:orders:new:0",
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status is None:
        b.button(text="✅ Принят", callback_data=f"admin:status:{order_id}:accepted")
        b.button(text="👤 Назначить", callback_data=f"admin:assign_executor:{order_id}")
        b.button(text="🔄 В работе", callback_data=f"admin:status:{order_id}:in_work")
        b.button(text="🎯 Выполнен", callback_data=f"admin:status:{order_id}:completed")
        b.button(text="❌ Отменён", callback_data=f"admin:status:{order_id}:cancelled")
    else:
        if status == "new":
            b.button(text="✅ Принять", callback_data=f"admin:status:{order_id}:accepted")
        if status in {"new", "accepted", "assigned", "contacted"}:
            b.button(text="👤 Назначить", callback_data=f"admin:assign_executor:{order_id}")
        if status in {"accepted", "assigned", "contacted"} and executor_id:
            b.button(text="🔄 В работу", callback_data=f"admin:status:{order_id}:in_work")
        if status == "in_work":
            b.button(text="🎯 Выполнить", callback_data=f"admin:status:{order_id}:completed")
        if status in {"new", "accepted", "assigned", "in_work", "contacted"}:
            b.button(text="❌ Отменить", callback_data=f"admin:status:{order_id}:cancelled")
        if executor_id and status not in {"completed", "cancelled"}:
            b.button(text="🚫 Снять назначение", callback_data=f"admin:unassign:{order_id}")
    b.button(text="📸 Фото помещения", callback_data=f"admin:photos:client:{order_id}")
    b.button(text="📸 Фото результата", callback_data=f"admin:photos:result:{order_id}")
    b.button(text="🔙 К списку", callback_data=back_callback)
    b.adjust(2)
    return b.as_markup()

def executor_order_actions_kb(order_id: str, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "assigned":
        b.button(text="🔄 Взять в работу", callback_data=f"exec:status:{order_id}:in_work")
    if status == "in_work":
        b.button(text="🎯 Завершить", callback_data=f"exec:status:{order_id}:completed")
    if status in ("in_work", "completed"):
        b.button(text="📸 Отправить фото результата", callback_data=f"exec:photo:{order_id}")
    b.button(text="📸 Фото помещения", callback_data=f"exec:photos:client:{order_id}")
    b.button(text="📸 Фото результата", callback_data=f"exec:photos:result:{order_id}")
    b.button(text="🔙 Назад", callback_data="exec:orders:back")
    b.adjust(2)
    return b.as_markup()

def rating_kb(order_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for i in range(1, 6):
        stars = "⭐" * i
        b.button(text=stars, callback_data=f"rating:{order_id}:{i}")
    b.adjust(5)
    return b.as_markup()

def individual_cleaning_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✨ Заказать уборку", callback_data="individual:order")
    b.button(text="🔙 Назад", callback_data="individual:back")
    b.adjust(1)
    return b.as_markup()

def admin_orders_filter_kb(page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🆕 Новые", callback_data=f"admin:orders:new:{page}")
    b.button(text="📌 Активные", callback_data=f"admin:orders:active:{page}")
    b.button(text="✅ Выполненные", callback_data=f"admin:orders:completed:{page}")
    b.button(text="❌ Отменённые", callback_data=f"admin:orders:cancelled:{page}")
    b.button(text="👥 Исполнители", callback_data="admin:executors")
    b.button(text="📊 Статистика", callback_data="admin:stats")
    b.adjust(2)
    return b.as_markup()


def admin_executor_manage_kb(page: int = 0) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить исполнителя", callback_data="admin:executor:add")
    b.button(text="🔙 Назад", callback_data=f"admin:orders:new:{page}")
    b.adjust(1)
    return b.as_markup()


def admin_executor_add_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 К исполнителям", callback_data="admin:executors")
    b.adjust(1)
    return b.as_markup()


def executor_assign_kb(order_id: str, executors: list) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for ex in executors:
        b.button(
            text=ex[1] or str(ex[0]),
            callback_data=f"admin:assign:{order_id}:{ex[0]}",
        )
    b.button(text="🔙 Назад", callback_data=f"admin:order:{order_id}")
    b.adjust(1)
    return b.as_markup()


def executor_edit_field_kb(executor_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="👤 Имя", callback_data=f"admin:executor:edit:name:{executor_id}")
    b.button(text="📱 Телефон", callback_data=f"admin:executor:edit:phone:{executor_id}")
    b.button(text="📝 Описание", callback_data=f"admin:executor:edit:bio:{executor_id}")
    b.button(text="🔙 Назад", callback_data=f"admin:executor:{executor_id}")
    b.adjust(1)
    return b.as_markup()

def confirm_kb(prefix: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Отправить заявку", callback_data=f"{prefix}:confirm:send")
    b.button(text="✏️ Изменить (заново)", callback_data=f"{prefix}:confirm:restart")
    b.adjust(1)
    return b.as_markup()

def cleaning_type_kb():
    b = InlineKeyboardBuilder()
    b.button(text="🪄 Поддерживающая уборка", callback_data="cleaning:type:regular")
    b.button(text="✨ Генеральная уборка", callback_data="cleaning:type:general")
    b.button(text="🛠 После ремонта", callback_data="cleaning:type:after")
    b.button(text="🛋 Химчистка мебели", callback_data="cleaning:type:hym2")
    b.button(text="🏠 Индивидуальная уборка", callback_data="cleaning:type:individual")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(1)
    return b.as_markup()


def name_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Пропустить", callback_data="order:name:skip")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(1)
    return b.as_markup()


def flat_type_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="1-комнатная", callback_data="flat:1k")
    b.button(text="2-комнатная", callback_data="flat:2k")
    b.button(text="3-комнатная", callback_data="flat:3k")
    b.button(text="Студия / площадь", callback_data="flat:other")
    b.button(text="🔙 Назад", callback_data="order:back")
    b.button(text="❌ Отменить", callback_data="order:cancel")
    b.adjust(2)
    return b.as_markup()

