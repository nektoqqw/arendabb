"""Custom aiohttp session for Telegram API (Windows / TLS workarounds)."""
from __future__ import annotations

import logging
import socket
import ssl

import certifi
from aiogram.client.session.aiohttp import AiohttpSession

logger = logging.getLogger(__name__)


class TelegramAiohttpSession(AiohttpSession):
    """
    Workarounds for ``SSL: INVALID_SESSION_ID`` / TLS issues on Windows
    (often antivirus HTTPS inspection):

    - ``truststore`` uses the OS trust store (often matches AV MITM certs).
    - ``force_tls12`` avoids some TLS 1.3 session-ticket bugs.
    - ``ipv4_only`` if IPv6 route is broken.
    - ``insecure_ssl``: aiohttp ``ssl=False`` (no cert verify — last resort only).
    - ``proxy``: HTTP(S) or SOCKS5 URL if Telegram API is blocked (VPN local port, etc.).
    """

    def __init__(
        self,
        proxy: str | None = None,
        insecure_ssl: bool = False,
        force_tls12: bool = False,
        ipv4_only: bool = False,
        limit: int = 100,
        **kwargs,
    ) -> None:
        super().__init__(proxy=proxy, limit=limit, **kwargs)
        if proxy:
            logger.info("Telegram connection: using proxy from TELEGRAM_PROXY")
        if ipv4_only:
            self._connector_init["family"] = socket.AF_INET
        if insecure_ssl:
            self._connector_init["ssl"] = False
            self._connector_init["force_close"] = True
            logger.warning("TELEGRAM_INSECURE_SSL enabled — certificate verification is OFF")
            return
        try:
            import truststore

            ctx = truststore.ssl_context()
            logger.info("Telegram HTTPS: using truststore (OS certificate store)")
        except ImportError:
            ctx = ssl.create_default_context(cafile=certifi.where())
            ctx.check_hostname = True
            ctx.verify_mode = ssl.CERT_REQUIRED
            logger.info("Telegram HTTPS: using certifi bundle (install truststore for Windows store)")
        ctx.options |= ssl.OP_NO_TICKET
        if force_tls12:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
            logger.info("Telegram HTTPS: TLS 1.2 only")
        self._connector_init["ssl"] = ctx
        self._connector_init["force_close"] = True
