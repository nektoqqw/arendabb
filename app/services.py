from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models import Blackout, Booking, BookingStatus, Car, User


@dataclass
class CarFilters:
    price: str | None = None  # lt_2000 | between_2000_4000 | gte_4000
    transmission: str | None = None  # automatic | manual


PAGE_SIZE = 3
ADMIN_PAGE_SIZE = 6


def diff_rental_days(start: datetime, end: datetime) -> int:
    d = (end.date() - start.date()).days
    return max(1, d)


async def upsert_user(
    session: AsyncSession,
    telegram_id: int,
    first_name: str,
    last_name: str | None,
    username: str | None,
) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        user = User(
            telegram_id=telegram_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
        )
        session.add(user)
    else:
        user.first_name = first_name
        user.last_name = last_name
        user.username = username
    await session.commit()
    await session.refresh(user)
    return user


#async def ensure_seed_cars(session: AsyncSession) -> None:
#   if count and count > 0:
        # Если ранее были seed-ссылки example.com — очистим их, чтобы Telegram не пытался скачать несуществующие картинки.
 #########################################################################


async def create_car(
    session: AsyncSession,
    *,
    name: str,
    description: str,
    price_per_day: int,
    image_url: str,
) -> Car:
    car = Car(
        name=name,
        description=description,
        price_per_day=price_per_day,
        engine="-",
        transmission="automatic",
        seats=5,
        has_ac=True,
        image_url=image_url,
        is_available=True,
    )
    session.add(car)
    await session.commit()
    await session.refresh(car)
    return car


async def update_car_fields(session: AsyncSession, car_id: int, **fields) -> Car | None:
    car = await get_car(session, car_id)
    if not car:
        return None
    for k, v in fields.items():
        if not hasattr(car, k):
            continue
        setattr(car, k, v)
    await session.commit()
    await session.refresh(car)
    return car


async def count_cars_admin(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count(Car.id))) or 0)


