from __future__ import annotations

import calendar
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.services import CarFilters


def main_reply_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🚗 Каталог авто"), KeyboardButton(text="📋 Мои бронирования")],
        [KeyboardButton(text="👤 Мой профиль"), KeyboardButton(text="📞 Контакты"), KeyboardButton(text="ℹ️ О нас")],
    ]
    if is_admin:
        rows.append([KeyboardButton(text="🛠 Админ-панель")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def admin_inline_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚗 Добавить авто", callback_data="admin:add_car")],
            [InlineKeyboardButton(text="📋 Список авто", callback_data="admin:cars")],
            [InlineKeyboardButton(text="📅 Закрыть даты", callback_data="admin:blackouts")],
            [InlineKeyboardButton(text="📋 Брони", callback_data="admin:bookings")],
            [InlineKeyboardButton(text="📋 Фильтры бронирования", callback_data="admin:bookings_filters")],
            [InlineKeyboardButton(text="🗑 Отменённые брони", callback_data="admin:bookings_cancelled")],
            [InlineKeyboardButton(text="📤 Экспорт броней (CSV)", callback_data="admin:export_bookings")],
        ]
    )


def filters_encode(f: CarFilters | None) -> str:
    if not f or (not f.price and not f.transmission):
        return "N-N"
    p = f.price or "N"
    t = f.transmission or "N"
    return f"{p}:{t}"


def filters_decode(s: str) -> CarFilters:
    if not s or s == "N-N":
        return CarFilters()
    parts = s.split(":", 1)
    p = parts[0] if parts[0] != "N" else None
    t = parts[1] if len(parts) > 1 and parts[1] != "N" else None
    return CarFilters(price=p, transmission=t)


def catalog_pager_kb(page: int, total_pages: int, fcode: str) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    if page > 1:
        row.append(InlineKeyboardButton(text="◀️ Назад", callback_data=f"cat:p:{page - 1}:{fcode}"))
    row.append(InlineKeyboardButton(text=f"Стр {page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        row.append(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"cat:p:{page + 1}:{fcode}"))
    rows = [row, [InlineKeyboardButton(text="Фильтры", callback_data=f"cat:f:{fcode}")]]
    if fcode != "N-N":
        rows.append([InlineKeyboardButton(text="Сбросить фильтры", callback_data="cat:clr")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_list_kb(*, cars: list, page: int, total_pages: int, fcode: str) -> InlineKeyboardMarkup:
    """Кнопки каталога: список авто + пагинация (одно редактируемое сообщение)."""
    rows: list[list[InlineKeyboardButton]] = []
    for car in cars:
        rows.append([InlineKeyboardButton(text=f"🚗 {car.name}", callback_data=f"cat:open:{car.id}:{page}:{fcode}")])

    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="◀️", callback_data=f"cat:p:{page - 1}:{fcode}"))
    pager.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        pager.append(InlineKeyboardButton(text="▶️", callback_data=f"cat:p:{page + 1}:{fcode}"))
    rows.append(pager)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def catalog_detail_kb(*, car_id: int, page: int, fcode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Забронировать", callback_data=f"car:b:{car_id}")],
            [InlineKeyboardButton(text="⬅️ Назад в каталог", callback_data=f"cat:back:{page}:{fcode}")],
        ]
    )


def car_card_kb(car_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Забронировать", callback_data=f"car:b:{car_id}"),
                InlineKeyboardButton(text="Подробнее", callback_data=f"car:d:{car_id}"),
            ]
        ]
    )


def admin_cars_list_kb(*, cars: list, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for car in cars:
        avail = "✅" if getattr(car, "is_available", False) else "🚫"
        rows.append([InlineKeyboardButton(text=f"{avail} {car.name}", callback_data=f"admin:car:{car.id}:p:{page}")])
    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:cars:p:{page - 1}"))
    pager.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        pager.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:cars:p:{page + 1}"))
    rows.append(pager)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_car_manage_kb(*, car_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Название", callback_data=f"admin:edit:{car_id}:name:{page}"),
                InlineKeyboardButton(text="✏️ Описание", callback_data=f"admin:edit:{car_id}:desc:{page}"),
            ],
            [
                InlineKeyboardButton(text="💰 Цена", callback_data=f"admin:edit:{car_id}:price:{page}"),
                InlineKeyboardButton(text="🖼 Фото", callback_data=f"admin:edit:{car_id}:photo:{page}"),
            ],
            [
                InlineKeyboardButton(text="⚙️ Двигатель", callback_data=f"admin:edit:{car_id}:engine:{page}"),
                InlineKeyboardButton(text="🔁 Коробка", callback_data=f"admin:edit:{car_id}:trans:{page}"),
            ],
            [
                InlineKeyboardButton(text="👥 Места", callback_data=f"admin:edit:{car_id}:seats:{page}"),
            ],
            [
                InlineKeyboardButton(text="❄️ Кондиционер", callback_data=f"admin:edit:{car_id}:ac:{page}"),
                InlineKeyboardButton(text="✅/🚫 Доступность", callback_data=f"admin:edit:{car_id}:avail:{page}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:del:{car_id}:{page}"),
                InlineKeyboardButton(text="⬅️ К списку", callback_data=f"admin:cars:p:{page}"),
            ],
        ]
    )


def admin_confirm_delete_kb(*, car_id: int, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, удалить", callback_data=f"admin:del_ok:{car_id}:{page}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"admin:car:{car_id}:p:{page}"),
            ]
        ]
    )


