"""招待メール送信。SMTP未設定・失敗時は False を返す(呼び出し側でコードを手渡し表示する)。"""
import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger(__name__)


def send_invite(to: str, code: str, base_url: str = "") -> bool:
    if not (config.SMTP_HOST and config.SMTP_FROM):
        return False
    url = f"{base_url.rstrip('/')}/activate" if base_url else "/activate"
    msg = EmailMessage()
    msg["Subject"] = "【名刺管理】アカウント有効化のご案内"
    msg["From"] = config.SMTP_FROM
    msg["To"] = to
    msg.set_content(
        "名刺管理システムに登録されました。\n"
        f"次のページでメールアドレス・下記の認証コード・新しいパスワードを入力して有効化してください。\n\n"
        f"有効化ページ: {url}\n"
        f"認証コード: {code}\n\n"
        "このコードは1回のみ有効です。心当たりがない場合は破棄してください。\n"
    )
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return True
    except Exception:
        log.exception("招待メールの送信に失敗しました")
        return False
