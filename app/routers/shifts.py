"""근무 유형 추가 · 수정 · 보관 · 삭제 화면 (설정과 온보딩에서 함께 쓴다)."""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user, owned, safe_back
from ..models import MAX_ACTIVE_SHIFTS, SHIFT_COLORS, ShiftType, User
from ..services import shifts as svc
from ..templating import templates

router = APIRouter(prefix="/settings/shifts")


def _back(value: str | None) -> str:
    return safe_back(value, "/settings")


def _with_msg(url: str, msg: str) -> str:
    base = url.split("#")[0]
    return f"{base}{'&' if '?' in base else '?'}msg={quote(msg)}#shifts"


def _form_page(request: Request, shift: ShiftType | None, back: str, error: str | None = None, values=None, **extra):
    return templates.TemplateResponse(
        request,
        "settings_shift_form.html",
        {
            "shift": shift,
            "back": back,
            "error": error,
            "values": values,
            "colors": SHIFT_COLORS,
            "onboarding": back.startswith("/onboarding"),
            **extra,
        },
        status_code=400 if error else 200,
    )


@router.get("/new")
def new_page(request: Request, back: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    full = svc.active_count(db, user.id) >= MAX_ACTIVE_SHIFTS
    return _form_page(request, None, _back(back), full=full)


@router.post("/new")
async def create(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    form = await request.form()
    back = _back(form.get("back"))
    try:
        shift = svc.create_shift(db, user.id, svc.parse_form(form))
    except svc.ShiftError as e:
        return _form_page(request, None, back, str(e), values=form)
    return RedirectResponse(_with_msg(back, f"'{shift.label}' 유형을 추가했어요."), status_code=303)


@router.get("/{shift_id}")
def edit_page(
    shift_id: int, request: Request, back: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)
):
    shift = owned(db, ShiftType, shift_id, user)
    return _form_page(request, shift, _back(back), used=svc.is_used(db, shift))


@router.post("/{shift_id}")
async def update(shift_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    shift = owned(db, ShiftType, shift_id, user)
    form = await request.form()
    back = _back(form.get("back"))
    try:
        recalculated = svc.update_shift(db, shift, svc.parse_form(form))
    except svc.ShiftError as e:
        db.rollback()
        return _form_page(request, shift, back, str(e), values=form, used=svc.is_used(db, shift))
    msg = f"'{shift.label}' 유형을 저장했어요." + (" 내일부터 배정을 다시 계산했습니다." if recalculated else "")
    return RedirectResponse(_with_msg(back, msg), status_code=303)


@router.post("/{shift_id}/archive")
async def archive(shift_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    shift = owned(db, ShiftType, shift_id, user)
    back = _back((await request.form()).get("back"))
    svc.archive_shift(db, shift)
    return RedirectResponse(_with_msg(back, f"'{shift.label}' 유형을 보관했어요. 이미 입력된 날은 그대로 유지됩니다."), 303)


@router.post("/{shift_id}/unarchive")
async def unarchive(shift_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    shift = owned(db, ShiftType, shift_id, user)
    back = _back((await request.form()).get("back"))
    try:
        svc.unarchive_shift(db, shift)
        msg = f"'{shift.label}' 유형을 다시 쓸 수 있어요."
    except svc.ShiftError as e:
        msg = str(e)
    return RedirectResponse(_with_msg(back, msg), status_code=303)


@router.post("/{shift_id}/delete")
async def delete(shift_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    shift = owned(db, ShiftType, shift_id, user)
    back = _back((await request.form()).get("back"))
    label = shift.label
    try:
        svc.delete_shift(db, shift)
        msg = f"'{label}' 유형을 삭제했어요."
    except svc.ShiftError as e:
        msg = str(e)
    return RedirectResponse(_with_msg(back, msg), status_code=303)
