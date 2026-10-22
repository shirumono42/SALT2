import time
import hashlib
import hmac
import logging

import bcrypt
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import config, normalize
from ..db import get_db
from ..templating import templates

log = logging.getLogger(__name__)
router = APIRouter()

MAX_ATTEMPTS = 5
MIN_PASSWORD = 8
LOGIN_ERROR = "メールアドレスまたはパスワードが正しくありません。"
ACTIVATE_ERROR = "メールアドレスまたはコードが正しくありません。"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(pw.encode()[:72], hashed.encode())
    except ValueError:
        return False


def hash_code(email: str, code: str) -> str:
    return hmac.new(config.SECRET_KEY.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def _render(request: Request, name: str, status: int = 200, **ctx):
    return templates.TemplateResponse(request, name, {"user": None, **ctx}, status_code=status)


def seed_initial_admin() -> None:
    email = normalize.user_email(config.ADMIN_EMAIL)
    if not email or not config.ADMIN_PASSWORD:
        log.warning("ADMIN_EMAIL/ADMIN_PASSWORD が未設定のため初期管理者を作成しません。")
        return
    with get_db() as db:
        if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return
        db.execute(
            "INSERT INTO users (email, password_hash, role) VALUES (?, ?, 'admin')",
            (email, hash_password(config.ADMIN_PASSWORD)),
        )
    log.info("初期管理者を作成しました。")


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/cards", status_code=303)
    return _render(request, "auth/login.html")


_FAILS: dict[tuple[str, str], list[float]] = {}
_MAX_FAILS, _WINDOW_SEC = 10, 300


def _throttled(ip: str, key: str) -> bool:
    now = time.time()
    recent = [t for t in _FAILS.get((ip, key), []) if now - t < _WINDOW_SEC]
    _FAILS[(ip, key)] = recent
    return len(recent) >= _MAX_FAILS


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: str = Form(""), password: str = Form("")):
    key = normalize.user_email(email)
    ip = request.client.host if request.client else ""
    if _throttled(ip, key):
        return _render(request, "auth/login.html", 429,
                       error="ログインの失敗が続いたため、しばらく時間をおいてからお試しください。", email=email)
    with get_db() as db:
        row = db.execute("SELECT id, password_hash FROM users WHERE email=?", (key,)).fetchone()
    ok = check_password(password, row["password_hash"] if row else _DUMMY_HASH)
    if not (row and row["password_hash"] and ok):
        _FAILS.setdefault((ip, key), []).append(time.time())
        return _render(request, "auth/login.html", 400, error=LOGIN_ERROR, email=email)
    _FAILS.pop((ip, key), None)
    request.session.clear()
    request.session["user_id"] = row["id"]
    return RedirectResponse("/cards", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/activate", response_class=HTMLResponse)
def activate_form(request: Request):
    return _render(request, "auth/activate.html")


@router.post("/activate", response_class=HTMLResponse)
def activate(
    request: Request,
    email: str = Form(""),
    code: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    key = normalize.user_email(email)
    code = code.strip()

    def fail(msg: str):
        return _render(request, "auth/activate.html", 400, error=msg, email=email)

    with get_db() as db:
        row = db.execute(
            "SELECT id, password_hash, activation_code_hash, activation_attempts FROM users WHERE email=?",
            (key,),
        ).fetchone()
        if not row or row["password_hash"] or not row["activation_code_hash"]:
            return fail(ACTIVATE_ERROR)
        if row["activation_attempts"] >= MAX_ATTEMPTS:
            db.execute("UPDATE users SET activation_code_hash=NULL WHERE id=?", (row["id"],))
            return fail(ACTIVATE_ERROR)
        if not hmac.compare_digest(row["activation_code_hash"], hash_code(key, code)):
            attempts = row["activation_attempts"] + 1
            # 5回を超えて失敗した時点(=6回目)でコード失効
            expire = attempts > MAX_ATTEMPTS
            db.execute(
                "UPDATE users SET activation_attempts=?, activation_code_hash=CASE WHEN ? THEN NULL ELSE activation_code_hash END WHERE id=?",
                (attempts, expire, row["id"]),
            )
            if expire:
                log.warning("有効化コードが試行回数超過で失効しました: user_id=%s", row["id"])
            return fail(ACTIVATE_ERROR)
        if len(password) < MIN_PASSWORD:
            return fail(f"パスワードは{MIN_PASSWORD}文字以上で入力してください。")
        if password != password2:
            return fail("パスワード(確認)が一致しません。")
        db.execute(
            "UPDATE users SET password_hash=?, activation_code_hash=NULL, activation_attempts=0 WHERE id=?",
            (hash_password(password), row["id"]),
        )
    return _render(request, "auth/login.html", notice="有効化しました。ログインしてください。", email=email)
