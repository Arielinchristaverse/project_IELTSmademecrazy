"""웹 푸시 구독 관리와 테스트 알림."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..db import get_db
from ..deps import current_user, owned
from ..models import MAX_PUSH_DEVICES, PushSubscription, User
from ..security import MESSAGES, TEST_PUSH_PER_DAY, limit
from ..services import counters
from ..services.daily import send_test_push

router = APIRouter(prefix="/push")


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=255)
    auth: str = Field(min_length=8, max_length=255)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2048)
    keys: Keys


class EndpointIn(BaseModel):
    endpoint: str = Field(max_length=2048)


def _require_json(request: Request) -> None:
    # 다른 사이트의 폼 전송으로 구독을 바꾸지 못하게 JSON 요청만 받는다 (CORS 사전 요청 필요)
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(status_code=415, detail="JSON only")


@router.get("/key")
def public_key(_: User = Depends(current_user)):
    return {"publicKey": settings.vapid_public_key, "ready": settings.push_ready}


@router.post("/subscribe", dependencies=[Depends(limit("subscribe"))])
def subscribe(
    body: SubscriptionIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    _require_json(request)
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=400, detail="invalid endpoint")
    sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(endpoint=body.endpoint, created_at=clock.now())
        db.add(sub)
    # 같은 기기에서 다른 계정으로 로그인한 경우 → 지금 계정으로 옮긴다
    sub.user_id = user.id
    sub.p256dh = body.keys.p256dh
    sub.auth = body.keys.auth
    sub.user_agent = request.headers.get("user-agent", "")[:512]
    sub.last_error = ""
    db.commit()
    # 기기 수 상한: 넘으면 가장 오래 쓰지 않은(최근 성공이 가장 오래된) 기기부터 지운다
    subs = db.scalars(
        select(PushSubscription).where(PushSubscription.user_id == user.id)
        .order_by(PushSubscription.last_success_at.is_(None).desc(), PushSubscription.last_success_at, PushSubscription.created_at)
    ).all()
    for old in [x for x in subs if x.id != sub.id][: max(len(subs) - MAX_PUSH_DEVICES, 0)]:
        db.delete(old)
    db.commit()
    return {"ok": True, "id": sub.id}


@router.post("/status", dependencies=[Depends(limit("subscribe"))])
def status(body: EndpointIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """이 기기 구독을 서버가 알고 있는지 (내 계정 구독만 확인). 앱 상단 알림 배너용."""
    _require_json(request)
    known = db.scalar(
        select(PushSubscription.id).where(
            PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id
        )
    )
    return {"known": known is not None}


@router.post("/unsubscribe")
def unsubscribe(
    body: EndpointIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    _require_json(request)
    sub = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id
        )
    )
    if sub:
        db.delete(sub)
        db.commit()
    return {"ok": True}


@router.post("/devices/{sub_id}/delete")
def delete_device(sub_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.delete(owned(db, PushSubscription, sub_id, user))
    db.commit()
    return RedirectResponse("/settings#push", status_code=303)


@router.post("/test")
def test_push(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not settings.push_ready:
        return JSONResponse({"ok": False, "message": "서버에 VAPID 키가 설정되지 않았습니다."}, status_code=503)
    key = f"test_push:{user.id}"  # 하루 횟수 (서비스 전체 하루 집계 표, 30일 뒤 정리)
    if counters.get(db, key) >= TEST_PUSH_PER_DAY:
        return JSONResponse({"ok": False, "message": MESSAGES["test_push"]}, status_code=429)
    counters.bump(db, key)
    r = send_test_push(db, user, clock.today())
    return {"ok": r.ok, "message": r.detail}
