"""全ルーター共通の認証依存関数。セッションCookie(署名付き)に user_id を保持。"""
import sqlite3

from fastapi import Depends, HTTPException, Request

from .db import get_db


class LoginRequired(Exception):
    pass


def current_user(request: Request) -> sqlite3.Row:
    uid = request.session.get("user_id")
    if not uid:
        raise LoginRequired()
    with get_db() as db:
        user = db.execute(
            "SELECT id,email,role FROM users WHERE id=? AND password_hash IS NOT NULL", (uid,)
        ).fetchone()
    if not user:
        request.session.clear()
        raise LoginRequired()
    return user


def require_admin(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="管理者のみ実行できます")
    return user
