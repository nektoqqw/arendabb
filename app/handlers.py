from __future__ import annotations

import logging
from aiogram.types import BufferedInputFile
from sqlalchemy import delete, select
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.fsm.state import StatesGroup, State
from aiogram.filters import Command
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.exceptions import TelegramBadRequest

from app.config import settings
from app.keyboards import (
    admin_booking_list_kb,
    admin_booking_manage_kb,
    admin_car_manage_kb,
    admin_cars_list_kb,
    admin_confirm_cancel_booking_kb,
    admin_confirm_delete_kb,
    admin_inline_menu,
    admin_pick_car_kb,
    calendar_kb,
    catalog_detail_kb,
    catalog_list_kb,
    confirm_booking_kb,
    filters_decode,
    filters_encode,
    filters_kb,
    main_reply_menu,
    pay_stub_kb,
)
from app.models import Booking, BookingStatus
from app.services import (
    CarFilters,
    aggregate_user_stats,
    add_blackout_range,
    cancel_booking_admin,
    cancel_booking_user,
    create_booking,
    create_car,
    delete_car,
    diff_rental_days,
    get_car,
    get_booking,
    get_user_bookings,
    list_cars_page,
    list_cars_admin_page,
    list_blocked_days_for_month,
    list_bookings_admin_page,
    update_car_fields,
    upsert_user,
    has_overlapping_booking,      # ← обязательно с запятой
    has_overlapping_blackout,     # ← обязательно с запятой
)

from app.states import (
    AdminAddCarState,
    AdminBlackoutState,
    AdminEditCarState,
    ProfileState,
    RegistrationState,
    RentalBookingState,
)

logger = logging.getLogger(__name__)
router = Router()
ERR = "Что-то пошло не так. Попробуйте позже."


def _log_event(user_id: int | None, event: str, **data) -> None:
    safe = {k: (str(v)[:300] if v is not None else None) for k, v in data.items()}
    logger.info("event=%s user=%s data=%s", event, user_id, safe)


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admins


def _status_ru(status: str) -> str:
    m = {
        BookingStatus.waiting_payment.value: "⏰ Ожидает оплаты",
        BookingStatus.confirmed.value: "✅ Подтверждена",
        BookingStatus.completed.value: "✅ Завершена",
        BookingStatus.cancelled.value: "❌ Отменена",
    }
    return m.get(status, status)


def _format_car_html(car) -> str:
    trans = "автомат" if car.transmission == "automatic" else "механика"
    ac = "есть" if car.has_ac else "нет"
    desc = car.description or "Без описания"
    return (
        f"🚗 <b>{car.name}</b>\n"
        f"{desc}\n"
        f"💰 Цена: <b>{car.price_per_day}₽/сутки</b>\n"
        f"Двигатель: {car.engine}\n"
        f"Коробка: {trans}\n"
        f"Мест: {car.seats}\n"
        f"Кондиционер: {ac}"
    )


async def _notify_admins(bot: Bot, text: str) -> None:
    for aid in settings.admins:
        try:
            await bot.send_message(aid, text)
        except Exception as exc:
            logger.warning("admin notify %s: %s", aid, exc)


async def _menu_for_user(message: Message, user_id: int) -> None:
    await message.answer("Главное меню:", reply_markup=main_reply_menu(is_admin=_is_admin(user_id)))


def _registration_complete(user) -> bool:
    name = (user.registered_name or "").strip()
    phone = (user.phone or "").strip()
    return len(name) >= 2 and len(phone) >= 10


def _display_name(user) -> str:
    n = (user.registered_name or "").strip()
    return n or user.first_name or "Клиент"


async def _require_registration_or_prompt(message: Message, session, state: FSMContext) -> bool:
    if not message.from_user:
        await message.answer(ERR)
        return False
    user = await upsert_user(
        session,
        message.from_user.id,
        message.from_user.first_name,
        message.from_user.last_name,
        message.from_user.username,
    )
    if _registration_complete(user):
        return True
    reg_name = (user.registered_name or "").strip()
    if len(reg_name) < 2:
        await state.set_state(RegistrationState.waiting_name)
        await message.answer(
            "Сначала завершите регистрацию. Введите <b>ваше имя</b> (как в договоре или как вам удобно):"
        )
    else:
        await state.set_state(RegistrationState.waiting_phone)
        await state.update_data(reg_name=reg_name)
        await message.answer("Сначала завершите регистрацию. Введите <b>номер телефона</b> (вручную):")
    return False


@router.message(CommandStart())
async def cmd_start(message: Message, session, state: FSMContext) -> None:
    try:
        if not message.from_user:
            await message.answer(ERR)
            return
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        if not _registration_complete(user):
            reg_name = (user.registered_name or "").strip()
            if len(reg_name) < 2:
                await state.set_state(RegistrationState.waiting_name)
                await message.answer(
                    "✅ <b>Добро пожаловать в Аренду автомобилей!</b>\n"
                    "Укажите <b>ваше имя</b> (введите текстом, как вам удобно):"
                )
                return
            await state.set_state(RegistrationState.waiting_phone)
            await state.update_data(reg_name=reg_name)
            await message.answer(
                "Теперь введите <b>номер телефона</b> (текстом, например +7… или 8…):"
            )
            return
        await message.answer("✅ <b>Добро пожаловать в Аренду автомобилей!</b>")
        await _menu_for_user(message, message.from_user.id)
    except Exception:
        logger.exception("start")
        await message.answer(ERR)


