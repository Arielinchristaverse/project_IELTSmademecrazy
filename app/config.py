import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _default_base_url() -> str:
    # Render는 서비스의 공개 주소를 RENDER_EXTERNAL_URL 로 넣어 준다
    if os.getenv("RENDER_EXTERNAL_URL"):
        return os.environ["RENDER_EXTERNAL_URL"]
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
    # 서버에서 켤 알림 수단. 사용자는 이 중에서 고른다 (push, email)
    notify_channels: tuple[str, ...] = tuple(
        c.strip() for c in os.getenv("NOTIFY_CHANNELS", "push,email").split(",") if c.strip()
    )
    admin_email: str = os.getenv("ADMIN_EMAIL", "").strip().lower()
    # 인터넷에 공개된 서버(Render)에서 실행 중인지
    is_production: bool = os.getenv("RENDER", "").lower() == "true"

    @property
    def password_is_default(self) -> bool:
        return self.app_password in ("", "change-me")


settings = Settings()
