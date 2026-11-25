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


# 서버 시작 로그에 '설정됨/없음' 만 남길 환경 변수 (값은 절대 출력하지 않는다)
REPORTED_ENV = (
    "SECRET_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "VAPID_PUBLIC_KEY",
    "VAPID_PRIVATE_KEY",
    "VAPID_SUBJECT",
    "ADMIN_EMAIL",
    "MAX_USERS",
    "BASE_URL",
)


def env_presence(names: tuple[str, ...] = REPORTED_ENV) -> dict[str, bool]:
    """이름 → 설정 여부. 없거나, 빈 문자열이거나, 공백·따옴표뿐이면 '없음'. 값은 반환하지 않는다."""
    return {n: _secret_env(n) != "" for n in names}


def env_presence_line() -> str:
    return "[config] " + " ".join(f"{n}={'설정됨' if ok else '없음'}" for n, ok in env_presence().items())


class Settings:
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key-change-me")
    base_url: str = (os.getenv("BASE_URL") or _default_base_url()).rstrip("/")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/app.db")
    timezone: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))
    scheduler_enabled: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    # 가입 정원 (관리자 제외). 새 사용자를 만드는 순간에만 확인, 기존 사용자 로그인은 항상 허용
    max_users: int = int(_secret_env("MAX_USERS") or 50)
    admin_email: str = _secret_env("ADMIN_EMAIL").lower()
    # 구글 로그인 (Google Cloud Console 의 OAuth 클라이언트)
    google_client_id: str = _secret_env("GOOGLE_CLIENT_ID")
    google_client_secret: str = _secret_env("GOOGLE_CLIENT_SECRET")
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
        if self.is_production and not self.push_ready:
            problems.append("웹 푸시를 쓰려면 VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY / VAPID_SUBJECT 가 필요합니다.")
        if self.is_production and self.secret_key == "dev-secret-key-change-me":
            problems.append("SECRET_KEY 를 설정해야 합니다.")
        return problems


settings = Settings()
