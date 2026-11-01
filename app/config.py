import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _default_base_url() -> str:
    # GitHub Codespaces에서는 포워딩된 https 주소를 자동으로 사용
    name = os.getenv("CODESPACE_NAME")
    domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if name and domain:
        return f"https://{name}-8000.{domain}"
    return "http://localhost:8000"


class Settings:
    app_password: str = os.getenv("APP_PASSWORD", "change-me")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    base_url: str = (os.getenv("BASE_URL") or _default_base_url()).rstrip("/")
    mail_backend: str = os.getenv("MAIL_BACKEND", "console")
    gmail_address: str = os.getenv("GMAIL_ADDRESS", "")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    timezone: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    token_valid_days: int = 14


settings = Settings()
