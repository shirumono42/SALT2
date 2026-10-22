"""名刺登録フロー: アップロード → AI抽出 → 確認/編集 → 登録（重複時は更新 or 破棄）。"""
import json
import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from .. import cardsvc, config, extraction, images, permissions
from ..db import get_db
from ..deps import current_user
from ..templating import templates

logger = logging.getLogger(__name__)
router = APIRouter()

SAVE_FAILED = "保存に失敗しました。もう一度お試しください。"


def _upload_page(request, user, error=None, status=200):
    return templates.TemplateResponse(
        request, "register/new.html",
        {"user": user, "error": error, "max_mb": config.MAX_UPLOAD_MB,
         "exts": "JPEG（.jpg/.jpeg）・PNG（.png）・HEIC（.heic/.heif）"},
        status_code=status)


def _get_draft(db, draft_id: int, user):
    return db.execute(
        "SELECT * FROM drafts WHERE id = ? AND user_id = ?", (draft_id, user["id"])).fetchone()


def _draft_page(request, user, draft, fields, errors=None, notice=None, status=200):
    """確認画面。重複検知は毎回その時点のフォーム値で行う。"""
    dup = None
    dup_view = dup_edit = False
    diff_keys: list[str] = []
    with get_db() as db:
        row = cardsvc.find_duplicate(db, fields)
        if row is not None:
            dup = row
    if dup is not None:
        dup_view = permissions.can_view(user, dup)
        if dup_view:
            dup_edit = permissions.can_edit(user, dup)
            diff_keys = [k for k in cardsvc.FIELDS if (dup[k] or "").strip() != fields[k]]
    empty = cardsvc.empty_field_labels(fields)
    ctx = {
        "user": user, "draft": draft, "fields": fields, "labels": cardsvc.LABELS,
        "order": cardsvc.FIELDS, "empty_labels": empty, "name_empty": not fields["name"],
        "errors": errors or [], "notice": notice,
        "dup": dup if dup_view else None, "dup_exists": dup is not None,
        "dup_view": dup_view, "dup_edit": dup_edit, "diff_keys": diff_keys,
    }
    return templates.TemplateResponse(request, "register/draft.html", ctx, status_code=status)


@router.get("/cards/new")
def new_form(request: Request, user=Depends(current_user)):
    return _upload_page(request, user)


@router.post("/cards/extract")
def extract(request: Request, file: UploadFile | None = File(None), user=Depends(current_user)):
    if file is None or not (file.filename or ""):
        return _upload_page(request, user, "画像ファイルを選択してください。", 400)
    data = file.file.read(config.MAX_UPLOAD_BYTES + 1)
    try:
        img_bytes, mime = images.process_upload(file.filename, data)
    except images.ImageError as e:
        return _upload_page(request, user, e.message, 400)
    try:
        saved = images.save_image(img_bytes, mime)
    except Exception:
        logger.exception("画像の保存に失敗")
        return _upload_page(request, user, "画像の保存に失敗しました。もう一度お試しください。", 500)
    try:
        fields = extraction.extract_card(img_bytes, mime)
    except extraction.ExtractionError as e:
        images.delete_image(saved)
        return _upload_page(request, user, f"{e.message}（もう一度アップロードして再試行できます）", 502)
    except Exception:
        logger.exception("抽出で予期しないエラー")
        images.delete_image(saved)
        return _upload_page(request, user, "名刺の読み取りに失敗しました。もう一度お試しください。", 502)
    try:
        fields = cardsvc.clean_fields(fields)
        with get_db() as db:
            cur = db.execute(
                "INSERT INTO drafts (user_id, image_file, image_mime, extracted_json) VALUES (?,?,?,?)",
                (user["id"], saved, mime, json.dumps(fields, ensure_ascii=False)))
            draft_id = cur.lastrowid
    except Exception:
        logger.exception("draft の保存に失敗")
        images.delete_image(saved)
        return _upload_page(request, user, SAVE_FAILED, 500)
    return RedirectResponse(f"/cards/draft/{draft_id}", status_code=303)


