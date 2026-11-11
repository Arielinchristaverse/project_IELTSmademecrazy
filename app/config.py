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


def _secret_env(name: str, default: str = "") -> str:
    """대시보드에 붙여 넣은 값 정리: 앞뒤 공백·따옴표, 실수로 함께 붙인 'NAME=' 접두어를 제거한다."""
    v = (os.getenv(name) or default).strip().strip("'\"").strip()
    if v.startswith(name + "="):
        v = v[len(name) + 1 :].strip().strip("'\"").strip()
    return v


class Settings:
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    base_url: str = (os.getenv("BASE_URL") or _default_base_url()).rstrip("/")
    mail_backend: str = os.getenv("MAIL_BACKEND", "console")
    gmail_address: str = _secret_env("GMAIL_ADDRESS")
    gmail_app_password: str = os.getenv("GMAIL_APP_PASSWORD", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    timezone: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    token_valid_days: int = 14
    # 서버에서 켤 알림 수단. 사용자는 이 중에서 고른다 (push, email)
    notify_channels: tuple[str, ...] = tuple(
        c.strip() for c in os.getenv("NOTIFY_CHANNELS", "push,email").split(",") if c.strip()
    )
    admin_email: str = _secret_env("ADMIN_EMAIL").lower()
    # 구글 로그인 (Google Cloud Console 의 OAuth 클라이언트)
    google_client_id: str = _secret_env("GOOGLE_CLIENT_ID")
    google_client_secret: str = _secret_env("GOOGLE_CLIENT_SECRET")
    # 비어 있으면 누구나 가입, 값이 있으면 목록(쉼표 구분)과 ADMIN_EMAIL 만 가입
    allowed_emails: frozenset[str] = frozenset(
        e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()
    )
    # 웹 푸시 VAPID 키 (python -m scripts.gen_vapid 로 생성)
    vapid_public_key: str = _secret_env("VAPID_PUBLIC_KEY")
    vapid_private_key: str = _secret_env("VAPID_PRIVATE_KEY")
    vapid_subject: str = _secret_env("VAPID_SUBJECT")  # mailto:you@example.com 또는 https://사이트주소
    # 개발·테스트용 이메일 로그인 (운영에서 켜져 있으면 서버가 시작을 거부)
    dev_login: bool = os.getenv("DEV_LOGIN", "false").lower() == "true"
    # 인터넷에 공개된 서버(Render)에서 실행 중인지
    is_production: bool = os.getenv("RENDER", "").lower() == "true"

    @property
    def push_ready(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key and self.vapid_subject)

    def production_problems(self) -> list[str]:
        """운영 서버에서 위험한 설정. 하나라도 있으면 서버가 시작을 거부한다."""
        problems = []
        if self.is_production and self.dev_login:
            problems.append("DEV_LOGIN 은 운영 서버에서 켤 수 없습니다.")
        if self.is_production and "push" in self.notify_channels and not self.push_ready:
            problems.append("웹 푸시를 쓰려면 VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT 가 필요합니다.")
        if self.is_production and self.secret_key == "dev-secret-key-change-me":
            problems.append("SECRET_KEY 를 설정해야 합니다.")
        return problems


settings = Settings()