def admin_pick_car_kb(*, cars: list, prefix: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for car in cars:
        rows.append([InlineKeyboardButton(text=f"🚗 {car.name}", callback_data=f"{prefix}:{car.id}:p:{page}")])
    pager: list[InlineKeyboardButton] = []
    if page > 1:
        pager.append(InlineKeyboardButton(text="◀️", callback_data=f"{prefix}:page:{page - 1}"))
    pager.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        pager.append(InlineKeyboardButton(text="▶️", callback_data=f"{prefix}:page:{page + 1}"))
    rows.append(pager)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)




def admin_booking_manage_kb(booking_id, page, extra):
    buttons = []

    # Кнопка отмены
    buttons.append([
        InlineKeyboardButton(
            text="❌ Отменить бронь",
            callback_data=f"admin:bk:cancel:{booking_id}:{page}"
        )
    ])

    # Добавляем extra-кнопки, если есть
    if extra:
        buttons.extend(extra)

    return InlineKeyboardMarkup(inline_keyboard=buttons)




def admin_confirm_cancel_booking_kb(*, booking_id: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Да, отменить", callback_data=f"admin:bk:cx_ok:{booking_id}:{page}"),
                InlineKeyboardButton(text="Нет", callback_data=f"admin:bk:{booking_id}:p:{page}"),
            ]
        ]
    )


def filters_kb(fcode: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="До 2000", callback_data=f"cat:fp:lt_2000:{fcode}"),
                InlineKeyboardButton(text="2000–4000", callback_data=f"cat:fp:between_2000_4000:{fcode}"),
            ],
            [InlineKeyboardButton(text="От 4000", callback_data=f"cat:fp:gte_4000:{fcode}")],
            [
                InlineKeyboardButton(text="Автомат", callback_data=f"cat:ft:automatic:{fcode}"),
                InlineKeyboardButton(text="Механика", callback_data=f"cat:ft:manual:{fcode}"),
            ],
            [InlineKeyboardButton(text="Назад в каталог", callback_data="cat:back")],
        ]
    )


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def _dcompact(d: date) -> str:
    return d.strftime("%Y%m%d")


def calendar_kb(
    prefix: str,
    min_day: date,
    *,
    car_id: int,
    blocked_days: set[date] | None = None,
    view: date | None = None,
) -> InlineKeyboardMarkup:
    """prefix: s (start) или e (end); min_day — первый доступный день."""
    view = view or _month_start(min_day)
    year, month = view.year, view.month
    _, last_day = calendar.monthrange(year, month)

    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, 1)
    mc = _dcompact(min_day)
    nav_row = [
        InlineKeyboardButton(
            text="◀️",
            callback_data=f"cal:nav:{prefix}:{car_id}:{py}:{pm}:{mc}",
        ),
        InlineKeyboardButton(text=f"{month:02d}.{year}", callback_data="noop"),
        InlineKeyboardButton(
            text="▶️",
            callback_data=f"cal:nav:{prefix}:{car_id}:{ny}:{nm}:{mc}",
        ),
    ]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    header = [InlineKeyboardButton(text=d, callback_data="noop") for d in weekdays]

    first_weekday = date(year, month, 1).weekday()  # Mon=0
    rows: list[list[InlineKeyboardButton]] = [nav_row, header]
    current_row: list[InlineKeyboardButton] = []

    for pad in range(first_weekday):
        current_row.append(InlineKeyboardButton(text=" ", callback_data="noop"))

    for day in range(1, last_day + 1):
        cell = date(year, month, day)
        label = f"{day:02d}"
        is_blocked = blocked_days is not None and cell in blocked_days
        if cell < min_day or is_blocked:
            # Можно визуально пометить занятые/закрытые даты
            shown = f"⛔{label}" if is_blocked else label
            current_row.append(InlineKeyboardButton(text=shown, callback_data="noop"))
        else:
            current_row.append(
                InlineKeyboardButton(text=label, callback_data=f"cal:pick:{prefix}:{_dcompact(cell)}")
            )
        if len(current_row) == 7:
            rows.append(current_row)
            current_row = []
    if current_row:
        while len(current_row) < 7:
            current_row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        rows.append(current_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_booking_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Подтвердить бронь", callback_data="rent:ok"),
                InlineKeyboardButton(text="Отмена", callback_data="rent:cancel"),
            ]
        ]
    )


def pay_stub_kb(booking_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Оплатить", callback_data=f"pay:{booking_id}")]]
    )

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def admin_booking_list_kb(bookings, page, total_pages):
    kb = InlineKeyboardMarkup(inline_keyboard=[])

    # список броней
    for b in bookings:
        kb.inline_keyboard.append([
            InlineKeyboardButton(
                text=f"#{b.id} — {b.start_date:%d.%m} → {b.end_date:%d.%m}",
                callback_data=f"admin:bk:{b.id}:{page}"
            )
        ])

    # навигация
    nav = []
    if page > 1:
        nav.append(
            InlineKeyboardButton(
                text="⬅️",
                callback_data=f"admin:bookings:p:{page-1}"
            )
        )
    if page < total_pages:
        nav.append(
            InlineKeyboardButton(
                text="➡️",
                callback_data=f"admin:bookings:p:{page+1}"
            )
        )

    if nav:
        kb.inline_keyboard.append(nav)

    return kb
