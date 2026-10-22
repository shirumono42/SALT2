"""名刺フィールドの検証・キー計算・重複検知（register / cards 共通）。"""
import re
import sqlite3

from . import normalize

FIELDS = ("name", "company", "department", "title", "email", "phone", "address", "website")
LABELS = {
    "name": "氏名", "company": "会社名", "department": "部署", "title": "役職",
    "email": "メールアドレス", "phone": "電話番号", "address": "住所", "website": "Webサイト",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^(https?://)?[^\s/@]+\.[^\s/@]+(/\S*)?$", re.IGNORECASE)


def clean_fields(raw: dict) -> dict:
    return {k: (raw.get(k) or "").strip() for k in FIELDS}


def compute_keys(fields: dict) -> dict:
    return {
        "company_key": normalize.company_key(fields.get("company")),
        "department_key": normalize.department_key(fields.get("department")),
        "person_key": normalize.person_key(fields.get("email")),
    }


def validate_fields(fields: dict) -> list[str]:
    errors = []
    if not (fields.get("name") or "").strip():
        errors.append("氏名は必須です。")
    email = (fields.get("email") or "").strip()
    if email and not _EMAIL_RE.match(email):
        errors.append("メールアドレスの形式が正しくありません。")
    website = (fields.get("website") or "").strip()
    if website and not _URL_RE.match(website):
        errors.append("WebサイトのURL形式が正しくありません。")
    return errors


def empty_field_labels(fields: dict) -> list[str]:
    return [LABELS[k] for k in FIELDS if not (fields.get(k) or "").strip()]


def find_duplicate(db: sqlite3.Connection, fields: dict, exclude_id: int | None = None):
    """person_key(メール)優先、無ければ 企業キー+氏名 で既存名刺を探す。無ければ None。"""
    keys = compute_keys(fields)
    ex = exclude_id if exclude_id is not None else -1
    if keys["person_key"]:
        return db.execute(
            "SELECT * FROM cards WHERE person_key = ? AND id <> ? ORDER BY id LIMIT 1",
            (keys["person_key"], ex),
        ).fetchone()
    name = normalize.search_text(fields.get("name"))
    if keys["company_key"] and name:
        for row in db.execute(
            "SELECT * FROM cards WHERE company_key = ? AND person_key = '' AND id <> ? ORDER BY id",
            (keys["company_key"], ex),
        ):
            if normalize.search_text(row["name"]) == name:
                return row
    return None
