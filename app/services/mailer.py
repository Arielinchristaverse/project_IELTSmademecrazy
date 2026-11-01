import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import settings


def send_mail(to: str, subject: str, html: str, text: str) -> str:
    """메일 발송. console 모드에서는 outbox/ 에 HTML 파일로 저장한다. 반환: 결과 설명."""
    if settings.mail_backend == "console":
        os.makedirs("outbox", exist_ok=True)
        path = os.path.join("outbox", f"{datetime.now():%Y%m%d-%H%M%S}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"[mail:console] to={to} subject={subject} -> {path}")
        return f"console 모드: {path} 에 저장됨"

    if not settings.gmail_address or not settings.gmail_app_password:
        raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD 가 설정되지 않았습니다.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = to
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(settings.gmail_address, settings.gmail_app_password.replace(" ", ""))
        s.send_message(msg)
    return f"{to} 로 발송 완료"
