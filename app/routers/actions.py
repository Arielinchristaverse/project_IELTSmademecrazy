from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..models import ActionToken
from ..services.allocator import pending_items
from ..services.progress import set_status, week_progress
from ..templating import templates

router = APIRouter()

LABEL = {"done": "달성", "missed": "미달성"}


@router.get("/a/{token}")
def record_action(token: str, request: Request, db: Session = Depends(get_db)):
    tok = db.get(ActionToken, token)
    ctx: dict = {"title": "", "message": "", "progress": None, "task": None}
    status_code = 200
    if tok is None:
        ctx.update(title="유효하지 않은 링크", message="링크가 잘못되었거나 배정이 다시 생성되었습니다.")
        status_code = 404
    else:
        a = tok.assignment
        ctx["task"] = a.task
        if tok.used_at or a.status != "scheduled":
            ctx.update(title="이미 기록됨", message=f"이 과제는 이미 '{LABEL.get(a.status, a.status)}'(으)로 기록되어 있습니다.")
        elif tok.expires_at < clock.now():
            ctx.update(title="만료된 링크", message="웹 화면의 주간 보기에서 기록해 주세요.")
        else:
            tok.used_at = clock.now()
            set_status(db, a, tok.action)
            if tok.action == "done":
                ctx.update(title="달성 기록 완료", message="수고했어요. 진척도에 반영했습니다.")
            else:
                ctx.update(title="미달성 기록 완료", message="보류 목록에 추가했습니다. 주말(오프/일요일)에 다시 배정됩니다.")
        ctx["progress"] = week_progress(db, a.user_id, a.week_start)
        ctx["pending_count"] = len(pending_items(db, a.user_id))
    return templates.TemplateResponse(request, "action_result.html", ctx, status_code=status_code)
