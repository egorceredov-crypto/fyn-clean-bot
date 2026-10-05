from dataclasses import dataclass
from dotenv import load_dotenv
import os

load_dotenv()

@dataclass
class Config:
    bot_token: str
    admin_chat_id: int
    work_start: str
    work_end: str
    timezone: str
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    session_string: str | None = None
    owner_session: str | None = None
    telegram_proxy: str | None = None

def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty in .env")

    admin_chat_id = int(os.getenv("ADMIN_CHAT_ID", "-1000").strip())
    telegram_api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    telegram_api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_string = os.getenv("SESSION_STRING", "").strip()
    owner_session = os.getenv("OWNER_SESSION", "").strip()
    telegram_proxy = os.getenv("TELEGRAM_PROXY", "").strip() or None
    return Config(
        bot_token=token,
        admin_chat_id=admin_chat_id,
        work_start=os.getenv("WORK_START", "09:00").strip(),
        work_end=os.getenv("WORK_END", "21:00").strip(),
        timezone=os.getenv("TIMEZONE", "Europe/Moscow").strip(),
        telegram_api_id=int(telegram_api_id) if telegram_api_id else None,
        telegram_api_hash=telegram_api_hash or None,
        session_string=session_string or None,
        owner_session=owner_session or None,
        telegram_proxy=telegram_proxy,
    )