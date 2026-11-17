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
from ..models import (
    NOTIFY_DAILY,
    NOTIFY_REMIND,
    ONBOARDING_DONE,
    ActionToken,
    Assignment,
    CalendarDay,
    NotifyLog,
    Sentence,
    User,
)
from .allocator import (
    auto_miss_past,
    day_capacity,
    ensure_week,
    generate_week,
    get_calendar_day,
    is_rest_day,
    pending_items,
    week_start_of,
)
from . import notify_text as T
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


def _sentence_for(db: Session, user: User) -> Sentence | None:
    sentences = db.scalars(
        select(Sentence).where(Sentence.user_id == user.id).order_by(Sentence.position, Sentence.id)
    ).all()
    if not sentences:
        return None
    s = sentences[user.sentence_cursor % len(sentences)]
    return s


def day_assignments(db: Session, uid: int, d: date) -> list[Assignment]:
    return db.scalars(
        select(Assignment)
        .where(Assignment.user_id == uid, Assignment.date == d)
        .order_by(Assignment.position, Assignment.id)
    ).all()


def prepare_day(db: Session, user: User, d: date) -> list[Assignment]:
    """알림/오늘 화면 직전 처리: 지난 배정 자동 미달성 → 주간 배정 확인 →
    쉬는 날이면 보충 재배정. 재배정은 하루 1회만 (화면을 열 때마다 바뀌지 않도록)."""
    auto_miss_past(db, user.id, d)
    ensure_week(db, user.id, d, d)
    last = user.last_prepared_date
    if last != d:
        cal = get_calendar_day(db, user.id, d)
        if is_rest_day(d, cal):
            generate_week(db, user.id, week_start_of(d), d)
        # 영어 문장은 날짜가 넘어갈 때 한 번만 다음 문장으로 → 그날의 메일·푸시·오늘 화면이 같은 문장
        if last is not None and d > last:
            user.sentence_cursor += 1
        if last is None or d > last:
            user.last_prepared_date = d
        db.commit()
    return day_assignments(db, user.id, d)


def build_today_context(
    db: Session, user: User, d: date, assignments: list[Assignment] | None = None
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
        "sentence": _sentence_for(db, user),
    }


def build_mail(db: Session, user: User, d: date, assignments: list[Assignment], kind: str = NOTIFY_DAILY):
    """메일 내용. 재알림이면 assignments 는 아직 기록하지 않은 과제만 받는다."""
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

    ctx = build_today_context(db, user, d, assignments)
    ctx.update(
        items=items,
        base_url=settings.base_url,
        heading=T.REMIND_MAIL_HEADING if kind == NOTIFY_REMIND else T.DAILY_MAIL_HEADING,
    )
    html = _env.get_template("daily.html").render(**ctx)
    text_lines = [ctx["greeting"], ""]
    for it in items:
        text_lines.append(f"- {it['a'].task.name} ({it['a'].task.duration_min}분)")
        for action, label in (("done", "달성"), ("missed", "미달성")):
            if action in it["links"]:
                text_lines.append(f"  {label}: {it['links'][action]}")
    text_lines.append(f"\n이번 주 진척도: {ctx['progress']}%")
    if kind == NOTIFY_REMIND:
        subject = T.REMIND_MAIL_SUBJECT.format(count=len(items))
    else:
        subject = T.DAILY_MAIL_SUBJECT.format(m=d.month, d=d.day, wd=WEEKDAYS_KO[d.weekday()], count=len(items))
    return subject, html, "\n".join(text_lines)


def push_body(db: Session, user: User, d: date, kind: str, test: bool = False) -> str:
    """푸시 본문: 재알림 묶음 / 쉬는 날 묶음 / 근무한 날 묶음 중에서 날짜·사용자로 고른다.
    쉬는 날 여부는 그날 근무 유형의 '쉬는 날' 체크만으로 정한다(요일과 무관). 테스트 알림은 근무한 날 묶음."""
    if kind == NOTIFY_REMIND:
        phrases = T.REMIND
    elif test:
        phrases = T.WORKDAY_DAILY
    else:
        cal = get_calendar_day(db, user.id, d)
        rest = bool(cal is not None and cal.shift is not None and cal.shift.is_rest_day)
        phrases = T.RESTDAY_DAILY if rest else T.WORKDAY_DAILY
    return T.pick(phrases, d, user.id)


def remaining(assignments: list[Assignment]) -> list[Assignment]:
    """아직 기록하지 않은 과제 (달성·미달성을 누른 과제는 제외)."""
    return [a for a in assignments if a.status == "scheduled"]


# ---------------------------------------------------------------------------
# 알림 채널
# ---------------------------------------------------------------------------
class ChannelResult:
    """채널 발송 결과. skipped=True 는 보낼 대상이 없어 건너뜀(예: 등록된 기기 없음)."""

    def __init__(self, ok: bool, detail: str = "", skipped: bool = False):
        self.ok, self.detail, self.skipped = ok, detail, skipped


def _send_email(
    db: Session, user: User, d: date, assignments: list[Assignment], test: bool, kind: str = NOTIFY_DAILY
) -> ChannelResult:
    subject, html, text = build_mail(db, user, d, assignments, kind)
    return ChannelResult(True, send_mail(user.mail_to, ("[테스트] " if test else "") + subject, html, text, owner_id=user.id))