def _not_found():
    return Response("見つかりません", status_code=404, media_type="text/plain; charset=utf-8")


@router.get("/cards/draft/{draft_id}/image")
def draft_image(draft_id: int, user=Depends(current_user)):
    with get_db() as db:
        draft = _get_draft(db, draft_id, user)
    if not draft:
        return _not_found()
    try:
        path = images.image_path(draft["image_file"])
    except ValueError:
        return _not_found()
    if not path.is_file():
        return _not_found()
    return FileResponse(path, media_type=draft["image_mime"],
                        headers={"Cache-Control": "private, no-store"})


@router.get("/cards/draft/{draft_id}")
def draft_view(request: Request, draft_id: int, user=Depends(current_user)):
    with get_db() as db:
        draft = _get_draft(db, draft_id, user)
    if not draft:
        return _not_found()
    fields = cardsvc.clean_fields(json.loads(draft["extracted_json"]))
    return _draft_page(request, user, draft, fields)


@router.post("/cards/draft/{draft_id}/register")
async def draft_register(request: Request, draft_id: int, user=Depends(current_user)):
    form = await request.form()
    with get_db() as db:
        draft = _get_draft(db, draft_id, user)
    if not draft:
        return _not_found()
    # registered_by 等のフォーム値は一切信用しない。8項目だけを取り出す。
    fields = cardsvc.clean_fields({k: form.get(k) for k in cardsvc.FIELDS if isinstance(form.get(k), str)})

    errors = cardsvc.validate_fields(fields)
    if errors:
        return _draft_page(request, user, draft, fields, errors=errors, status=400)

    keys = cardsvc.compute_keys(fields)
    discard_file = None
    try:
        with get_db() as db:
            dup = cardsvc.find_duplicate(db, fields)
            if dup is not None:
                if not permissions.can_view(user, dup):
                    return _draft_page(request, user, draft, fields,
                                       errors=["この名刺は既に登録されています。"], status=409)
                if not permissions.can_edit(user, dup):
                    return _draft_page(request, user, draft, fields,
                                       errors=["この名刺は既に登録されています。更新できるのは登録者と管理者のみです。"],
                                       status=403)
                if all((dup[k] or "").strip() == fields[k] for k in cardsvc.FIELDS):
                    return _draft_page(request, user, draft, fields,
                                       errors=["同一内容が登録済みです。"], status=409)
                db.execute(
                    "UPDATE cards SET name=?, company=?, department=?, title=?, email=?, phone=?,"
                    " address=?, website=?, company_key=?, department_key=?, person_key=?,"
                    " updated_at=datetime('now') WHERE id=?",
                    (*[fields[k] for k in cardsvc.FIELDS], keys["company_key"],
                     keys["department_key"], keys["person_key"], dup["id"]))
                db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
                discard_file = draft["image_file"]
                target = dup["id"]
            else:
                cur = db.execute(
                    "INSERT INTO cards (name, company, department, title, email, phone, address,"
                    " website, image_file, image_mime, company_key, department_key, person_key,"
                    " registered_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (*[fields[k] for k in cardsvc.FIELDS], draft["image_file"], draft["image_mime"],
                     keys["company_key"], keys["department_key"], keys["person_key"], user["id"]))
                db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
                target = cur.lastrowid
    except Exception:
        logger.exception("名刺の保存に失敗 (draft=%s)", draft_id)
        return _draft_page(request, user, draft, fields, errors=[SAVE_FAILED], status=500)
    if discard_file:
        images.delete_image(discard_file)
    return RedirectResponse(f"/cards/{target}", status_code=303)


@router.post("/cards/draft/{draft_id}/discard")
def draft_discard(draft_id: int, user=Depends(current_user)):
    with get_db() as db:
        draft = _get_draft(db, draft_id, user)
        if not draft:
            return _not_found()
        db.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    images.delete_image(draft["image_file"])
    return RedirectResponse("/cards/new", status_code=303)
