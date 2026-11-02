import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from .config import settings
from .templating import templates

PUBLIC_PREFIXES = ("/a/", "/static/", "/login", "/healthz")

DEFAULT_PW_ERROR = "공개 서버에서는 기본 비밀번호(change-me)를 쓸 수 없습니다. 호스팅 설정에서 APP_PASSWORD 를 바꿔 주세요."

router = APIRouter()


def is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PREFIXES)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    if settings.is_production and settings.password_is_default:
        return templates.TemplateResponse(request, "login.html", {"error": DEFAULT_PW_ERROR}, status_code=403)
    if hmac.compare_digest(password, settings.app_password):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": "비밀번호가 올바르지 않습니다."}, status_code=401)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/healthz")
def healthz():
    return {"ok": True}
