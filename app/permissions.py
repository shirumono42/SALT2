"""閲覧権限の唯一の判定箇所。一覧・検索・詳細・画像・編集・削除はすべてここを通す。"""
import sqlite3

from .db import get_db

_USER_WHERE = """(
    c.registered_by = ?
    OR (c.company_key <> '' AND EXISTS (
        SELECT 1 FROM grants g WHERE g.user_id = ? AND g.kind = 'company'
          AND g.company_key <> '' AND g.company_key = c.company_key))
    OR (c.company_key <> '' AND c.department_key <> '' AND EXISTS (
        SELECT 1 FROM grants g WHERE g.user_id = ? AND g.kind = 'department'
          AND g.company_key <> '' AND g.department_key <> ''
          AND g.company_key = c.company_key AND g.department_key = c.department_key))
    OR (c.person_key <> '' AND EXISTS (
        SELECT 1 FROM grants g WHERE g.user_id = ? AND g.kind = 'person'
          AND g.person_key <> '' AND g.person_key = c.person_key))
)"""


def visible_cards_where(user) -> tuple[str, list]:
    """`cards c` に対するWHERE断片。role が admin/user のどちらでもなければ何も見せない。"""
    if user["role"] == "admin":
        return "1=1", []
    if user["role"] == "user":
        uid = user["id"]
        return _USER_WHERE, [uid, uid, uid, uid]
    return "0=1", []


def can_view(user, card) -> bool:
    if card is None:
        return False
    where, params = visible_cards_where(user)
    with get_db() as db:
        row = db.execute(
            f"SELECT 1 FROM cards c WHERE c.id = ? AND {where}", [card["id"], *params]
        ).fetchone()
    return row is not None


def can_edit(user, card) -> bool:
    if card is None:
        return False
    return user["role"] == "admin" or card["registered_by"] == user["id"]
