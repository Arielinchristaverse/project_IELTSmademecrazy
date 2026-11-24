"""관리자 화면 (ADMIN_EMAIL 계정만. 다른 사용자에게는 없는 주소처럼 404)."""
import os
import tempfile

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from .. import clock
from ..db import get_db
from ..deps import admin_user
from ..models import User
from ..services import admin_stats
from ..services.backup import backup_to
from ..templating import templates

router = APIRouter(prefix="/admin")


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db), user: User = Depends(admin_user)):
    return templates.TemplateResponse(request, "admin.html", {"user": user, **admin_stats.build(db, clock.today())})


@router.get("/backup")
def download_backup(_: User = Depends(admin_user)):
    """현재 DB의 일관된 사본을 내려받는다."""
    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    backup_to(tmp)
    return FileResponse(
        tmp,
        media_type="application/octet-stream",
        filename=f"afterwork-backup-{clock.now():%Y%m%d-%H%M%S}.db",
        background=BackgroundTask(os.remove, tmp),
    )