async def list_cars_admin_page(session: AsyncSession, page: int) -> tuple[list[Car], int]:
    page = max(1, page)
    total = await count_cars_admin(session)
    total_pages = max(1, (total + ADMIN_PAGE_SIZE - 1) // ADMIN_PAGE_SIZE)
    q = (
        select(Car)
        .order_by(Car.created_at.asc())
        .offset((page - 1) * ADMIN_PAGE_SIZE)
        .limit(ADMIN_PAGE_SIZE)
    )
    rows = list((await session.scalars(q)).all())
    return rows, total_pages


async def car_has_any_booking(session: AsyncSession, car_id: int) -> bool:
    q = select(Booking.id).where(
        Booking.car_id == car_id,
        Booking.status.in_(["waiting_payment", "paid", "active"])
    ).limit(1)

    return (await session.scalar(q)) is not None

async def car_has_blocked_dates(session, car_id):
    # blackout
    q1 = select(Blackout.id).where(Blackout.car_id == car_id).limit(1)
    if (await session.execute(q1)).first():
        return True

    # брони
    q2 = select(Booking.id).where(Booking.car_id == car_id).limit(1)
    if (await session.execute(q2)).first():
        return True

    return False





async def delete_car(session: AsyncSession, car_id: int) -> tuple[bool, str]:
    car = await get_car(session, car_id)
    if not car:
        return False, "NOT_FOUND"

    # Есть активные брони?
    q1 = select(Booking.id).where(Booking.car_id == car_id).limit(1)
    if (await session.execute(q1)).first():
        return False, "HAS_BOOKINGS"

    # Есть blackout?
    q2 = select(Blackout.id).where(Blackout.car_id == car_id).limit(1)
    if (await session.execute(q2)).first():
        return False, "HAS_BLOCKED_DATES"

    # Удаляем машину
    await session.delete(car)
    await session.commit()

    return True, "OK"



def _car_filter_query(filters: CarFilters | None):
    q = select(Car).where(Car.is_available.is_(True))
    if not filters:
        return q
    if filters.transmission:
        q = q.where(Car.transmission == filters.transmission)
    if filters.price == "lt_2000":
        q = q.where(Car.price_per_day < 2000)
    elif filters.price == "between_2000_4000":
        q = q.where(and_(Car.price_per_day >= 2000, Car.price_per_day <= 4000))
    elif filters.price == "gte_4000":
        q = q.where(Car.price_per_day >= 4000)
    return q


async def count_cars(session: AsyncSession, filters: CarFilters | None) -> int:
    subq = _car_filter_query(filters).subquery()
    return int(await session.scalar(select(func.count()).select_from(subq)) or 0)


async def list_cars_page(
    session: AsyncSession,
    page: int,
    filters: CarFilters | None,
) -> tuple[list[Car], int]:
    page = max(1, page)
    total = await count_cars(session, filters)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    q = (
        _car_filter_query(filters)
        # Новые авто (добавленные админом) показываем первыми — чаще у них есть корректный file_id фото.
        .order_by(Car.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )
    rows = list((await session.scalars(q)).all())
    return rows, total_pages


async def get_car(session: AsyncSession, car_id: int) -> Car | None:
    return await session.scalar(select(Car).where(Car.id == car_id))


from sqlalchemy import select, and_

async def has_overlapping_booking(session, car_id, start_date, end_date):
    result = await session.execute(
        select(Booking).where(
            Booking.car_id == car_id,
            Booking.status != "cancelled",  # если есть отменённые
            and_(
                Booking.start_date <= end_date,
                Booking.end_date >= start_date,
            )
        )
    )
    return result.scalar_one_or_none() is not None



from sqlalchemy import select, and_

async def has_overlapping_blackout(session, car_id, start_date, end_date):
    result = await session.execute(
        select(Blackout).where(
            Blackout.car_id == car_id,
            Blackout.day >= start_date,
            Blackout.day <= end_date
        )
    )
    return result.first() is not None




async def create_booking(
    session: AsyncSession,
    user_id: int,
    car: Car,
    start_date: datetime,
    end_date: datetime,
    phone: str,
) -> Booking:
    if await has_overlapping_booking(session, car.id, start_date, end_date):
        raise ValueError("CAR_BUSY")
    if await has_overlapping_blackout(session, car.id, start_date, end_date):
        raise ValueError("CAR_BUSY")
    days = diff_rental_days(start_date, end_date)
    total = days * car.price_per_day
    booking = Booking(
        id=str(uuid4()),
        user_id=user_id,
        car_id=car.id,
        start_date=start_date,
        end_date=end_date,
        total_price=total,
        phone=phone,
        status=BookingStatus.waiting_payment.value,
    )
    session.add(booking)
    await session.commit()
    await session.refresh(booking)
    return booking


async def get_user_bookings(session: AsyncSession, user_id: int) -> list[Booking]:
    q = (
        select(Booking)
        .where(Booking.user_id == user_id)
        .options(selectinload(Booking.car))
        .order_by(Booking.created_at.desc())
    )
    return list((await session.scalars(q)).all())


async def get_booking(session: AsyncSession, booking_id: str) -> Booking | None:
    return await session.scalar(
        select(Booking).where(Booking.id == booking_id).options(selectinload(Booking.car))
    )


async def cancel_booking_user(session: AsyncSession, booking_id: str, user_id: int) -> tuple[bool, bool]:
    """Возвращает (успех, нужен_менеджер). need_manager=True если &lt; 24ч до старта."""
    booking = await session.scalar(select(Booking).where(Booking.id == booking_id, Booking.user_id == user_id))
    if not booking:
        return False, False
    if booking.status not in (BookingStatus.waiting_payment.value, BookingStatus.confirmed.value):
        return False, False
    now = datetime.now(UTC).replace(tzinfo=None)
    start = booking.start_date
    if start.tzinfo:
        start = start.replace(tzinfo=None)
    if start - now < timedelta(hours=24):
        return False, True
    booking.status = BookingStatus.cancelled.value
    await session.commit()
    return True, False


async def cancel_booking_admin(session: AsyncSession, booking_id: str) -> bool:
    booking = await session.scalar(select(Booking).where(Booking.id == booking_id))
    if not booking:
        return False
    if booking.status == BookingStatus.cancelled.value:
        return False
    booking.status = BookingStatus.cancelled.value
    await session.commit()
    return True


from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from app.models import Booking, BookingStatus, Car

async def list_bookings_admin_page(session, page: int, page_size: int = 10):
    # Загружаем брони + связанные авто (selectinload = безопасно в async)
    base_query = (
        select(Booking)
        .options(selectinload(Booking.car))  # ← ВАЖНО
        .where(Booking.status != BookingStatus.cancelled.value)
        .order_by(Booking.created_at.desc())
    )

    q = (
        base_query
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    rows = list((await session.scalars(q)).all())

    total_count = await session.scalar(
        select(func.count()).select_from(
            select(Booking)
            .where(Booking.status != BookingStatus.cancelled.value)
            .subquery()
        )
    )

    total_pages = max(1, (total_count + page_size - 1) // page_size)

    return rows, total_pages

def _daterange(a: date, b: date) -> list[date]:
    if b < a:
        a, b = b, a
    days = (b - a).days
    return [a + timedelta(days=i) for i in range(days + 1)]


async def add_blackout_range(
    session: AsyncSession,
    *,
    car_id: int,
    start_day: date,
    end_day: date,
    source: str = "external",
    note: str = "",
) -> int:
    days = _daterange(start_day, end_day)
    added = 0
    for d in days:
        # day храним как datetime на 00:00
        dt = datetime.combine(d, datetime.min.time())
        exists = await session.scalar(
            select(Blackout.id).where(Blackout.car_id == car_id, func.date(Blackout.day) == d.isoformat()).limit(1)
        )
        if exists:
            continue
        session.add(Blackout(car_id=car_id, day=dt, source=source, note=note))
        added += 1
    await session.commit()
    return added


async def list_blocked_days_for_month(session: AsyncSession, *, car_id: int, year: int, month: int) -> set[date]:
    # Черные даты
    m_start = date(year, month, 1)
    if month == 12:
        m_next = date(year + 1, 1, 1)
    else:
        m_next = date(year, month + 1, 1)
    m_end = m_next - timedelta(days=1)

    blocked: set[date] = set()
    q_bl = select(Blackout.day).where(
        Blackout.car_id == car_id,
        func.date(Blackout.day) >= m_start.isoformat(),
        func.date(Blackout.day) <= m_end.isoformat(),
    )
    for dt in list((await session.execute(q_bl)).scalars().all()):
        blocked.add(dt.date())

    # Даты активных броней Telegram
    active = [BookingStatus.waiting_payment.value, BookingStatus.confirmed.value]
    q_bk = select(Booking.start_date, Booking.end_date).where(
        Booking.car_id == car_id,
        Booking.status.in_(active),
        Booking.start_date <= datetime.combine(m_end, datetime.max.time()),
        Booking.end_date >= datetime.combine(m_start, datetime.min.time()),
    )
    for st, en in list((await session.execute(q_bk)).all()):
        d1 = st.date()
        d2 = en.date()
        # блокируем каждый день диапазона
        for d in _daterange(d1, d2):
            if m_start <= d <= m_end:
                blocked.add(d)
    return blocked


async def aggregate_user_stats(session: AsyncSession, user_id: int) -> tuple[int, int]:
    total_count = int(await session.scalar(select(func.count(Booking.id)).where(Booking.user_id == user_id)) or 0)
    total_sum = int(
        await session.scalar(
            select(func.coalesce(func.sum(Booking.total_price), 0)).where(
                Booking.user_id == user_id,
                Booking.status != BookingStatus.cancelled.value,
            )
        )
        or 0
    )
    return total_count, total_sum


def make_db_backup(db_url: str) -> Path | None:
    if "sqlite" not in db_url:
        return None
    sqlite_path = db_url.split("///")[-1]
    source = Path(sqlite_path)
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        return None
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target = settings.backup_dir_path / f"backup_{timestamp}.db"
    shutil.copy2(source, target)
    return target

from datetime import date
from sqlalchemy import select
from app.models import Booking, BookingStatus
from app.db import async_sessionmaker  # адаптируй под свой импорт

async def auto_complete_bookings():
    async with async_sessionmaker() as session:
        today = date.today()
        rows = await session.execute(
            select(Booking).where(
                Booking.end_date < today,
                Booking.status == BookingStatus.confirmed.value,
            )
        )
        bookings = rows.scalars().all()
        for b in bookings:
            b.status = BookingStatus.completed.value
        await session.commit()

from datetime import date, timedelta
from sqlalchemy import select
from app.models import Booking, BookingStatus
from app.db import async_sessionmaker
 # или откуда ты берёшь bot

async def notify_upcoming_rentals():
    async with async_sessionmaker() as session:
        tomorrow = date.today() + timedelta(days=1)
        rows = await session.execute(
            select(Booking).where(
                Booking.start_date == tomorrow,
                Booking.status == BookingStatus.confirmed.value,
            )
        )
        bookings = rows.scalars().all()
        for b in bookings:
            try:
                await bot.send_message(
                    b.user_id,  # если поле так называется, иначе адаптируй
                    f"📅 Напоминаем! Завтра начинается ваша аренда авто {b.car.name}.\n"
                    f"Период: {b.start_date:%d.%m.%Y}–{b.end_date:%d.%m.%Y}"
                )
            except Exception:
                pass


async def send_clean_message(bot, user_id, state, text=None, photo=None, reply_markup=None):
    data = await state.get_data()
    last_msg = data.get("last_msg")

    # удаляем старое сообщение
    if last_msg:
        try:
            await bot.delete_message(user_id, last_msg)
        except:
            pass

    # отправляем новое
    if photo:
        msg = await bot.send_photo(user_id, photo, caption=text, reply_markup=reply_markup)
    else:
        msg = await bot.send_message(user_id, text, reply_markup=reply_markup)

    # сохраняем id
    await state.update_data(last_msg=msg.message_id)

    return msg
