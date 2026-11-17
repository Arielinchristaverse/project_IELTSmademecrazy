import hashlib
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
templates.env.globals["WEEKDAYS_KO"] = "월화수목금토일"


@lru_cache(maxsize=None)
def _file_version(path: str) -> str:
    try:
        return hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def asset(path: str) -> str:
    """정적 파일 주소에 내용 기반 버전을 붙인다 (예: /static/style.css?v=3f2a…).
    파일이 바뀌면 주소가 바뀌므로, 브라우저(특히 iOS Safari)가 예전 파일을 계속 쓰지 않는다."""
    return f"/static/{path}?v={_file_version(path)}"


templates.env.globals["asset"] = asset