@router.message(RegistrationState.waiting_name, F.text)
async def registration_name_text(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Имя слишком короткое. Введите хотя бы 2 символа.")
        return
    await state.update_data(reg_name=name)
    await state.set_state(RegistrationState.waiting_phone)
    await message.answer("Введите <b>номер телефона</b> (текстом):")


@router.message(RegistrationState.waiting_phone, F.text)
async def registration_phone_text(message: Message, session, state: FSMContext) -> None:
    data = await state.get_data()
    reg_name = (data.get("reg_name") or "").strip()
    if len(reg_name) < 2:
        await state.set_state(RegistrationState.waiting_name)
        await message.answer("Сначала введите имя.")
        return
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("Введите корректный номер телефона.")
        return
    try:
        if not message.from_user:
            await message.answer(ERR)
            return
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        user.registered_name = reg_name
        user.phone = text
        await session.commit()
        await state.clear()
        await message.answer("✅ Регистрация завершена. Имя и телефон сохранены.")
        await _menu_for_user(message, message.from_user.id)
    except Exception:
        logger.exception("registration_phone_text")
        await message.answer(ERR)


@router.message(F.text == "🚗 Каталог авто")
async def open_catalog(message: Message, session, state: FSMContext) -> None:
    _log_event(getattr(message.from_user, "id", None), "open_catalog", text=message.text)
    if not await _require_registration_or_prompt(message, session, state):
        return
    await _send_or_edit_catalog_list(message=message, session=session, page=1, filters=None, edit=False)


@router.message(F.text == "📋 Мои бронирования")
async def my_bookings_msg(message: Message, session, state: FSMContext) -> None:
    try:
        if not await _require_registration_or_prompt(message, session, state):
            return
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        bookings = await get_user_bookings(session, user.id)
        if not bookings:
            await message.answer("📋 У вас пока нет бронирований.")
            return
        for b in bookings:
            lines = [
                f"📋 <b>Бронь #{b.id[:8]}</b>",
                f"🚗 {b.car.name}",
                f"📅 {b.start_date:%d.%m.%Y} - {b.end_date:%d.%m.%Y}",
                f"💰 {b.total_price}₽",
                _status_ru(b.status),
            ]
            kb = None
            if b.status in (BookingStatus.waiting_payment.value, BookingStatus.confirmed.value):
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Отменить бронь", callback_data=f"bk:cx:{b.id}")]]
                )
            await message.answer("\n".join(lines), reply_markup=kb)
    except Exception:
        logger.exception("my_bookings")
        await message.answer(ERR)


@router.message(F.text == "👤 Мой профиль")
async def profile_msg(message: Message, session, state: FSMContext) -> None:
    try:
        if not await _require_registration_or_prompt(message, session, state):
            return
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        cnt, total = await aggregate_user_stats(session, user.id)
        un = f"@{user.username}" if user.username else "не указан"
        phone = user.phone or "не указан"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Изменить телефон", callback_data="prof:phone")]]
        )
        await message.answer(
            "\n".join(
                [
                    "👤 <b>Ваш профиль</b>",
                    f"Имя: {_display_name(user)}",
                    f"Username: {un}",
                    f"📞 Телефон: {phone}",
                    f"📋 Всего броней: {cnt}",
                    f"💰 Общая сумма: {total}₽",
                ]
            ),
            reply_markup=kb,
        )
    except Exception:
        logger.exception("profile")
        await message.answer(ERR)


@router.message(F.text == "📞 Контакты")
async def contacts_msg(message: Message) -> None:
    await message.answer(f"📞 Связь: @{settings.support_username}")


@router.message(F.text == "ℹ️ О нас")
async def about_msg(message: Message) -> None:
    await message.answer("ℹ️ Аренда авто с 2024 года. Надёжность и комфорт.")


@router.message(Command("admin"))
@router.message(F.text == "🛠 Админ-панель")
async def admin_panel(message: Message) -> None:
    if not message.from_user or not _is_admin(message.from_user.id):
        await message.answer("Нет доступа.")
        return
    await message.answer("🛠 Админ-панель", reply_markup=admin_inline_menu())


@router.callback_query(F.data == "admin:add_car")
async def admin_add_car_start(call: CallbackQuery, state: FSMContext) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(AdminAddCarState.waiting_name)
    await call.message.answer("Введите название автомобиля:")
    await call.answer()


@router.message(AdminAddCarState.waiting_name)
async def admin_add_car_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=(message.text or "").strip())
    await state.set_state(AdminAddCarState.waiting_description)
    await message.answer("Введите описание автомобиля:")


@router.message(AdminAddCarState.waiting_description)
async def admin_add_car_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=(message.text or "").strip())
    await state.set_state(AdminAddCarState.waiting_price)
    await message.answer("Введите цену за сутки (число):")


