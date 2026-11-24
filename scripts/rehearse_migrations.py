"""운영 DB 백업본으로 마이그레이션 리허설.

    python -m scripts.rehearse_migrations <백업.db>

1. 백업을 임시 폴더로 복사한다 (원본은 건드리지 않음).
2. 모든 테이블의 지문(행 수 + 열별 내용 해시)을 기록한다.
3. 현재 버전부터 최신까지 '한 단계씩' 적용하면서, 단계마다
   - 기존 테이블·열 중 바뀐 것 (예상된 변경은 EXPECTED 로 표시)
   - 외래키 검사, 무결성 검사
   를 출력한다.
4. 최신 구조로 앱을 띄워 사용자별 주요 화면이 열리는지 확인한다 (사용자는 번호로만 표시).
5. 임시 폴더를 지운다 (--keep 이면 남김).

출력에는 이메일·이름 등 개인정보와 비밀값을 넣지 않는다.
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 리비전별로 '바뀌는 것이 정상'인 기존 열 (table.column)
EXPECTED = {
    "0007": {"users.notify_channel", "users.notify_email"},
}
PAGES = ["/today", "/", "/calendar", "/settings", "/tasks", "/sentences", "/account"]


def fingerprint(path: str) -> dict[str, dict]:
    con = sqlite3.connect(path)
    out = {}
    tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")]
    for t in sorted(tables):
        cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
        rows = con.execute(f'select * from "{t}" order by rowid').fetchall()
        col_hash = {c: hashlib.sha256(repr([r[i] for r in rows]).encode()).hexdigest()[:12] for i, c in enumerate(cols)}
        out[t] = {"rows": len(rows), "cols": col_hash}
    con.close()
    return out


def checks(path: str) -> tuple[int, str]:
    con = sqlite3.connect(path)
    fk = con.execute("pragma foreign_key_check").fetchall()
    ok = con.execute("pragma integrity_check").fetchone()[0]
    con.close()
    return len(fk), ok


def compare(before: dict, after: dict, rev: str) -> list[str]:
    lines = []
    for t, b in before.items():
        if t == "alembic_version":
            continue  # 버전 번호는 단계마다 바뀌는 것이 정상
        a = after.get(t)
        if a is None:
            lines.append(f"  - 테이블 삭제: {t}")
            continue
        if a["rows"] != b["rows"]:
            lines.append(f"  ! 행 수 변화: {t} {b['rows']} → {a['rows']}")
        for c, h in b["cols"].items():
            if c not in a["cols"]:
                lines.append(f"  - 열 삭제: {t}.{c}")
            elif a["cols"][c] != h:
                tag = "예상됨" if f"{t}.{c}" in EXPECTED.get(rev, set()) else "확인 필요"
                lines.append(f"  {'·' if tag == '예상됨' else '!'} 내용 변화({tag}): {t}.{c}")
    for t in after.keys() - before.keys():
        lines.append(f"  + 새 테이블: {t} ({after[t]['rows']}행)")
    return lines


def main(src: str, keep: bool = False) -> int:
    work = tempfile.mkdtemp(prefix="rehearsal-")
    db_path = os.path.join(work, "app.db")
    shutil.copy2(src, db_path)
    url = f"sqlite:///{db_path}"
    os.environ.update(DATABASE_URL=url, DEV_LOGIN="true", SCHEDULER_ENABLED="false")
    os.environ.pop("RENDER", None)
    sys.path.insert(0, str(ROOT))
    from alembic import command
    from alembic.script import ScriptDirectory

    from scripts import migrate

    cfg = migrate.alembic_config(url)
    script = ScriptDirectory.from_config(cfg)
    con = sqlite3.connect(db_path)
    current = con.execute("select version_num from alembic_version").fetchone()[0]
    con.close()
    revs = [r.revision for r in reversed(list(script.walk_revisions()))]
    todo = revs[revs.index(current) + 1:]
    print(f"현재 {current} → 적용할 단계: {', '.join(todo) or '없음'}")
    problems = 0
    for rev in todo:
        before = fingerprint(db_path)
        command.upgrade(cfg, rev)
        after = fingerprint(db_path)
        fk, integrity = checks(db_path)
        diff = compare(before, after, rev)
        print(f"[{rev}] 외래키 문제 {fk}건 · 무결성 {integrity}")
        print("\n".join(diff) if diff else "  (기존 데이터 변화 없음)")
        problems += fk + (integrity != "ok") + sum(1 for line in diff if line.lstrip().startswith(("!", "-")))

    from fastapi.testclient import TestClient

    from app.main import app

    con = sqlite3.connect(db_path)
    users = con.execute("select id, email from users order by id").fetchall()
    con.close()
    with TestClient(app) as c:
        for uid, email in users:
            c.post("/logout")
            c.post("/auth/dev", data={"email": email})
            codes = {p: c.get(p).status_code for p in PAGES}
            bad = {p: s for p, s in codes.items() if s != 200}
            problems += len(bad)
            print(f"user{uid}: 화면 {len(PAGES) - len(bad)}/{len(PAGES)} 정상" + (f" 문제 {bad}" if bad else ""))
        print("healthz:", c.get("/healthz").json())
    if keep:
        print("사본 보관:", work)
    else:
        shutil.rmtree(work)
    print("결과:", "문제 없음" if problems == 0 else f"확인할 항목 {problems}개")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--keep"]
    if len(args) != 1:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(args[0], keep="--keep" in sys.argv))
