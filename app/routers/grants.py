"""閲覧許可(grants)の管理。adminのみ。キーはサーバー側でカード実データから解決する。"""
import sqlite3
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import normalize
from ..db import get_db
from ..deps import require_admin
from ..templating import templates

router = APIRouter()

KINDS = {"company": "企業", "department": "部署", "person": "人物"}
SEP = "\t"  # キーは空白除去済みなので区切りに使える
MAX_CANDIDATES = 200


def _target_user(db: sqlite3.Connection, uid: int):
    row = db.execute("SELECT id,email,role FROM users WHERE id = ?", (uid,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="利用者が見つかりません")
    return row


def _rep(values: list[str]) -> str:
    """同じキーの表記ゆれから代表表記(最頻、同数なら最初)を選ぶ。"""
    vals = [v for v in values if v]
    return Counter(vals).most_common(1)[0][0] if vals else ""


def _candidates(db: sqlite3.Connection, kind: str, q: str) -> list[dict]:
    out = []
    if kind == "company":
        groups: dict[str, list] = {}
        for r in db.execute("SELECT company, company_key FROM cards WHERE company_key <> '' ORDER BY id"):
            groups.setdefault(r["company_key"], []).append(r["company"])
        for ck, names in groups.items():
            out.append({"value": ck, "label": _rep(names), "search": [_rep(names), ck]})
    elif kind == "department":
        groups = {}
        for r in db.execute(
            "SELECT company, department, company_key, department_key FROM cards "
            "WHERE company_key <> '' AND department_key <> '' ORDER BY id"
        ):
            groups.setdefault((r["company_key"], r["department_key"]), []).append(
                (r["company"], r["department"]))
        for (ck, dk), pairs in groups.items():
            c, d = _rep([p[0] for p in pairs]), _rep([p[1] for p in pairs])
            out.append({"value": f"{ck}{SEP}{dk}", "label": f"{c} / {d}", "search": [c, d]})
    else:
        groups = {}
        for r in db.execute("SELECT name, email, person_key FROM cards WHERE person_key <> '' ORDER BY id"):
            groups.setdefault(r["person_key"], []).append((r["name"], r["email"]))
        for pk, pairs in groups.items():
            n, e = _rep([p[0] for p in pairs]), _rep([p[1] for p in pairs]) or pk
            out.append({"value": pk, "label": f"{n} <{e}>", "search": [n, e, pk]})
    terms = [t for t in (normalize.search_text(w) for w in q.split()) if t]
    if terms:
        out = [c for c in out if all(any(t in normalize.search_text(s) for s in c["search"]) for t in terms)]
    out.sort(key=lambda c: normalize.search_text(c["label"]))
    return out


def _resolve(db: sqlite3.Connection, kind: str, value: str):
    """候補値からカード実データを引き、(company_key, department_key, person_key, label) を返す。無効なら None。"""
    if kind == "company":
        if not value:
            return None
        names = [r["company"] for r in db.execute(
            "SELECT company FROM cards WHERE company_key = ? AND company_key <> ''", (value,))]
        return (value, "", "", _rep(names)) if names else None
    if kind == "department":
        ck, _, dk = value.partition(SEP)
        if not ck or not dk:
            return None
        rows = db.execute(
            "SELECT company, department FROM cards WHERE company_key = ? AND department_key = ? "
            "AND company_key <> '' AND department_key <> ''", (ck, dk)).fetchall()
        if not rows:
            return None
        return (ck, dk, "", f"{_rep([r['company'] for r in rows])} / {_rep([r['department'] for r in rows])}")
    if kind == "person":
        if not value:
            return None
        rows = db.execute(
            "SELECT name, email FROM cards WHERE person_key = ? AND person_key <> ''", (value,)).fetchall()
        if not rows:
            return None
        return ("", "", value, f"{_rep([r['name'] for r in rows])} <{_rep([r['email'] for r in rows]) or value}>")
    return None


def _render(request, admin, uid, kind, q, error=None, notice=None, status=200):
    with get_db() as db:
        target = _target_user(db, uid)
        grants = db.execute("SELECT * FROM grants WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
        cands = _candidates(db, kind, q) if target["role"] == "user" else []
    return templates.TemplateResponse(request, "admin/grants.html", {
        "user": admin, "target": target, "grants": grants, "KINDS": KINDS, "kind": kind, "q": q,
        "candidates": cands[:MAX_CANDIDATES], "cand_total": len(cands), "max_cands": MAX_CANDIDATES,
        "error": error, "notice": notice,
    }, status_code=status)


@router.get("/admin/users/{uid:int}/grants")
def grants_page(request: Request, uid: int, kind: str = "company", q: str = "", admin=Depends(require_admin)):
    kind = kind if kind in KINDS else "company"
    notice = {"added": "閲覧許可を付与しました。", "dup": "すでに付与済みです。",
              "removed": "閲覧許可を解除しました。"}.get(request.query_params.get("msg", ""))
    return _render(request, admin, uid, kind, q, notice=notice)


@router.post("/admin/users/{uid:int}/grants")
async def grants_add(request: Request, uid: int, admin=Depends(require_admin)):
    form = await request.form()
    kind = str(form.get("kind") or "")
    value = str(form.get("value") or "")
    q = str(form.get("q") or "")
    if kind not in KINDS:
        return _render(request, admin, uid, "company", q, error="許可の種類が正しくありません。", status=400)
    with get_db() as db:
        target = _target_user(db, uid)
        if target["role"] != "user":
            return _render(request, admin, uid, kind, q, error="一般利用者のみに閲覧許可を付与できます。", status=400)
        res = _resolve(db, kind, value)
        if res is None:
            return _render(request, admin, uid, kind, q,
                           error="選択した対象が見つからないか、識別できないため付与できません。", status=400)
        ck, dk, pk, label = res
        cur = db.execute(
            "INSERT OR IGNORE INTO grants (user_id, kind, company_key, department_key, person_key, label) "
            "VALUES (?,?,?,?,?,?)", (uid, kind, ck, dk, pk, label))
        added = cur.rowcount > 0
    return RedirectResponse(
        f"/admin/users/{uid}/grants?kind={kind}&msg={'added' if added else 'dup'}", status_code=303)


@router.post("/admin/users/{uid:int}/grants/{gid:int}/delete")
def grants_delete(uid: int, gid: int, admin=Depends(require_admin)):
    with get_db() as db:
        _target_user(db, uid)
        db.execute("DELETE FROM grants WHERE id = ? AND user_id = ?", (gid, uid))
    return RedirectResponse(f"/admin/users/{uid}/grants?msg=removed", status_code=303)