@router.message(AdminAddCarState.waiting_price)
async def admin_add_car_price(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Цена должна быть числом, например 3500")
        return
    await state.update_data(price_per_day=int(text))
    await state.set_state(AdminAddCarState.waiting_engine)
    await message.answer("Введите двигатель (например 2.0, 1.6, 3.0):")


@router.message(AdminAddCarState.waiting_engine)
async def admin_add_car_engine(message: Message, state: FSMContext) -> None:
    await state.update_data(engine=(message.text or "").strip() or "-")
    await state.set_state(AdminAddCarState.waiting_transmission)
    await message.answer("Введите коробку: <b>automatic</b> или <b>manual</b>:")


@router.message(AdminAddCarState.waiting_transmission)
async def admin_add_car_transmission(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("automatic", "manual"):
        await message.answer("Коробка должна быть: automatic или manual.")
        return
    await state.update_data(transmission=val)



    await state.set_state(AdminAddCarState.waiting_seats)
    await message.answer("Введите количество мест (число):")

# --- CONFIRM ---
@router.callback_query(F.data.startswith("admin:bk:confirm:"))
async def admin_booking_confirm(call: CallbackQuery, session):
    parts = call.data.split(":")
    booking_id = parts[3]
    page = int(parts[4])


    b = await get_booking(session, booking_id)
    if not b:
        await call.answer("Бронь не найдена", show_alert=True)
        return

    b.status = BookingStatus.confirmed.value
    await session.commit()

    await call.answer("Оплата подтверждена", show_alert=True)





@router.message(AdminAddCarState.waiting_seats)
async def admin_add_car_seats(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val.isdigit():
        await message.answer("Введите число, например 5.")
        return
    await state.update_data(seats=int(val))
    await state.set_state(AdminAddCarState.waiting_has_ac)
    await message.answer("Кондиционер есть? Ответьте: <b>да</b> или <b>нет</b>:")


@router.message(AdminAddCarState.waiting_has_ac)
async def admin_add_car_ac(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("да", "нет", "yes", "no", "y", "n"):
        await message.answer("Ответьте: да или нет.")
        return
    await state.update_data(has_ac=val in ("да", "yes", "y"))
    await state.set_state(AdminAddCarState.waiting_is_available)
    await message.answer("Доступно для аренды? Ответьте: <b>да</b> или <b>нет</b>:")


@router.message(AdminAddCarState.waiting_is_available)
async def admin_add_car_available(message: Message, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("да", "нет", "yes", "no", "y", "n"):
        await message.answer("Ответьте: да или нет.")
        return
    await state.update_data(is_available=val in ("да", "yes", "y"))
    await state.set_state(AdminAddCarState.waiting_photo)
    await message.answer("Отправьте фото автомобиля (как фото).")



@router.callback_query(F.data == "admin:cars")
async def admin_cars_start(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        cars, total_pages = await list_cars_admin_page(session, 1)
        await call.message.answer("📋 <b>Авто (все)</b>", reply_markup=admin_cars_list_kb(cars=cars, page=1, total_pages=total_pages))
        await call.answer()
    except Exception:
        logger.exception("admin_cars_start")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:cars:p:"))
async def admin_cars_page(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(call.data.split(":")[-1])
        cars, total_pages = await list_cars_admin_page(session, page)
        await call.message.edit_text("📋 <b>Авто (все)</b>", reply_markup=admin_cars_list_kb(cars=cars, page=page, total_pages=total_pages))
        await call.answer()
    except Exception:
        logger.exception("admin_cars_page")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data == "admin:back")
async def admin_back(call: CallbackQuery) -> None:
    await call.message.edit_text("🛠 Админ-панель", reply_markup=admin_inline_menu())
    await call.answer()


@router.callback_query(F.data == "admin:blackouts")
async def admin_blackouts_start(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        cars, total_pages = await list_cars_admin_page(session, 1)
        await call.message.answer(
            "📅 <b>Закрыть даты</b>\nВыберите авто:",
            reply_markup=admin_pick_car_kb(cars=cars, prefix="admin:blackout:car", page=1, total_pages=total_pages),
        )
        await call.answer()
    except Exception:
        logger.exception("admin_blackouts_start")
        await call.answer(ERR, show_alert=True)

import json

@router.message(AdminAddCarState.waiting_photo, F.photo)
async def admin_add_car_photo(message: Message, session, state: FSMContext):
    data = await state.get_data()

    photos = data.get("photos", [])
    photos.append(message.photo[-1].file_id)

    await state.update_data(photos=photos)

    await message.answer(
        "Фото добавлено. Отправьте ещё или нажмите «Готово».",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Готово", callback_data="admin:add_car:finish")]
            ]
        )
    )


@router.callback_query(F.data.startswith("admin:blackout:car:page:"))
async def admin_blackouts_car_page(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(call.data.split(":")[-1])
        cars, total_pages = await list_cars_admin_page(session, page)
        await call.message.edit_text(
            "📅 <b>Закрыть даты</b>\nВыберите авто:",
            reply_markup=admin_pick_car_kb(cars=cars, prefix="admin:blackout:car", page=page, total_pages=total_pages),
        )
        await call.answer()
    except Exception:
        logger.exception("admin_blackouts_car_page")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:blackout:car:"))
async def admin_blackout_pick_car(call: CallbackQuery, state: FSMContext) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        # admin:blackout:car:<car_id>:p:<page>
        parts = call.data.split(":")
        car_id = int(parts[3])
        page = int(parts[-1])
        await state.update_data(blackout_car_id=car_id, blackout_page=page)
        await state.set_state(AdminBlackoutState.waiting_range)
        await call.message.answer(
            "Введите диапазон дат в формате:\n"
            "<code>YYYY-MM-DD..YYYY-MM-DD</code>\n"
            "Например: <code>2026-05-10..2026-05-15</code>\n"
            "Эти даты будут видны, но недоступны для брони."
        )
        await call.answer()
    except Exception:
        logger.exception("admin_blackout_pick_car")
        await call.answer(ERR, show_alert=True)


@router.message(AdminBlackoutState.waiting_range, F.text)
async def admin_blackout_set_range(message: Message, session, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if ".." not in text:
        await message.answer("Неверный формат. Нужно: YYYY-MM-DD..YYYY-MM-DD")
        return
    try:
        a, b = [p.strip() for p in text.split("..", 1)]
        start_d = date.fromisoformat(a)
        end_d = date.fromisoformat(b)
    except Exception:
        await message.answer("Неверная дата. Пример: 2026-05-10..2026-05-15")
        return
    data = await state.get_data()
    car_id = int(data["blackout_car_id"])
    added = await add_blackout_range(session, car_id=car_id, start_day=start_d, end_day=end_d, source="external")
    await state.clear()
    await message.answer(
        f"✅ Закрыто дат: <b>{added}</b>\nАвто ID: <code>{car_id}</code>\nДиапазон: <code>{start_d}..{end_d}</code>",
        reply_markup=main_reply_menu(is_admin=_is_admin(message.from_user.id)) if message.from_user else None,
    )


@router.callback_query(F.data == "admin:bookings")
async def admin_bookings_start(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        rows, total_pages = await list_bookings_admin_page(session, 1)
        await call.message.answer("📋 <b>Брони</b>", reply_markup=admin_booking_list_kb(bookings=rows, page=1, total_pages=total_pages))
        await call.answer()
    except Exception:
        logger.exception("admin_bookings_start")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:bookings:p:"))
async def admin_bookings_page(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        page = int(call.data.split(":")[-1])
        rows, total_pages = await list_bookings_admin_page(session, page)
        try:
            await call.message.edit_text(
                "📋 <b>Брони</b>",
                reply_markup=admin_booking_list_kb(
                    bookings=rows,
                    page=page,
                    total_pages=total_pages
                )
            )

        except TelegramBadRequest:
            pass
        await call.answer()
    except Exception:
        logger.exception("admin_bookings_page")
        await call.answer(ERR, show_alert=True)






        


@router.callback_query(F.data.startswith("admin:bk:cx:"))
async def admin_booking_cancel_confirm(call: CallbackQuery) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        # admin:bk:cx:<booking_id>:<page>
        _, _, _, booking_id, page_s = call.data.split(":", 4)
        page = int(page_s)
        await call.message.edit_text(
            "❌ Отменить бронь?\nЭто снимет бронь и освободит даты в календаре.",
            reply_markup=admin_confirm_cancel_booking_kb(booking_id=booking_id, page=page),
        )
        await call.answer()
    except Exception:
        logger.exception("admin_booking_cancel_confirm")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:bk:cx_ok:"))
async def admin_booking_cancel_ok(call: CallbackQuery, session, bot: Bot) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        # admin:bk:cx_ok:<booking_id>:<page>
        _, _, _, booking_id, page_s = call.data.split(":", 4)
        page = int(page_s)
        ok = await cancel_booking_admin(session, booking_id)
        if not ok:
            await call.answer("Не удалось отменить", show_alert=True)
            return
        b = await get_booking(session, booking_id)
        if b:
            try:
                await bot.send_message(b.user.telegram_id, f"❌ Ваша бронь <code>{b.id}</code> была отменена администратором.")
            except Exception:
                pass
        await call.answer("Отменено")
        rows, total_pages = await list_bookings_admin_page(session, page)
        await call.message.edit_text(
            "✅ Отменено.\n\n📋 <b>Брони</b>",
            reply_markup=admin_booking_list_kb(bookings=rows, page=page, total_pages=total_pages),
        )
    except Exception:
        logger.exception("admin_booking_cancel_ok")
        await call.answer(ERR, show_alert=True)


def _format_car_admin(car) -> str:
    trans = "automatic" if car.transmission == "automatic" else "manual"
    return (
        "🚗 <b>Авто</b>\n"
        f"ID: <code>{car.id}</code>\n"
        f"Название: <b>{car.name}</b>\n"
        f"Описание: {car.description or '—'}\n"
        f"Цена: <b>{car.price_per_day}₽/сутки</b>\n"
        f"Двигатель: {car.engine}\n"
        f"Коробка: {trans}\n"
        f"Места: {car.seats}\n"
        f"AC: {'да' if car.has_ac else 'нет'}\n"
        f"Доступность: {'✅' if car.is_available else '🚫'}"
    )


@router.callback_query(F.data.startswith("admin:car:"))
async def admin_car_open(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        # admin:car:<id>:p:<page>
        parts = call.data.split(":")
        car_id = int(parts[2])
        page = int(parts[-1])
        car = await get_car(session, car_id)
        if not car:
            await call.answer("Не найдено", show_alert=True)
            return
        await call.message.edit_text(_format_car_admin(car), reply_markup=admin_car_manage_kb(car_id=car_id, page=page))
        await call.answer()
    except Exception:
        logger.exception("admin_car_open")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:del:"))
async def admin_car_delete_confirm(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, car_id_s, page_s = call.data.split(":", 3)
        car_id = int(car_id_s)
        page = int(page_s)
        car = await get_car(session, car_id)
        if not car:
            await call.answer("Не найдено", show_alert=True)
            return
        await call.message.edit_text(
            f"🗑 Удалить авто <b>{car.name}</b> (ID {car.id})?\nЭто действие нельзя отменить.",
            reply_markup=admin_confirm_delete_kb(car_id=car_id, page=page),
        )
        await call.answer()
    except Exception:
        logger.exception("admin_car_delete_confirm")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:del_ok:"))
async def admin_car_delete_ok(call: CallbackQuery, session) -> None:
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        _, _, car_id_s, page_s = call.data.split(":", 3)
        car_id = int(car_id_s)
        page = int(page_s)
        ok, reason = await delete_car(session, car_id)
        if not ok and reason == "HAS_BOOKINGS":
            await call.answer()
            await call.message.edit_text(
                "Нельзя удалить авто: по нему есть бронирования.\n"
                "Используйте «✅/🚫 Доступность», чтобы скрыть авто из каталога.",
                reply_markup=admin_car_manage_kb(car_id=car_id, page=page),
            )
            return
        if not ok:
            await call.answer("Не удалось удалить", show_alert=True)
            return
        cars, total_pages = await list_cars_admin_page(session, page)
        await call.message.edit_text("✅ Удалено.\n\n📋 <b>Авто (все)</b>", reply_markup=admin_cars_list_kb(cars=cars, page=page, total_pages=total_pages))
        await call.answer()
    except Exception:
        logger.exception("admin_car_delete_ok")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("admin:edit:"))
async def admin_edit_start(call: CallbackQuery, session, state: FSMContext):
    try:
        # admin:edit:<car_id>:<field>:<page>
        _, _, car_id_s, field, page_s = call.data.split(":")

        car_id = int(car_id_s)
        page = int(page_s)

        car = await get_car(session, car_id)
        if not car:
            await call.answer("Авто не найдено", show_alert=True)
            return

        await state.update_data(car_id=car.id, page=page, field=field)

        await state.set_state(AdminEditCarState.waiting_value)

        await call.message.answer(f"Введите новое значение для поля: {field}")

    except Exception:
        logger.exception("admin_edit_start")
        await call.answer("Ошибка", show_alert=True)

async def _admin_edit_apply_and_show(call_or_msg, session, state: FSMContext, **fields) -> None:
    data = await state.get_data()
    car_id = int(data["car_id"])
    page = int(data.get("page", 1))
    car = await update_car_fields(session, car_id, **fields)
    await state.clear()
    if not car:
        await call_or_msg.answer("Авто не найдено", show_alert=True) if isinstance(call_or_msg, CallbackQuery) else None
        return
    # показываем карточку управления (новым сообщением, чтобы не ломать поток)
    if isinstance(call_or_msg, CallbackQuery):
        await call_or_msg.message.answer(_format_car_admin(car), reply_markup=admin_car_manage_kb(car_id=car.id, page=page))
    else:
        await call_or_msg.answer(_format_car_admin(car), reply_markup=admin_car_manage_kb(car_id=car.id, page=page))


@router.message(AdminEditCarState.waiting_name, F.text)
async def admin_edit_name(message: Message, session, state: FSMContext) -> None:
    await _admin_edit_apply_and_show(message, session, state, name=(message.text or "").strip())


@router.message(AdminEditCarState.waiting_description, F.text)
async def admin_edit_desc(message: Message, session, state: FSMContext) -> None:
    await _admin_edit_apply_and_show(message, session, state, description=(message.text or "").strip())


@router.message(AdminEditCarState.waiting_price, F.text)
async def admin_edit_price(message: Message, session, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val.isdigit():
        await message.answer("Цена должна быть числом.")
        return
    await _admin_edit_apply_and_show(message, session, state, price_per_day=int(val))


@router.message(AdminEditCarState.waiting_engine, F.text)
async def admin_edit_engine(message: Message, session, state: FSMContext) -> None:
    await _admin_edit_apply_and_show(message, session, state, engine=(message.text or "").strip() or "-")


@router.message(AdminEditCarState.waiting_transmission, F.text)
async def admin_edit_trans(message: Message, session, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("automatic", "manual"):
        await message.answer("Коробка должна быть: automatic или manual.")
        return
    await _admin_edit_apply_and_show(message, session, state, transmission=val)


@router.message(AdminEditCarState.waiting_seats, F.text)
async def admin_edit_seats(message: Message, session, state: FSMContext) -> None:
    val = (message.text or "").strip()
    if not val.isdigit():
        await message.answer("Введите число.")
        return
    await _admin_edit_apply_and_show(message, session, state, seats=int(val))


@router.message(AdminEditCarState.waiting_has_ac, F.text)
async def admin_edit_ac(message: Message, session, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("да", "нет", "yes", "no", "y", "n"):
        await message.answer("Ответьте: да или нет.")
        return
    await _admin_edit_apply_and_show(message, session, state, has_ac=val in ("да", "yes", "y"))


@router.message(AdminEditCarState.waiting_is_available, F.text)
async def admin_edit_avail(message: Message, session, state: FSMContext) -> None:
    val = (message.text or "").strip().lower()
    if val not in ("да", "нет", "yes", "no", "y", "n"):
        await message.answer("Ответьте: да или нет.")
        return
    await _admin_edit_apply_and_show(message, session, state, is_available=val in ("да", "yes", "y"))


async def _send_or_edit_catalog_list(
    *,
    message: Message,
    session,
    page: int,
    filters: CarFilters | None,
    edit: bool,
) -> None:
    """Один экран каталога: баннер (фото) + кнопки авто + пагинация."""
    cars, total_pages = await list_cars_page(session, page, filters)
    fcode = filters_encode(filters)
    if not cars:
        if edit:
            await message.edit_text("🚗 Машины не найдены.", reply_markup=None)
        else:
            await message.answer("🚗 Машины не найдены.")
        return

    title = f"🚗 <b>Каталог авто</b>\nСтраница: <b>{page}/{total_pages}</b>\n\nВыберите автомобиль:"
    kb = catalog_list_kb(cars=cars, page=page, total_pages=total_pages, fcode=fcode)

    # Баннер: первое авто на странице, у которого есть фото (file_id).
    banner = ""
    for c in cars:
        if (c.image_url or "").strip():
            banner = (c.image_url or "").strip()
            break

    if not edit:
        if banner:
            try:
                await message.answer_photo(banner, caption=title, reply_markup=kb)
                return
            except TelegramBadRequest:
                pass
        await message.answer(title, reply_markup=kb)
        return

    # edit=True
    if message.photo:
        # Сообщение с фото: можно менять media/caption.
        if banner:
            try:
                await message.edit_media(InputMediaPhoto(media=banner, caption=title), reply_markup=kb)
                return
            except TelegramBadRequest:
                # если media не удалось, попробуем хотя бы caption
                pass
        try:
            await message.edit_caption(caption=title, reply_markup=kb)
        except TelegramBadRequest:
            # fallback: если по какой-то причине caption/edit не прошёл
            await message.answer(title, reply_markup=kb)
        return

    # Сообщение без фото: редактируем текст. Если есть баннер, "пересоздадим" сообщение как фото.
    if banner:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.answer_photo(banner, caption=title, reply_markup=kb)
            return
        except TelegramBadRequest:
            pass
    await message.answer(title, reply_markup=kb)



async def _edit_catalog_detail(
    *,
    message: Message,
    session,
    car_id: int,
    page: int,
    filters: CarFilters | None,
) -> None:
    car = await get_car(session, car_id)
    if not car:
        await message.answer("Машина не найдена.")
        return

    fcode = filters_encode(filters)
    kb = catalog_detail_kb(car_id=car.id, page=page, fcode=fcode)
    caption = _format_car_html(car)

    # Загружаем фото
    import json
    photos = []
    try:
        photos = json.loads(car.image_url)
    except:
        if car.image_url:
            photos = [car.image_url]

    # Если фото нет
    if not photos:
        await message.answer(caption, reply_markup=kb)
        return

    # Если одно фото
    if len(photos) == 1:
        await message.answer_photo(
            photos[0],
            caption=caption,
            reply_markup=kb
        )
        return

    # Если несколько фото — отправляем альбом
    media = [InputMediaPhoto(media=p) for p in photos]
    media[0].caption = caption

    await message.answer_media_group(media)
    await message.answer("Выберите действие:", reply_markup=kb)




    # текстовое сообщение: если у авто есть фото, "пересоздадим" сообщение как фото
    if photo:
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.answer_photo(photo, caption=caption, reply_markup=kb)
            return
        except TelegramBadRequest:
            pass
        try:
            await message.edit_text(caption, reply_markup=kb)
        except:
            await message.answer(caption, reply_markup=kb)



@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("cat:p:"))
async def cat_page(call: CallbackQuery, session) -> None:
    try:
        _, _, page_s, fcode = call.data.split(":", 3)
        page = int(page_s)
        filters = filters_decode(fcode)
        cars, total_pages = await list_cars_page(session, page, filters)
        await call.answer()
        # редактируем текущее сообщение каталога
        await _send_or_edit_catalog_list(message=call.message, session=session, page=page, filters=filters, edit=True)
    except Exception:
        logger.exception("cat_page")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("cat:open:"))
async def cat_open(call: CallbackQuery, session) -> None:
    try:
        _, _, car_id_s, page_s, fcode = call.data.split(":", 4)
        car_id = int(car_id_s)
        page = int(page_s)
        filters = filters_decode(fcode)
        await call.answer()
        await _edit_catalog_detail(message=call.message, session=session, car_id=car_id, page=page, filters=filters)
    except Exception:
        logger.exception("cat_open")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("cat:f:"))
async def cat_filters_menu(call: CallbackQuery) -> None:
    fcode = call.data.split(":", 2)[2]
    await call.message.answer("Выберите фильтры:", reply_markup=filters_kb(fcode))
    await call.answer()


@router.callback_query(F.data.startswith("cat:back:"))
async def cat_back(call: CallbackQuery, session) -> None:
    try:
        _, _, page_s, fcode = call.data.split(":", 3)
        page = int(page_s)
        filters = filters_decode(fcode)
        await call.answer()
        await _send_or_edit_catalog_list(message=call.message, session=session, page=page, filters=filters, edit=True)
    except Exception:
        logger.exception("cat_back")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data == "cat:clr")
async def cat_clear(call: CallbackQuery, session) -> None:
    # legacy callback из старой версии (если у кого-то осталось сообщение)
    await call.answer()
    await _send_or_edit_catalog_list(message=call.message, session=session, page=1, filters=None, edit=True)


@router.callback_query(F.data.startswith("cat:fp:"))
async def cat_filter_price(call: CallbackQuery, session) -> None:
    try:
        _, _, price, fcode = call.data.split(":", 3)
        cur = filters_decode(fcode)
        cur.price = price
        await call.answer()
        await _send_or_edit_catalog_list(message=call.message, session=session, page=1, filters=cur, edit=True)
    except Exception:
        logger.exception("cat_fp")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("cat:ft:"))
async def cat_filter_trans(call: CallbackQuery, session) -> None:
    try:
        _, _, trans, fcode = call.data.split(":", 3)
        cur = filters_decode(fcode)
        cur.transmission = trans
        await call.answer()
        await _send_or_edit_catalog_list(message=call.message, session=session, page=1, filters=cur, edit=True)
    except Exception:
        logger.exception("cat_ft")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("car:d:"))
async def car_details(call: CallbackQuery, session) -> None:
    try:
        cid = int(call.data.rsplit(":", 1)[-1])
        car = await get_car(session, cid)
        await call.answer()
        if not car:
            await call.message.answer("Машина не найдена.")
            return
        await call.message.answer(_format_car_html(car))
    except Exception:
        logger.exception("car_d")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("car:b:"))
async def car_book_start(call: CallbackQuery, session, state: FSMContext) -> None:
    try:
        if not call.from_user:
            await call.answer(ERR, show_alert=True)
            return
        user = await upsert_user(
            session,
            call.from_user.id,
            call.from_user.first_name,
            call.from_user.last_name,
            call.from_user.username,
        )
        if not _registration_complete(user):
            await call.answer("Сначала завершите регистрацию: нажмите /start", show_alert=True)
            return
        cid = int(call.data.rsplit(":", 1)[-1])
        car = await get_car(session, cid)
        if not car:
            await call.answer("Машина не найдена", show_alert=True)
            return
        tomorrow = date.today() + timedelta(days=1)
        blocked = await list_blocked_days_for_month(session, car_id=cid, year=tomorrow.year, month=tomorrow.month)
        await state.set_state(RentalBookingState.choosing_start)
        await state.update_data(car_id=cid)
        await call.message.answer(
            "📅 Выберите дату начала аренды (занятые/закрытые даты не нажимаются):",
            reply_markup=calendar_kb("s", tomorrow, car_id=cid, blocked_days=blocked),
        )
        await call.answer()
    except Exception:
        logger.exception("car_b")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("cal:nav:"))
async def cal_nav(call: CallbackQuery, session) -> None:
    try:
        parts = call.data.split(":")
        prefix = parts[2]
        car_id = int(parts[3])
        year, month = int(parts[4]), int(parts[5])
        min_day = datetime.strptime(parts[6], "%Y%m%d").date()
        view = date(year, month, 1)
        min_month = date(min_day.year, min_day.month, 1)
        if view < min_month:
            view = min_month
        blocked = await list_blocked_days_for_month(session, car_id=car_id, year=view.year, month=view.month)
        await call.message.edit_reply_markup(
            reply_markup=calendar_kb(prefix, min_day, car_id=car_id, blocked_days=blocked, view=view)
        )
        await call.answer()
    except Exception:
        logger.exception("cal_nav")
        await call.answer()


@router.callback_query(F.data.startswith("cal:pick:"))
async def cal_pick(call: CallbackQuery, session, state: FSMContext) -> None:
    try:
        # cal:pick:<prefix>:<YYYYMMDD>
        _, _, _, compact = call.data.split(":", 3)
        picked = datetime.strptime(compact, "%Y%m%d").date()

        data = await state.get_data()
        st = await state.get_state()

        # ---------------------------------------------------
        # 1) ВЫБОР ДАТЫ НАЧАЛА АРЕНДЫ
        # ---------------------------------------------------
        if st == RentalBookingState.choosing_start.state:
            tomorrow = date.today() + timedelta(days=1)
            if picked < tomorrow:
                await call.answer("Дата недоступна.", show_alert=True)
                return

            await state.update_data(start_iso=picked.isoformat())
            await state.set_state(RentalBookingState.choosing_end)

            min_end = picked + timedelta(days=1)
            blocked = await list_blocked_days_for_month(
                session,
                car_id=data["car_id"],
                year=min_end.year,
                month=min_end.month
            )

            await call.message.answer(
                "📅 Выберите дату окончания аренды (занятые/закрытые даты не нажимаются):",
                reply_markup=calendar_kb(
                    "e",
                    min_end,
                    car_id=data["car_id"],
                    blocked_days=blocked
                ),
            )
            await call.answer()
            return

        # ---------------------------------------------------
        # 2) ВЫБОР ДАТЫ ОКОНЧАНИЯ АРЕНДЫ
        # ---------------------------------------------------
        elif st == RentalBookingState.choosing_end.state:
            start_date = datetime.fromisoformat(data["start_iso"]).date()
            end_date = picked

            if end_date <= start_date:
                await call.answer("Дата окончания должна быть позже даты начала.", show_alert=True)
                return


            if await has_overlapping_booking(session, data["car_id"], start_date, end_date):
                await call.answer("Эти даты уже заняты.", show_alert=True)
                return


            days = (end_date - start_date).days + 1

            car = await get_car(session, data["car_id"])

            total_price = days * car.price_per_day

            await state.update_data(
                end_iso=end_date.isoformat(),
                total_price=total_price
            )

            await state.set_state(RentalBookingState.waiting_phone)
            await call.message.answer(
                f"📅 Даты выбраны:\n"
                f"С {start_date} по {end_date}\n"
                f"💰 Стоимость: {total_price}₽\n\n"
                f"Введите номер телефона:"
            )
            await call.answer()
    except Exception as e:
        logger.exception("cal_pick ERROR")
        await call.message.answer(f"Ошибка: {e}")

 
    except Exception:
        logger.exception("cal_pick")
        await call.answer("Ошибка", show_alert=True)
@router.message(RentalBookingState.waiting_phone, F.text)
async def booking_phone_text(message: Message, session, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("Введите корректный номер телефона.")
        return

    try:
        if not message.from_user:
            await message.answer(ERR)
            return

        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        user.phone = text
        await session.commit()

        data = await state.get_data()

        car = await get_car(session, data["car_id"])
        start_d = date.fromisoformat(data["start_iso"])
        end_d = date.fromisoformat(data["end_iso"])

        await state.set_state(RentalBookingState.confirming)

        await message.answer(
            "\n".join(
                [
                    "✅ <b>Подтверждение брони</b>",
                    f"🚗 Авто: {car.name}",
                    f"📅 Период: {start_d:%d.%m.%Y} - {end_d:%d.%m.%Y}",
                    f"💰 Сумма: {data['total_price']}₽",
                    f"📞 Телефон: {user.phone}",
                ]
            ),
            reply_markup=confirm_booking_kb(),
        )

    except Exception:
        logger.exception("booking_phone_text")
        await message.answer(ERR)


@router.callback_query(F.data == "rent:cancel")
async def rent_cancel(call: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await call.message.delete()
    except Exception:
        pass
    uid = call.from_user.id if call.from_user else 0
    await call.message.answer(
        "❌ Бронирование отменено.",
        reply_markup=main_reply_menu(is_admin=_is_admin(uid)) if uid else None,
    )
    await call.answer()

@router.callback_query(F.data == "rent:ok")
async def rent_confirm(call: CallbackQuery, session, state: FSMContext, bot: Bot) -> None:
    try:
        data = await state.get_data()
        if not call.from_user:
            await call.answer(ERR, show_alert=True)
            return
        if not data or any(k not in data for k in ("car_id", "start_iso", "end_iso")):
            await state.clear()
            try:
                await call.message.delete()
            except Exception:
                pass
            await call.message.answer(
                "Сессия бронирования устарела. Пожалуйста, начните бронирование заново из каталога.",
                reply_markup=main_reply_menu(is_admin=_is_admin(call.from_user.id)),
            )
            await call.answer()
            return
        user = await upsert_user(
            session,
            call.from_user.id,
            call.from_user.first_name,
            call.from_user.last_name,
            call.from_user.username,
        )
        car = await get_car(session, data["car_id"])
        if not car:
            await state.clear()
            await call.answer("Машина не найдена. Откройте каталог ещё раз.", show_alert=True)
            return
        start_dt = datetime.combine(date.fromisoformat(data["start_iso"]), datetime.min.time())
        end_dt = datetime.combine(date.fromisoformat(data["end_iso"]), datetime.min.time())
        try:
            booking = await create_booking(session, user.id, car, start_dt, end_dt, user.phone or "")
        except ValueError as exc:
            if str(exc) == "CAR_BUSY":
                await state.clear()
                try:
                    await call.message.delete()
                except Exception:
                    pass
                await call.message.answer(
                    "⛔ Эта машина уже занята на выбранные даты.\nОткройте каталог и выберите другие даты или авто.",
                    reply_markup=main_reply_menu(is_admin=_is_admin(call.from_user.id)),
                )
                await call.answer()
                return
            raise
        await state.clear()
        await call.answer()
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(
            f"✅ Бронь создана!\nНомер: <b>{booking.id}</b>\n⏰ Бронь зарезервирована на 2 часа.",
            reply_markup=pay_stub_kb(booking.id),
        )
        # Inline-кнопки оплаты не возвращают reply-меню, поэтому отправим следующее сообщение с постоянной клавиатурой.
        await call.message.answer(
            "Главное меню:",
            reply_markup=main_reply_menu(is_admin=_is_admin(call.from_user.id)),
        )
        await _notify_admins(
            bot,
            f"📋 <b>Новая бронь</b>\nID: {booking.id}\n"
            f"Пользователь: {_display_name(user)} (tg {user.telegram_id})\n"
            f"📞 Телефон: {user.phone or 'не указан'}\n"
            f"Авто: {car.name}\nСумма: {booking.total_price}₽",
        )
    except Exception:
        logger.exception("rent_ok")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data.startswith("pay:"))
async def pay_stub(call: CallbackQuery) -> None:
    await call.answer()
    await call.message.answer(
        f"⏰ Оплата в разработке. Забронировано на 2 часа. Свяжитесь с менеджером @{settings.support_username}"
    )


@router.callback_query(F.data.startswith("bk:cx:"))
async def booking_cancel_cb(call: CallbackQuery, session, bot: Bot) -> None:
    try:
        if not call.from_user:
            await call.answer(ERR, show_alert=True)
            return
        bid = call.data.split(":", 2)[2]
        user = await upsert_user(
            session,
            call.from_user.id,
            call.from_user.first_name,
            call.from_user.last_name,
            call.from_user.username,
        )
        ok, need_manager = await cancel_booking_user(session, bid, user.id)
        if need_manager:
            await call.answer()
            await call.message.answer(f"⏰ До начала менее 24 часов. Свяжитесь с менеджером @{settings.support_username}")
            return
        if not ok:
            await call.answer("Нельзя отменить.", show_alert=True)
            return
        await call.answer("Отменено")
        await call.message.answer("❌ Бронь отменена.")
        await _notify_admins(bot, f"❌ Отмена брони {bid}\nПользователь: {call.from_user.id}")
    except Exception:
        logger.exception("bk_cancel")
        await call.answer(ERR, show_alert=True)


@router.callback_query(F.data == "prof:phone")
async def prof_phone(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ProfileState.waiting_phone)
    await call.message.answer(
        "📞 Введите новый номер телефона (текстом):",
        reply_markup=main_reply_menu(is_admin=_is_admin(call.from_user.id)) if call.from_user else None,
    )
    await call.answer()


@router.message(ProfileState.waiting_phone, F.text)
async def profile_phone_text(message: Message, session, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if len(text) < 10:
        await message.answer("Введите корректный номер телефона.")
        return
    try:
        if not message.from_user:
            await message.answer(ERR)
            return
        user = await upsert_user(
            session,
            message.from_user.id,
            message.from_user.first_name,
            message.from_user.last_name,
            message.from_user.username,
        )
        user.phone = text
        await session.commit()
        await state.clear()
        await message.answer("✅ Телефон обновлен.", reply_markup=main_reply_menu(is_admin=_is_admin(message.from_user.id)))
    except Exception:
        logger.exception("profile_phone_text")
        await message.answer(ERR)


@router.callback_query(F.data == "admin:bookings_cancelled")
async def admin_bookings_cancelled(call: CallbackQuery, session):
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        rows = await session.execute(
            select(Booking)
            .where(Booking.status == BookingStatus.cancelled.value)
            .order_by(Booking.created_at.desc())
        )
        rows = rows.scalars().all()

        if not rows:
            await call.message.answer("🗑 Отменённых броней нет.")
            return

        text = "<b>🗑 Отменённые брони</b>\n\n"
        for b in rows:
            text += (
                f"• <code>{b.id[:8]}</code> — авто {b.car_id} — "
                f"{b.start_date:%d.%m}–{b.end_date:%d.%m}\n"
            )

        await call.message.answer(text)
        await call.answer()

    except Exception:
        logger.exception("admin_bookings_cancelled")
        await call.answer(ERR, show_alert=True)

@router.callback_query(F.data == "admin:bookings_filters")
async def admin_bookings_filters(call: CallbackQuery):
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все", callback_data="admin:bookings:flt:all")],
            [InlineKeyboardButton(text="Ожидают оплаты", callback_data="admin:bookings:flt:wp")],
            [InlineKeyboardButton(text="Подтверждённые", callback_data="admin:bookings:flt:conf")],
            [InlineKeyboardButton(text="Завершённые", callback_data="admin:bookings:flt:done")],
            [InlineKeyboardButton(text="Отменённые", callback_data="admin:bookings:flt:cx")],
        ]
    )
    await call.message.edit_text("📋 Выберите фильтр по бронированиям:", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("admin:bookings:flt:"))
async def admin_bookings_filtered(call: CallbackQuery, session):
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return
    try:
        flt = call.data.split(":")[-1]
        rows, total_pages = await list_bookings_admin_page(session, 1)

        if flt == "wp":
            rows = [b for b in rows if b.status == BookingStatus.waiting_payment.value]
        elif flt == "conf":
            rows = [b for b in rows if b.status == BookingStatus.confirmed.value]
        elif flt == "done":
            rows = [b for b in rows if b.status == BookingStatus.completed.value]
        elif flt == "cx":
            # тут можно отдельно запросить отменённые, но у тебя уже есть admin_bookings_cancelled
            rows = [b for b in rows if b.status == BookingStatus.cancelled.value]
        # flt == "all" — ничего не фильтруем

        await call.message.edit_text(
            "📋 <b>Брони (фильтр)</b>",
            reply_markup=admin_booking_list_kb(bookings=rows, page=1, total_pages=1),
        )
        await call.answer()
    except Exception:
        logger.exception("admin_bookings_filtered")
        await call.answer(ERR, show_alert=True)

import io
import csv
from aiogram.types import InputFile

@router.callback_query(F.data == "admin:export_bookings")
async def admin_export_bookings(call: CallbackQuery, session):
    if not call.from_user or not _is_admin(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    try:
        rows, _ = await list_bookings_admin_page(session, page=1, page_size=1000)

        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")

        writer.writerow(["ID", "Авто", "Начало", "Окончание", "Сумма", "Статус"])

        for b in rows:
            writer.writerow([
                b.id,
                b.car.name,
                b.start_date.isoformat(),
                b.end_date.isoformat(),
                b.total_price,
                b.status,
            ])

        # ПРАВИЛЬНОЕ СОЗДАНИЕ ФАЙЛА ДЛЯ AIROGRAM 3.x
        csv_bytes = buf.getvalue().encode("utf-8")
        file = BufferedInputFile(csv_bytes, filename="bookings.csv")

        await call.message.answer_document(file, caption="📤 Экспорт броней")
        await call.answer()

    except Exception:
        logger.exception("admin_export_bookings")
        await call.answer(ERR, show_alert=True)



from datetime import date, timedelta
from sqlalchemy import delete, select
from app.db import async_sessionmaker
from app.models import Booking, BookingStatus

async def notify_upcoming_rentals(bot):
    async with async_sessionmaker() as session:
        tomorrow = date.today() + timedelta(days=1)
        rows = await session.execute(
            select(Booking).where(
                Booking.start_date == tomorrow,
                Booking.status == BookingStatus.confirmed.value
            )
        )
        bookings = rows.scalars().all()

        for b in bookings:
            try:
                await bot.send_message(
                    b.user_id,
                    f"📅 Напоминаем! Завтра начинается ваша аренда авто {b.car.name}."
                )
            except:
                pass

@router.callback_query(F.data.startswith("admin:bk:cancel:"))
async def admin_booking_cancel(call: CallbackQuery, session):
    parts = call.data.split(":")
    booking_id = parts[3]
    page = int(parts[4])

    b = await get_booking(session, booking_id)
    if not b:
        await call.answer("Не найдено", show_alert=True)
        return

    b.status = BookingStatus.cancelled.value
    await session.commit()

    await call.answer("Бронь отменена", show_alert=True)

    await admin_booking_open(call, session)

@router.callback_query(F.data.startswith("admin:bk:") & ~F.data.startswith("admin:bk:confirm:"))
async def admin_booking_open(call: CallbackQuery, session):
    parts = call.data.split(":")
    # admin:bk:<booking_id>:<page>
    booking_id = parts[2]
    page = int(parts[3])

    b = await get_booking(session, booking_id)
    if not b:
        await call.answer("Не найдено", show_alert=True)
        return

    car = await get_car(session, b.car_id)

    text = "\n".join([
        "📋 <b>Бронь</b>",
        f"ID: <code>{b.id}</code>",
        f"Статус: {_status_ru(b.status)}",
        f"Авто: <b>{car.name}</b>",
        f"Период: {b.start_date:%d.%m.%Y} - {b.end_date:%d.%m.%Y}",
        f"Сумма: {b.total_price}₽",
        f"Телефон: {b.phone}",
    ])


    extra = []
    if b.status == BookingStatus.waiting_payment.value:
        extra.append([
            InlineKeyboardButton(
                text="💳 Подтвердить оплату",
                callback_data=f"admin:bk:confirm:{b.id}:{page}"
            )
        ])

    kb = admin_booking_manage_kb(booking_id=b.id, page=page, extra=extra)

    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("admin:dates:clear:"))
async def admin_clear_dates(call: CallbackQuery, session):
    car_id = int(call.data.split(":")[3])

    await session.execute(
        delete(BookingDate).where(BookingDate.car_id == car_id)
    )
    await session.commit()

    await call.answer("Все даты очищены", show_alert=True)


@router.callback_query(F.data == "admin:add_car:finish")
async def admin_add_car_finish(call: CallbackQuery, session, state: FSMContext):
    data = await state.get_data()

    photos = data.get("photos", [])
    name = data["name"]
    description = data["description"]
    price = data["price_per_day"]

    car = await create_car(
        session,
        name=name,
        description=description,
        price_per_day=price,
        image_url=json.dumps(photos)
    )

    await state.clear()
    await call.message.edit_text(f"Авто добавлено: {car.name}")


from aiogram import F
from aiogram.fsm.context import FSMContext

class AdminEditCarState(StatesGroup):
    waiting_value = State()

@router.message(AdminEditCarState.waiting_value)
async def admin_edit_value(message: Message, state: FSMContext, session):
    data = await state.get_data()
    car_id = data.get("car_id")
    field = data.get("field")
    page = data.get("page")

    text = message.text.strip()

    # --- РЕДАКТИРОВАНИЕ ЦЕНЫ ---
    if field == "price":
        raw = text.replace(",", ".")
        try:
            price = float(raw)
        except ValueError:
            await message.answer("Введи число, например: 2500 или 2500.5")
            return

        car = await get_car(session, car_id)
        car.price = price
        await session.commit()

        await message.answer("Цена обновлена ✅")
        await state.clear()
        return
    # --- РЕДАКТИРОВАНИЕ ДВИГАТЕЛЯ ---
    if field == "engine":
        value = message.text.strip()

        car = await get_car(session, car_id)
        if not car:
            await message.answer("Машина не найдена")
            await state.clear()
            return

        car.engine = value
        await session.commit()

        await message.answer("Двигатель обновлён ✅")
        await state.clear()
        return

        # --- РЕДАКТИРОВАНИЕ КОРОБКИ ---
    if field == "trans":
        value = message.text.strip()

        car = await get_car(session, car_id)
        if not car:
            await message.answer("Машина не найдена")
            await state.clear()
            return

        car.transmission = value
        await session.commit()

        await message.answer("Коробка обновлена ✅")
        await state.clear()
        return

    # --- ЕСЛИ ПОЛЕ НЕ price ---
    await message.answer(f"Редактирование поля '{field}' пока не реализовано")
    await state.clear()

@router.callback_query(F.data.startswith("admin:bookings:p:"))
async def admin_booking_list(call: CallbackQuery, session):
    parts = call.data.split(":")
    page = int(parts[2])
    await send_booking_list(call.message, session, page)
