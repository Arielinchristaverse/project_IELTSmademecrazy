from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import current_user
from ..models import MAX_SENTENCES, SENTENCE_MAX, Sentence, User
from ..templating import templates

router = APIRouter()


@router.get("/sentences")
def sentences_page(
    request: Request, saved: int = 0, error: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)
):
    rows = db.scalars(
        select(Sentence).where(Sentence.user_id == user.id).order_by(Sentence.position, Sentence.id)
    ).all()
    text = "\n".join(f"{s.text} | {s.meaning}" if s.meaning else s.text for s in rows)
    nxt = (user.sentence_cursor % len(rows)) + 1 if rows else 0
    return templates.TemplateResponse(
        request, "sentences.html", {"text": text, "count": len(rows), "next_no": nxt, "saved": saved, "error": error,
         "max_sentences": MAX_SENTENCES, "sentence_max": SENTENCE_MAX}
    )


@router.post("/sentences")
def save_sentences(body: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) > MAX_SENTENCES:
        return RedirectResponse("/sentences?error=count", status_code=303)
    for ln in lines:
        text, _, meaning = ln.partition("|")
        if len(text.strip()) > SENTENCE_MAX or len(meaning.strip()) > SENTENCE_MAX:
            return RedirectResponse("/sentences?error=length", status_code=303)
    db.execute(delete(Sentence).where(Sentence.user_id == user.id))
    for i, ln in enumerate(lines):
        text, _, meaning = ln.partition("|")
        db.add(Sentence(user_id=user.id, text=text.strip(), meaning=meaning.strip(), position=i))
    db.commit()
    return RedirectResponse("/sentences?saved=1", status_code=303)
