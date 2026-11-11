"""웹 푸시 발송 (pywebpush + VAPID).

- 기기(구독)마다 따로 보낸다. 하나라도 성공하면 그 사용자에 대한 발송은 성공으로 본다
  (재시도 때 이미 받은 기기에 또 가지 않도록).
- 푸시 서비스가 404/410 을 돌려주면 그 구독은 만료된 것이므로 삭제한다.
- 그 밖의 오류는 실패로 기록하고, 알림 스케줄러가 10분 간격 최대 3회 재시도한다.
"""
import json
from dataclasses import dataclass, field

from pywebpush import WebPushException, webpush
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import PushSubscription, User

GONE = (404, 410)
TTL_SECONDS = 12 * 60 * 60  # 기기가 꺼져 있어도 12시간까지는 전달 시도


@dataclass
class PushReport:
    sent: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.sent + self.removed + len(self.errors)


def payload(title: str, body: str, url: str = "/today", tag: str = "daily") -> str:
    return json.dumps({"title": title, "body": body, "url": url, "tag": tag}, ensure_ascii=False)


def _status(e: WebPushException) -> int | None:
    resp = getattr(e, "response", None)
    return getattr(resp, "status_code", None) or getattr(resp, "status", None)


def send_to_user(db: Session, user: User, data: str) -> PushReport:
    if not settings.push_ready:
        raise RuntimeError("VAPID 키가 설정되지 않았습니다.")
    report = PushReport()
    subs = db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=data,
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},  # 기기마다 새 dict (aud 가 기기마다 다름)
                ttl=TTL_SECONDS,
                headers={"Urgency": "high"},
                timeout=15,
            )
            sub.last_success_at = clock.now()
            sub.last_error = ""
            report.sent += 1
        except WebPushException as e:
            code = _status(e)
            if code in GONE:
                db.delete(sub)
                report.removed += 1
            else:
                sub.last_error = f"{code or ''} {e.message}".strip()[:500]
                report.errors.append(sub.last_error)
        except Exception as e:  # noqa: BLE001 — 네트워크 오류 등
            sub.last_error = str(e)[:500]
            report.errors.append(sub.last_error)
    db.commit()
    return report
