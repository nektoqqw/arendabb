from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable, Deque, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.config import settings


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        self._hits: Dict[int, Deque[float]] = defaultdict(deque)
        self._limit = settings.max_requests_per_minute

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        now = time.time()
        bucket = self._hits[user.id]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= self._limit:
            if hasattr(event, "answer"):
                await event.answer("⛔ Слишком много запросов. Попробуйте через минуту.")
            return None
        bucket.append(now)
        return await handler(event, data)