def _send_push(
    db: Session, user: User, d: date, assignments: list[Assignment], test: bool, kind: str = NOTIFY_DAILY
) -> ChannelResult:
    """브랜드 문구만 보내고(과제 개수·시간 없음), 자세한 내용은 알림을 눌러 연 /today 화면에서 보여준다.
    재알림은 아침 알림을 대체하지 않도록 태그를 따로 쓴다."""
    data = push.payload(T.PUSH_TITLE, push_body(db, user, d, kind, test), url="/today", tag=f"{kind}-{d.isoformat()}")
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
CHANNELS: dict[str, Callable[..., ChannelResult]] = {
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


def _logs(db: Session, user: User, d: date, kind: str) -> dict[str, NotifyLog]:
    return {
        log.channel: log
        for log in db.scalars(
            select(NotifyLog).where(NotifyLog.user_id == user.id, NotifyLog.date == d, NotifyLog.kind == kind)
        )
    }


def _due_channels(channels: list[str], logs: dict[str, NotifyLog], now: datetime) -> list[str]:
    """하루 1회 + 실패 시 10분 간격 최대 3회 재시도 규칙으로, 지금 보낼 수단만 고른다."""
    due = []
    for ch in channels:
        log = logs.get(ch)
        if log and (log.status in ("sent", "skipped") or log.attempts >= MAX_ATTEMPTS):
            continue
        if log and log.status == "failed" and now - log.sent_at < timedelta(minutes=RETRY_MINUTES):
            continue
        due.append(ch)
    return due


def _send(
    db: Session, user: User, d: date, kind: str, due: list[str], logs: dict[str, NotifyLog],
    assignments: list[Assignment], now: datetime,
) -> list[str]:
    results = []
    for ch in due:
        log = logs.get(ch)
        if log is None:
            log = NotifyLog(user_id=user.id, date=d, kind=kind, channel=ch, sent_at=now, status="pending", attempts=0)
            db.add(log)
        log.attempts += 1
        log.sent_at = now
        try:
            r = CHANNELS[ch](db, user, d, assignments, False, kind)
            log.status = "skipped" if r.skipped else ("sent" if r.ok else "failed")
            log.error = "" if r.ok else r.detail
            results.append(f"{ch}: {r.detail}" if kind == NOTIFY_DAILY else f"{ch}(재알림): {r.detail}")
        except Exception as e:  # noqa: BLE001
            log.status, log.error = "failed", str(e)
            results.append(f"{ch}: 발송 실패: {e}")
        db.commit()
    return results


def notify_due(db: Session, user: User, now: datetime) -> list[str]:
    """정기 알림: 오늘 유형의 알림 시각이 지났으면 수단별로 하루 1회 발송 (실패 시 10분 간격 최대 3회)."""
    d = now.date()
    cal = get_calendar_day(db, user.id, d)
    if day_capacity(cal) <= 0:
        return []  # 제외일 / 근무 미입력 / 0분 → 발송 안 함
    if now.time() < cal.shift.send_time:
        return []
    logs = _logs(db, user, d, NOTIFY_DAILY)
    due = _due_channels(user_channels(user), logs, now)
    if not due:
        return []
    assignments = prepare_day(db, user, d)
    if not assignments:
        return []
    return _send(db, user, d, NOTIFY_DAILY, due, logs, assignments, now)


def remind_due(db: Session, user: User, now: datetime) -> list[str]:
    """재알림: 오늘 유형의 재알림 시각이 지났고, 아직 기록하지 않은 과제가 남아 있으면 같은 날 한 번 더 알린다.

    - 재알림 시각이 비어 있는 유형, 제외일·근무 미입력·0분인 날, 배정이 없는 날은 보내지 않는다.
    - 수단별로, 그 수단으로 오늘 정기 알림이 실제로 발송된(sent) 경우에만 보낸다.
    - 날짜는 now 기준이라 자정이 지나면 전날 재알림은 보내지 않는다. 서버 재시작으로 시각을 놓쳐도
      그날 안이면 늦게라도 보낸다. 하루 1회와 재시도 규칙은 정기 알림과 같다.
    """
    d = now.date()
    cal = get_calendar_day(db, user.id, d)
    if day_capacity(cal) <= 0 or cal.shift.remind_time is None:
        return []
    if now.time() < cal.shift.remind_time:
        return []
    daily_sent = {ch for ch, log in _logs(db, user, d, NOTIFY_DAILY).items() if log.status == "sent"}
    logs = _logs(db, user, d, NOTIFY_REMIND)
    due = [ch for ch in _due_channels(user_channels(user), logs, now) if ch in daily_sent]
    if not due:
        return []
    left = remaining(day_assignments(db, user.id, d))
    if not left:
        return []
    return _send(db, user, d, NOTIFY_REMIND, due, logs, left, now)


def run_due(db: Session, now: datetime) -> list[str]:
    """스케줄러에서 1분마다 호출: 온보딩을 마친 모든 사용자를 순회한다."""
    out = []
    users = db.scalars(select(User).where(User.onboarding_step >= ONBOARDING_DONE).order_by(User.id)).all()
    for user in users:
        for step in (notify_due, remind_due):  # 정기 → 재알림 순서
            try:
                for r in step(db, user, now):
                    out.append(f"user={user.id} {r}")
            except Exception as e:  # noqa: BLE001 — 한 사용자의 오류가 다른 사용자 발송을 막지 않게
                db.rollback()
                out.append(f"user={user.id} 오류: {e}")
    return out
