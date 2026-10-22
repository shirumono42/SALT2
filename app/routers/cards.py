"""名刺の一覧・検索・詳細・画像配信・編集・削除。権限判定は app.permissions のみ。"""
import logging
import math
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .. import cardsvc, config, normalize
from ..db import get_db
from ..deps import current_user
from ..permissions import can_edit, can_view, visible_cards_where
from ..templating import templates

try:  # images.py は別担当。未作成でも動くようにフォールバックを持つ
    from .. import images
except ImportError:  # pragma: no cover
    images = None

log = logging.getLogger(__name__)
router = APIRouter()

PER_PAGE_CHOICES = (20, 50, 100)
SORTS = {"name": "氏名", "company": "会社名", "created": "登録日"}
TARGETS = {"both": "氏名・会社名", "name": "氏名", "company": "会社名"}
MATCHES = {"partial": "部分一致", "exact": "完全一致"}
MODES = {"and": "すべて含む(AND)", "or": "いずれか含む(OR)"}


def _image_path(file: str) -> Path:
    if images is not None and hasattr(images, "image_path"):
        return images.image_path(file)
    return config.IMAGE_DIR / file


def _get_card(card_id: int):
    with get_db() as db:
        return db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


def _viewable_or_404(user, card_id: int):
    card = _get_card(card_id)
    if card is None or not can_view(user, card):
        raise HTTPException(status_code=404, detail="名刺が見つかりません")
    return card


def _matches(terms, mode, target, match, row) -> bool:
    fields = []
    if target in ("both", "name"):
        fields.append(normalize.search_text(row["name"]))
    if target in ("both", "company"):
        fields.append(normalize.search_text(row["company"]))

    def hit(t):
        return any((t == f) if match == "exact" else (t in f) for f in fields)

    results = (hit(t) for t in terms)
    return all(results) if mode == "and" else any(results)


@router.get("/cards")
def list_cards(
    request: Request,
    q: str = "",
    target: str = "both",
    match: str = "partial",
    mode: str = "and",
    sort: str = "created",
    order: str = "desc",
    per_page: int = 20,
    page: int = 1,
    user=Depends(current_user),
):
    target = target if target in TARGETS else "both"
    match = match if match in MATCHES else "partial"
    mode = mode if mode in MODES else "and"
    sort = sort if sort in SORTS else "created"
    order = order if order in ("asc", "desc") else "desc"
    per_page = per_page if per_page in PER_PAGE_CHOICES else 20

    where, params = visible_cards_where(user)
    order_col = {"name": "c.name", "company": "c.company", "created": "c.created_at"}[sort]
    direction = "ASC" if order == "asc" else "DESC"
    with get_db() as db:
        rows = db.execute(
            f"SELECT c.id, c.name, c.company, c.department, c.title, c.created_at "
            f"FROM cards c WHERE {where} ORDER BY {order_col} {direction}, c.id {direction}",
            params,
        ).fetchall()

    terms = [t for t in (normalize.search_text(w) for w in q.split()) if t]
    if terms:
        rows = [r for r in rows if _matches(terms, mode, target, match, r)]
    if sort in ("name", "company"):
        # 全半角・大小差を吸収した順序で並べる(空の会社名は常に末尾側にならないよう単純比較)
        rows.sort(key=lambda r: normalize.search_text(r[sort]), reverse=(order == "desc"))

    total = len(rows)
    pages = max(1, math.ceil(total / per_page))
    page = min(max(page, 1), pages)
    items = rows[(page - 1) * per_page: page * per_page]

    qs = {"q": q, "target": target, "match": match, "mode": mode, "sort": sort,
          "order": order, "per_page": per_page}
    def pageurl(n: int) -> str:
        return "/cards?" + urlencode({**qs, "page": n})

    return templates.TemplateResponse(request, "cards/list.html", {
        "pageurl": pageurl, "user": user, "items": items, "total": total, "page": page, "pages": pages, "qs": qs,
        "SORTS": SORTS, "TARGETS": TARGETS, "MATCHES": MATCHES, "MODES": MODES,
        "PER_PAGE_CHOICES": PER_PAGE_CHOICES, "searched": bool(terms),
    })


