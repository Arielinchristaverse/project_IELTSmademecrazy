import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import settings


def send_mail(to: str, subject: str, html: str, text: str) -> str:
    """메일 발송. console 모드에서는 outbox/ 에 HTML 파일로 저장한다. 반환: 결과 설명."""
    if settings.mail_backend == "console":
        os.makedirs(OUTBOX, exist_ok=True)
        path = os.path.join(OUTBOX, f"{datetime.now():%Y%m%d-%H%M%S}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[mail:console] to={to} subject={subject} -> {path}")
        return "console 모드: 실제 발송 대신 저장했습니다. 아래 '메일 미리보기'로 확인하세요."

    if not settings.gmail_address or not settings.gmail_app_password:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD 가 설정되지 않았습니다.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(settings.gmail_address, settings.gmail_app_password.replace(" ", ""))
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "Gmail 로그인 실패: 앱 비밀번호가 틀렸거나, GMAIL_ADDRESS 가 비밀번호를 만든 계정과 다릅니다."
        ) from e
    except (OSError, TimeoutError) as e:
        raise RuntimeError(f"Gmail 서버(smtp.gmail.com:465)에 연결하지 못했습니다. 네트워크가 메일 포트를 막고 있을 수 있습니다. ({e})") from e
    return f"{to} 로 발송 완료"


def mail_status() -> dict:
    """설정 화면에 보여줄 메일 연결 상태 (비밀번호 값 자체는 노출하지 않음)."""
    return {
        "backend": settings.mail_backend,
        "address": settings.gmail_address,
        "has_password": bool(settings.gmail_app_password),
        "base_url": settings.base_url,
    }


OUTBOX = "outbox"
_NAME = re.compile(r"^\d{8}-\d{6}\.html$")


def latest_outbox_file() -> str | None:
    if not os.path.isdir(OUTBOX):
        return None
    names = sorted(n for n in os.listdir(OUTBOX) if _NAME.match(n))
    return names[-1] if names else None


def read_outbox(name: str) -> str | None:
    if not _NAME.match(name):
        return None
    path = os.path.join(OUTBOX, name)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()
