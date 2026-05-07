import logging
from pathlib import Path

from app.config import settings


def setup_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(logs_dir / "bot.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
