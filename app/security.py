"""남용 방지 · 보안 헤더 · 요청 위조(CSRF) 방어 · 접속 로그 가림.

요청 횟수 제한
- 분 단위 제한은 서버 메모리에만 둔다(서버 1대 운영). 재시작하면 초기화된다.
- IP 는 로그인 관련 제한에만, 메모리에서 최대 1분 동안만 쓰고 저장하거나 로그에 남기지 않는다.
- 하루 단위 제한(테스트 알림)은 DB 하루 집계(daily_counters)에 둔다.
"""
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .config import settings

# ---------------------------------------------------------------------------
# 요청 횟수 제한
# ---------------------------------------------------------------------------
LIMITS = {
    # 이름: (허용 횟수, 기간(초), 기준)
    "login": (10, 60, "ip"),          # 로그인 시작·콜백·개발용 로그인·동의 제출·넘겨받기
    "subscribe": (10, 60, "user"),    # 푸시 구독 등록·상태 확인
    "record": (60, 60, "user"),       # 달성/미달성·추가 달성 기록
    "handoff": (30, 60, "ip"),        # 앱이 다시 열릴 때 로그인 넘겨받기 확인
}
TEST_PUSH_PER_DAY = 5
MESSAGES = {
    "login": "로그인 시도가 너무 많아요. 1분 뒤에 다시 시도해 주세요.",
    "subscribe": "알림 설정 요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요.",
    "record": "기록 요청이 너무 많아요. 잠시 뒤에 다시 시도해 주세요.",
    "handoff": "잠시 뒤에 다시 시도해 주세요.",
    "test_push": f"테스트 알림은 하루 {TEST_PUSH_PER_DAY}번까지 보낼 수 있어요. 내일 다시 시도해 주세요.",
}


class RateLimited(HTTPException):
    def __init__(self, bucket: str):
        super().__init__(status_code=429, detail=MESSAGES[bucket])
        self.bucket = bucket


class _Window:
    def __init__(self):
        self.lock = threading.Lock()
        self.hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def hit(self, bucket: str, key: str, limit: int, period: int) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[(bucket, key)]
            while q and now - q[0] > period:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self.hits) > 50_000:  # 오래된 키 정리 (메모리 상한)
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] > 120]:
                    del self.hits[k]
            return True

    def clear(self) -> None:
        with self.lock:
            self.hits.clear()


window = _Window()


def client_ip(request: Request) -> str:
    """접속 IP. Render 프록시는 실제 접속 주소를 X-Forwarded-For 의 맨 뒤에 붙이므로 마지막 값을 쓴다
    (앞쪽 값은 요청자가 꾸밀 수 있어 쓰지 않는다). 프록시가 없으면(로컬) 연결 주소."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip() or "-"
    return request.client.host if request.client else "-"


def limit(bucket: str):
    """FastAPI 의존성: 이 경로에 횟수 제한을 건다."""
    def check(request: Request) -> None:
        count, period, basis = LIMITS[bucket]  # 호출할 때 읽는다 (리허설 스크립트 등에서 조정 가능)
        key = client_ip(request) if basis == "ip" else str(request.session.get("uid") or client_ip(request))
        if not window.hit(bucket, key, count, period):
            raise RateLimited(bucket)

    return check


# ---------------------------------------------------------------------------
# 요청 위조(CSRF) 방어
# ---------------------------------------------------------------------------
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower() if p.scheme and p.netloc else ""


def same_origin(request: Request) -> bool:
    """상태를 바꾸는 요청은 우리 사이트에서 보낸 것만: Origin, 없으면 Referer 로 확인. 둘 다 없으면 거부."""
    allowed = {_origin(settings.base_url), f"{request.url.scheme}://{request.headers.get('host', '')}".lower()}
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return origin.lower() in allowed
    referer = request.headers.get("referer")
    if referer:
        return _origin(referer) in allowed
    return False


def csrf_token(request: Request) -> str:
    """세션별 폼 토큰 (계정 삭제·동의 제출에 함께 쓴다)."""
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def check_form_token(request: Request, value: str | None) -> bool:
    expected = request.session.get("csrf")
    return bool(expected and value and secrets.compare_digest(expected, value))


# ---------------------------------------------------------------------------
# 보안 헤더
# ---------------------------------------------------------------------------
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; manifest-src 'self'; worker-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)


def security_headers(request: Request, headers) -> None:
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer" if request.url.path.startswith("/auth/") else "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    headers.setdefault("Content-Security-Policy", CSP)
    if settings.is_production:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


# ---------------------------------------------------------------------------
# 접속 로그 가림 (uvicorn.access)
# ---------------------------------------------------------------------------
_SECRET_QS = re.compile(r"(?i)\b(code|state|token|nonce|error_description)=[^&\s]*")


def mask_path(path: str) -> str:
    """주소의 일회용 값(code, state 등)을 가린다."""
    return _SECRET_QS.sub(lambda m: f"{m.group(1)}=***", path)


class AccessLogFilter(logging.Filter):
    """접속 기록: 이용자 IP 는 남기지 않고('-'), 주소의 code·state 등은 가린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            client, method, path, version, status = args
            record.args = ("-", method, mask_path(str(path)), version, status)
        return True


def install_log_filters() -> None:
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessLogFilter) for f in lg.filters):
        lg.addFilter(AccessLogFilter())
