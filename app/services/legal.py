"""이용약관 · 개인정보처리방침 문서 (content/*.md).

- 본문은 운영자가 content/terms.md, content/privacy.md 파일만 바꿔서 교체한다.
- 파일 맨 위의 머리 정보(--- 사이)에서 제목·버전·시행일을 읽는다. 버전이 바뀌면 모든 사용자가 다시 동의한다.
- Markdown 은 원문 HTML 을 허용하지 않고(html=False), javascript: 같은 위험한 링크는 걸러지는 설정으로 렌더링한다.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from markdown_it import MarkdownIt

CONTENT = Path(__file__).resolve().parent.parent.parent / "content"
CONTACT_EMAIL = "arielinafterwork@gmail.com"
_md = MarkdownIt("commonmark", {"html": False}).enable("table")


@dataclass(frozen=True)
class Doc:
    key: str
    title: str
    version: str
    effective_date: str
    html: str


def _parse(text: str) -> tuple[dict[str, str], str]:
    meta: dict[str, str] = {}
    if text.startswith("---"):
        head, _, body = text[3:].partition("\n---")
        for line in head.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip("'\"")
        text = body.lstrip("-").lstrip("\n")
    return meta, text


def _split_title(body: str) -> tuple[str, str]:
    """머리 정보에 title 이 없으면 본문 맨 앞의 '# 제목' 을 제목으로 쓰고 본문에서 뺀다 (제목이 두 번 나오지 않게)."""
    lines = body.lstrip("\n").split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:])
    return "", body


@lru_cache(maxsize=None)
def load(key: str) -> Doc:
    meta, body = _parse((CONTENT / f"{key}.md").read_text(encoding="utf-8"))
    if not meta.get("title"):
        meta["title"], body = _split_title(body)
    version = meta.get("version", "")
    if not version or len(version) > 20:
        raise RuntimeError(f"content/{key}.md 머리 정보에 version(20자 이내)이 필요합니다.")
    return Doc(key, meta.get("title", key), version, meta.get("effective_date", ""), _md.render(body))


def terms() -> Doc:
    return load("terms")


def privacy() -> Doc:
    return load("privacy")
