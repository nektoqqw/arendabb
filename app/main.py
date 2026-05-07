from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import text

from app.config import settings
from app.telegram_session import TelegramAiohttpSession
from app.db import Base, SessionLocal, engine
from app.handlers import router
from app.logging_setup import setup_logging
from app.middlewares import AntiFloodMiddleware
from app.scheduler import build_scheduler

logger = logging.getLogger(__name__)


async def _fail_if_legacy_salon_db(conn) -> None:
    """Старая схема (full_name, role) несовместима с ботом аренды."""
    res = await conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    )
    if res.scalar_one_or_none() is None:
        return
    cols = await conn.execute(text("PRAGMA table_info(users)"))
    names = {row[1] for row in cols}
    if "full_name" in names and "first_name" not in names:
        raise SystemExit(
            "База data/bot.db — от старого бота (салон). Текущему коду нужна новая схема.\n\n"
            "В .env укажите другой файл, например:\n"
            "  DB_URL=sqlite+aiosqlite:///./data/rental.db\n\n"
            "Либо удалите старый файл БД и оставьте прежний DB_URL."
        )

async def _ensure_car_description_column(conn) -> None:
    if "sqlite" not in settings.db_url:
        return
    cols = await conn.execute(text("PRAGMA table_info(cars)"))
    names = {row[1] for row in cols}
    if "description" not in names:
        await conn.execute(text("ALTER TABLE cars ADD COLUMN description TEXT DEFAULT ''"))


async def _ensure_user_registered_name_column(conn) -> None:
    if "sqlite" not in settings.db_url:
        return
    cols = await conn.execute(text("PRAGMA table_info(users)"))
    names = {row[1] for row in cols}
    if "registered_name" not in names:
        await conn.execute(text("ALTER TABLE users ADD COLUMN registered_name VARCHAR(255)"))


class DbSessionMiddleware:
    async def __call__(self, handler, event, data):
        async with SessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


async def on_startup(bot: Bot) -> None:
    # Safety: if webhook was set before, clear it for polling mode.
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        logger.warning("Could not delete webhook (network/SSL?): %s", exc)
    logger.info("Webhook disabled, running in polling mode")
    async with engine.begin() as conn:
        if "sqlite" in settings.db_url:
            await _fail_if_legacy_salon_db(conn)
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_car_description_column(conn)
        await _ensure_user_registered_name_column(conn)


def _ensure_proxy_deps() -> None:
    if not settings.telegram_proxy:
        return
    try:
        import aiohttp_socks  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "В .env указан TELEGRAM_PROXY, но не установлен пакет aiohttp-socks "
            "(aiogram требует его для любого прокси).\n\n"
            "Выполни в PowerShell:\n"
            "  .\\.venv\\Scripts\\python.exe -m pip install aiohttp-socks --default-timeout 120 --retries 10\n\n"
            "Если прокси пока не нужен — удали или закомментируй строку TELEGRAM_PROXY в .env."
        ) from exc


async def main() -> None:
    setup_logging()
    _ensure_proxy_deps()
    session = TelegramAiohttpSession(
        proxy=settings.telegram_proxy,
        insecure_ssl=settings.telegram_insecure_ssl,
        force_tls12=settings.telegram_force_tls12,
        ipv4_only=settings.telegram_ipv4_only,
    )
    bot = Bot(
        token=settings.bot_token,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    dp.update.outer_middleware(AntiFloodMiddleware())
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.startup.register(on_startup)

    scheduler = build_scheduler()
    scheduler.start()

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

from app.scheduler import build_scheduler
from app.handlers import notify_upcoming_rentals
from app.services import auto_complete_bookings

scheduler = build_scheduler()

scheduler.add_job(lambda: notify_upcoming_rentals(bot), "cron", hour=9)
scheduler.add_job(auto_complete_bookings, "interval", hours=1)

scheduler.start()
