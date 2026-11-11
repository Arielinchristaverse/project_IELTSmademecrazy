"""하루치 준비 · 오늘 화면/메일 내용 · 사용자별 알림 발송 (스케줄러와 테스트 발송이 공유)."""
import secrets
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import ONBOARDING_DONE, ActionToken, Assignment, CalendarDay, NotifyLog, Sentence, User
from .allocator import (
    auto_miss_past,
    day_capacity,
    ensure_week,
    generate_week,
    get_calendar_day,
    is_weekend_day,
    pending_items,
    week_start_of,
)
from . import push
from .mailer import send_mail
from .progress import week_progress

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates" / "email")),
    autoescape=select_autoescape(["html"]),
)

WEEKDAYS_KO = "월화수목금토일"
RETRY_MINUTES = 10
MAX_ATTEMPTS = 3


def _greeting(d: date, cal: CalendarDay | None) -> str:
    label = cal.shift.label if cal and cal.shift else ""
    base = f"좋은 하루예요. {d.month}월 {d.day}일({WEEKDAYS_KO[d.weekday()]})"
    if label:
        base += f" · {label} 근무일"
    return base + " 오늘의 공부입니다."


def _sentence_for(db: Session, user: User, advance: bool) -> Sentence | None:
    sentences = db.scalars(
        select(Sentence).where(Sentence.user_id == user.id).order_by(Sentence.position, Sentence.id)
    ).all()
    if not sentences:
        return None
    s = sentences[user.sentence_cursor % len(sentences)]
    if advance:
        user.sentence_cursor += 1
        db.commit()
    return s


def day_assignments(db: Session, uid: int, d: date) -> list[Assignment]:
    return db.scalars(
        select(Assignment)
        .where(Assignment.user_id == uid, Assignment.date == d)
        .order_by(Assignment.position, Assignment.id)
    ).all()


def prepare_day(db: Session, user: User, d: date) -> list[Assignment]:
    """알림/오늘 화면 직전 처리: 지난 배정 자동 미달성 → 주간 배정 확인 →
    주말(오프/일요일)이면 보충 재배정. 재배정은 하루 1회만 (화면을 열 때마다 바뀌지 않도록)."""
    auto_miss_past(db, user.id, d)
    ensure_week(db, user.id, d, d)
    if user.last_prepared_date != d:
        cal = get_calendar_day(db, user.id, d)
        if is_weekend_day(d, cal):
            generate_week(db, user.id, week_start_of(d), d)
        user.last_prepared_date = d
        db.commit()
    return day_assignments(db, user.id, d)


def build_today_context(
    db: Session, user: User, d: date, assignments: list[Assignment] | None = None, advance_sentence: bool = False
) -> dict:
    """오늘 화면과 메일이 공통으로 쓰는 내용."""
    if assignments is None:
        assignments = day_assignments(db, user.id, d)
    cal = get_calendar_day(db, user.id, d)
    return {
        "date": d,
        "greeting": _greeting(d, cal),
        "assignments": assignments,
        "total_min": sum(a.task.duration_min for a in assignments),
        "open_count": sum(1 for a in assignments if a.status == "scheduled"),
        "progress": week_progress(db, user.id, week_start_of(d)),
        "pending": pending_items(db, user.id),
        "sentence": _sentence_for(db, user, advance_sentence),
    }


def build_mail(db: Session, user: User, d: date, assignments: list[Assignment], advance_sentence: bool):
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

    ctx = build_today_context(db, user, d, assignments, advance_sentence)
    ctx.update(items=items, base_url=settings.base_url)
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


def short_summary(assignments: list[Assignment]) -> str:
    """푸시용 짧은 문구. 예: '오늘의 공부 과제 3개 · 총 90분'"""
    total = sum(a.task.duration_min for a in assignments)
    return f"오늘의 공부 과제 {len(assignments)}개 · 총 {total}분"


# ---------------------------------------------------------------------------
# 알림 채널
# ---------------------------------------------------------------------------
class ChannelResult:
    """채널 발송 결과. skipped=True 는 보낼 대상이 없어 건너뜀(예: 등록된 기기 없음)."""

    def __init__(self, ok: bool, detail: str = "", skipped: bool = False):
        self.ok, self.detail, self.skipped = ok, detail, skipped


def _send_email(db: Session, user: User, d: date, assignments: list[Assignment], test: bool) -> ChannelResult:
    subject, html, text = build_mail(db, user, d, assignments, advance_sentence=False)
    return ChannelResult(True, send_mail(user.mail_to, ("[테스트] " if test else "") + subject, html, text, owner_id=user.id))


