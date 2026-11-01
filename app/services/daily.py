"""하루치 메일 준비/발송 파이프라인 (스케줄러와 테스트 메일이 공유)."""
import secrets
from pathlib import Path
from datetime import date, datetime, timedelta

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import ActionToken, AppSettings, Assignment, CalendarDay, MailLog, Sentence
from .allocator import (
    auto_miss_past,
    day_capacity,
    ensure_week,
    generate_week,
    is_weekend_day,
    pending_items,
    week_start_of,
)
from .mailer import send_mail
from .progress import week_progress

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates" / "email")),
    autoescape=select_autoescape(["html"]),
)

WEEKDAYS_KO = "월화수목금토일"


def _greeting(d: date, cal: CalendarDay | None) -> str:
    label = cal.shift.label if cal and cal.shift else ""
    base = f"좋은 하루예요. {d.month}월 {d.day}일({WEEKDAYS_KO[d.weekday()]})"
    if label:
        base += f" · {label} 근무일"
    return base + " 오늘의 공부입니다."


def _pick_sentence(db: Session, advance: bool) -> Sentence | None:
    sentences = db.scalars(select(Sentence).order_by(Sentence.position, Sentence.id)).all()
    if not sentences:
        return None
    st = db.get(AppSettings, 1)
    s = sentences[st.sentence_cursor % len(sentences)]
    if advance:
        st.sentence_cursor += 1
        db.commit()
    return s


def prepare_day(db: Session, d: date) -> list[Assignment]:
    """메일 직전 처리: 지난 배정 자동 미달성 → 주간 배정 확인 → 주말이면 보충 재배정."""
    auto_miss_past(db, d)
    ensure_week(db, d, d)
    cal = db.get(CalendarDay, d)
    if is_weekend_day(d, cal):
        generate_week(db, week_start_of(d), d)
    return db.scalars(
        select(Assignment).where(Assignment.date == d).order_by(Assignment.position, Assignment.id)
    ).all()


def build_mail(db: Session, d: date, assignments: list[Assignment], advance_sentence: bool):
    cal = db.get(CalendarDay, d)
    expires = clock.now() + timedelta(days=settings.token_valid_days)
    items = []
    for a in assignments:
        links = {}
        if a.status == "scheduled":
            for action in ("done", "missed"):
                tok = ActionToken(
                    token=secrets.token_urlsafe(32), assignment_id=a.id, action=action, expires_at=expires
                )
                db.add(tok)
                links[action] = f"{settings.base_url}/a/{tok.token}"
        items.append({"a": a, "links": links})
    db.commit()

    pend = pending_items(db)
    ctx = {
        "greeting": _greeting(d, cal),
        "items": items,
        "total_min": sum(a.task.duration_min for a in assignments),
        "progress": week_progress(db, week_start_of(d)),
        "pending": pend,
        "sentence": _pick_sentence(db, advance_sentence),
        "base_url": settings.base_url,
    }
    html = _env.get_template("daily.html").render(**ctx)
    text_lines = [ctx["greeting"], ""]
    for it in items:
        text_lines.append(f"- {it['a'].task.name} ({it['a'].task.duration_min}분)")
        for action, label in (("done", "달성"), ("missed", "미달성")):
            if action in it["links"]:
                text_lines.append(f"  {label}: {it['links'][action]}")
    text_lines.append(f"\n이번 주 진척도: {ctx['progress']}%")
    subject = f"[오늘의 공부] {d.month}/{d.day}({WEEKDAYS_KO[d.weekday()]}) 과제 {len(items)}개"
    return subject, html, "\n".join(text_lines)


def recipient(db: Session) -> str:
    st = db.get(AppSettings, 1)
    return (st.recipient_email if st and st.recipient_email else settings.gmail_address) or "me@localhost"


def send_test_mail(db: Session, d: date) -> str:
    assignments = prepare_day(db, d)
    subject, html, text = build_mail(db, d, assignments, advance_sentence=False)
    return send_mail(recipient(db), "[테스트] " + subject, html, text)


def send_daily_if_due(db: Session, now: datetime) -> str | None:
    """스케줄러에서 1분마다 호출. 발송 시각이 지났고 아직 안 보냈으면 발송."""
    d = now.date()
    log = db.scalar(select(MailLog).where(MailLog.date == d))
    if log and (log.status == "sent" or log.attempts >= 3):
        return None
    if log and log.status == "failed" and now - log.sent_at < timedelta(minutes=10):
        return None

    cal = db.get(CalendarDay, d)
    if day_capacity(cal) <= 0:
        return None  # 제외일 / 근무 미입력 / 0분 → 발송 안 함
    if now.time() < cal.shift.send_time:
        return None

    assignments = prepare_day(db, d)
    if not assignments:
        return None
    if log is None:
        log = MailLog(date=d, sent_at=now, status="pending", attempts=0)
        db.add(log)
    log.attempts += 1
    log.sent_at = now
    try:
        subject, html, text = build_mail(db, d, assignments, advance_sentence=True)
        result = send_mail(recipient(db), subject, html, text)
        log.status, log.error = "sent", ""
    except Exception as e:  # noqa: BLE001
        log.status, log.error = "failed", str(e)
        result = f"발송 실패: {e}"
    db.commit()
    return result
