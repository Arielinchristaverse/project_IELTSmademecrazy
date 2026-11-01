from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import AppSettings, Sentence
from ..templating import templates

router = APIRouter()


@router.get("/sentences")
def sentences_page(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    rows = db.scalars(select(Sentence).order_by(Sentence.position, Sentence.id)).all()
    text = "\n".join(f"{s.text} | {s.meaning}" if s.meaning else s.text for s in rows)
    st = db.get(AppSettings, 1)
    nxt = (st.sentence_cursor % len(rows)) + 1 if rows else 0
    return templates.TemplateResponse(
        request, "sentences.html", {"text": text, "count": len(rows), "next_no": nxt, "saved": saved}
    )


@router.post("/sentences")
def save_sentences(body: str = Form(""), db: Session = Depends(get_db)):
    db.execute(delete(Sentence))
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        text, _, meaning = ln.partition("|")
        db.add(Sentence(text=text.strip(), meaning=meaning.strip(), position=i))
    db.commit()
    return RedirectResponse("/sentences?saved=1", status_code=303)