@router.get("/cards/{card_id:int}")
def card_detail(request: Request, card_id: int, user=Depends(current_user)):
    card = _viewable_or_404(user, card_id)
    with get_db() as db:
        reg = db.execute("SELECT email FROM users WHERE id = ?", (card["registered_by"],)).fetchone()
    return templates.TemplateResponse(request, "cards/detail.html", {
        "user": user, "card": card, "fields": cardsvc.FIELDS, "labels": cardsvc.LABELS,
        "registrant": reg["email"] if reg else "", "editable": can_edit(user, card),
        "notice": request.query_params.get("saved") and "保存しました。" or None,
    })


@router.get("/cards/{card_id:int}/image")
def card_image(card_id: int, user=Depends(current_user)):
    card = _viewable_or_404(user, card_id)
    try:
        path = _image_path(card["image_file"])
    except ValueError:
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="画像が見つかりません")
    return FileResponse(
        path,
        media_type=card["image_mime"],
        headers={"Cache-Control": "private", "X-Content-Type-Options": "nosniff"},
    )


def _edit_card_or_error(user, card_id: int):
    card = _viewable_or_404(user, card_id)
    if not can_edit(user, card):
        raise HTTPException(status_code=403, detail="この名刺を編集・削除する権限がありません")
    return card


def _render_edit(request, user, card, values, errors, status=200):
    return templates.TemplateResponse(request, "cards/edit.html", {
        "user": user, "card": card, "values": values, "fields": cardsvc.FIELDS,
        "labels": cardsvc.LABELS, "error": "\n".join(errors) if errors else None,
        "errors": errors,
    }, status_code=status)


@router.get("/cards/{card_id:int}/edit")
def edit_form(request: Request, card_id: int, user=Depends(current_user)):
    card = _edit_card_or_error(user, card_id)
    return _render_edit(request, user, card, {k: card[k] for k in cardsvc.FIELDS}, [])


@router.post("/cards/{card_id:int}/edit")
async def edit_save(request: Request, card_id: int, user=Depends(current_user)):
    card = _edit_card_or_error(user, card_id)
    form = await request.form()
    values = cardsvc.clean_fields({k: str(form.get(k) or "") for k in cardsvc.FIELDS})
    errors = cardsvc.validate_fields(values)
    if errors:
        return _render_edit(request, user, card, values, errors, 422)
    keys = cardsvc.compute_keys(values)
    try:
        with get_db() as db:
            if cardsvc.find_duplicate(db, values, exclude_id=card_id) is not None:
                errors = ["同じ人物の名刺が既に登録されています"]
            else:
                db.execute(
                    "UPDATE cards SET name=?, company=?, department=?, title=?, email=?, phone=?, "
                    "address=?, website=?, company_key=?, department_key=?, person_key=?, "
                    "updated_at=datetime('now') WHERE id=?",
                    [*(values[k] for k in cardsvc.FIELDS), keys["company_key"],
                     keys["department_key"], keys["person_key"], card_id],
                )
    except Exception:
        log.exception("card update failed id=%s", card_id)
        return _render_edit(request, user, card, values, ["保存に失敗しました。もう一度お試しください。"], 500)
    if errors:
        return _render_edit(request, user, card, values, errors, 409)
    return RedirectResponse(f"/cards/{card_id}?saved=1", status_code=303)


@router.post("/cards/{card_id:int}/delete")
def delete_card(card_id: int, user=Depends(current_user)):
    card = _edit_card_or_error(user, card_id)
    with get_db() as db:
        db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    try:
        _image_path(card["image_file"]).unlink(missing_ok=True)
    except Exception:
        log.exception("image delete failed id=%s", card_id)
    return RedirectResponse("/cards", status_code=303)
