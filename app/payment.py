from __future__ import annotations

import base64
import uuid
from decimal import Decimal

import aiohttp

from app.config import settings


class YooKassaClient:
    base_url = "https://api.yookassa.ru/v3"

    def __init__(self) -> None:
        key = f"{settings.yookassa_shop_id}:{settings.yookassa_secret_key}"
        self.auth = base64.b64encode(key.encode("utf-8")).decode("utf-8")

    async def create_payment(self, amount: Decimal, description: str, return_url: str, metadata: dict[str, str]) -> dict:
        payload = {
            "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": return_url},
            "description": description,
            "metadata": metadata,
        }
        headers = {
            "Authorization": f"Basic {self.auth}",
            "Idempotence-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/payments", json=payload, headers=headers, timeout=20) as response:
                response.raise_for_status()
                return await response.json()

    async def refund_payment(self, payment_id: str, amount: Decimal) -> dict:
        payload = {"payment_id": payment_id, "amount": {"value": f"{amount:.2f}", "currency": "RUB"}}
        headers = {
            "Authorization": f"Basic {self.auth}",
            "Idempotence-Key": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/refunds", json=payload, headers=headers, timeout=20) as response:
                response.raise_for_status()
                return await response.json()