def _send_push(db: Session, user: User, d: date, assignments: list[Assignment], test: bool) -> ChannelResult:
    """짧은 문구만 보내고, 자세한 내용은 알림을 눌러 연 /today 화면에서 보여준다."""
    data = push.payload(
        "Study Shift" + (" (테스트)" if test else ""),
        short_summary(assignments) if assignments else "오늘 배정된 과제가 없습니다.",
        url="/today",
        tag=f"daily-{d.isoformat()}",
    )
    r = push.send_to_user(db, user, data)
    if r.total == 0:
        return ChannelResult(False, "알림을 받을 기기가 없습니다. 설정에서 '알림 켜기'를 해 주세요.", skipped=True)
    detail = f"{r.sent}대 기기에 보냄" + (f", 만료된 기기 {r.removed}대 정리" if r.removed else "")
    if r.sent:
        return ChannelResult(True, detail)
    if not r.errors:  # 모두 만료되어 삭제됨 → 보낼 곳 없음
        return ChannelResult(False, detail + " — 다시 '알림 켜기'가 필요합니다.", skipped=True)
    return ChannelResult(False, "푸시 실패: " + "; ".join(r.errors[:3]))


# 채널 이름 → 발송 함수
CHANNELS: dict[str, Callable[[Session, User, date, list[Assignment], bool], ChannelResult]] = {
    "email": _send_email,
    "push": _send_push,
}


def user_channels(user: User) -> list[str]:
    wanted = []
    if user.wants_push:
        wanted.append("push")
    if user.wants_email:
        wanted.append("email")
    return [c for c in wanted if c in settings.notify_channels and c in CHANNELS]


def send_test_push(db: Session, user: User, d: date) -> ChannelResult:
    return _send_push(db, user, d, day_assignments(db, user.id, d), test=True)


def send_test_mail(db: Session, user: User, d: date) -> str:
    assignments = prepare_day(db, user, d)
    return _send_email(db, user, d, assignments, test=True).detail


def notify_due(db: Session, user: User, now: datetime) -> list[str]:
    """한 사용자: 오늘 발송 시각이 지났으면 채널별로 하루 1회 발송 (실패 시 10분 간격 최대 3회)."""
    d = now.date()
    cal = get_calendar_day(db, user.id, d)
    if day_capacity(cal) <= 0:
        return []  # 제외일 / 근무 미입력 / 0분 → 발송 안 함
    if now.time() < cal.shift.send_time:
        return []

    channels = user_channels(user)
    logs = {
        log.channel: log
        for log in db.scalars(select(NotifyLog).where(NotifyLog.user_id == user.id, NotifyLog.date == d))
    }
    due = []
    for ch in channels:
        log = logs.get(ch)
        if log and (log.status in ("sent", "skipped") or log.attempts >= MAX_ATTEMPTS):
            continue
        if log and log.status == "failed" and now - log.sent_at < timedelta(minutes=RETRY_MINUTES):
            continue
        due.append(ch)
    if not due:
        return []

    assignments = prepare_day(db, user, d)
    if not assignments:
        return []
    first_today = not logs
    results = []
    for ch in due:
        log = logs.get(ch)
        if log is None:
            log = NotifyLog(user_id=user.id, date=d, channel=ch, sent_at=now, status="pending", attempts=0)
            db.add(log)
        log.attempts += 1
        log.sent_at = now
        try:
            r = CHANNELS[ch](db, user, d, assignments, False)
            log.status = "skipped" if r.skipped else ("sent" if r.ok else "failed")
            log.error = "" if r.ok else r.detail
            results.append(f"{ch}: {r.detail}")
        except Exception as e:  # noqa: BLE001
            log.status, log.error = "failed", str(e)
            results.append(f"{ch}: 발송 실패: {e}")
        db.commit()
    # 영어 문장은 하루에 한 번만 다음 문장으로 넘긴다 (채널이 여러 개여도)
    if first_today:
        _sentence_for(db, user, advance=True)
    return results


def run_due(db: Session, now: datetime) -> list[str]:
    """스케줄러에서 1분마다 호출: 온보딩을 마친 모든 사용자를 순회한다."""
    out = []
    users = db.scalars(select(User).where(User.onboarding_step >= ONBOARDING_DONE).order_by(User.id)).all()
    for user in users:
        try:
            for r in notify_due(db, user, now):
                out.append(f"user={user.id} {r}")
        except Exception as e:  # noqa: BLE001 — 한 사용자의 오류가 다른 사용자 발송을 막지 않게
            db.rollback()
            out.append(f"user={user.id} 오류: {e}")
    return out
