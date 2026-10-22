import logging
import re
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from .. import mailer, normalize
from ..db import get_db
from ..deps import require_admin
from ..templating import templates
from .auth import hash_code

log = logging.getLogger(__name__)
router = APIRouter()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _new_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _list_users():
    with get_db() as db:
        return db.execute(
            "SELECT id, email, role, password_hash IS NOT NULL AS activated, "
            "activation_code_hash IS NOT NULL AS has_code FROM users ORDER BY id"
        ).fetchall()


def _page(request: Request, admin, status: int = 200, **ctx):
    return templates.TemplateResponse(
        request, "admin/users.html", {"user": admin, "users": _list_users(), **ctx}, status_code=status
    )


def _deliver(request: Request, email: str, code: str) -> dict:
    """招待メール送信。送れなければコードを1回だけ画面に出し、ログにも残す。"""
    if mailer.send_invite(email, code, str(request.base_url)):
        return {"notice": f"{email} に招待メールを送信しました。"}
    log.warning("招待メール未送信のためコードを手渡しで通知してください: %s code=%s", email, code)
    return {
        "notice": f"{email} への招待メールを送信できませんでした。下のコードを本人へお伝えください(この表示は一度きりです)。",
        "shown_code": code,
        "shown_email": email,
    }


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, admin=Depends(require_admin)):
    return _page(request, admin)


@router.post("/admin/users", response_class=HTMLResponse)
def add_user(request: Request, email: str = Form(""), admin=Depends(require_admin)):
    key = normalize.user_email(email)
    if not _EMAIL_RE.match(key):
        return _page(request, admin, 400, error="メールアドレスの形式が正しくありません。")
    code = _new_code()
    with get_db() as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (key,)).fetchone():
            return _page(request, admin, 400, error="登録済みです。")
        db.execute(
            "INSERT INTO users (email, role, activation_code_hash, activation_attempts) VALUES (?, 'user', ?, 0)",
            (key, hash_code(key, code)),
        )
    return _page(request, admin, **_deliver(request, key, code))


@router.post("/admin/users/{uid}/reissue", response_class=HTMLResponse)
def reissue(request: Request, uid: int, admin=Depends(require_admin)):
    code = _new_code()
    with get_db() as db:
        row = db.execute("SELECT id, email, password_hash FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            return _page(request, admin, 404, error="利用者が見つかりません。")
        if row["password_hash"]:
            return _page(request, admin, 400, error="有効化済みの利用者にはコードを再発行できません。")
        db.execute(
            "UPDATE users SET activation_code_hash=?, activation_attempts=0 WHERE id=?",
            (hash_code(row["email"], code), uid),
        )
    return _page(request, admin, **_deliver(request, row["email"], code))
