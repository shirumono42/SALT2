"""識別キーの正規化。権限判定の一意性はすべてここのキーに基づく。
空文字のキー = 識別不能 → 企業/部署/人物の許可には絶対に一致させない(安全側)。"""
import re
import unicodedata

_CORP_WORDS = [
    "株式会社", "有限会社", "合同会社", "合資会社", "一般社団法人", "一般財団法人",
    "公益社団法人", "公益財団法人", "特定非営利活動法人", "医療法人", "学校法人", "社会福祉法人",
    "(株)", "(有)", "(合)", "(同)",
    "co.,ltd.", "co.,ltd", "co.ltd.", "co.ltd", "company,limited", "corporation", "incorporated",
    "inc.", "inc", "corp.", "corp", "ltd.", "ltd", "llc", "gmbh", "kk",
]


def _base(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"[\s　・･]+", "", s)


_LATIN_CORP_RE = re.compile(
    r"(?<![a-z0-9])(?:co\.?\s*,?\s*ltd\.?|company\s*,?\s*limited|corporation|incorporated|inc\.?|corp\.?|ltd\.?|llc|gmbh|kk)(?![a-z0-9])"
)


def company_key(company: str | None) -> str:
    # 英字の法人格は語境界でのみ除去（'Zinc' の 'inc' 等を壊さない）。空白除去より前に行う。
    s = _LATIN_CORP_RE.sub("", unicodedata.normalize("NFKC", company or "").lower())
    s = _base(s)
    for w in sorted((w for w in _CORP_WORDS if not w.isascii() or w.startswith("(")), key=len, reverse=True):
        s = s.replace(w, "")
    return re.sub(r"[.,，、。_\-－ー・'\"]", "", s)


def department_key(department: str | None) -> str:
    return re.sub(r"[.,，、。_\-－ー・'\"]", "", _base(department))


def person_key(email: str | None) -> str:
    """人物の識別キー = 正規化メールアドレス。形式不正・空は識別不能('')。"""
    e = _base(email)
    return e if re.fullmatch(r"[^@]+@[^@]+\.[^@]+", e) else ""


def user_email(email: str | None) -> str:
    return unicodedata.normalize("NFKC", email or "").strip().lower()


def search_text(s: str | None) -> str:
    """氏名・会社名検索用(全角半角・大小文字・空白差を吸収)。"""
    return _base(s)
