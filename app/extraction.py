"""Anthropic API に名刺画像を直接渡して8項目をJSON抽出する。APIキーはサーバー側のみ。"""
import base64
import logging

import anthropic

from . import config

logger = logging.getLogger(__name__)

FIELDS = ("name", "company", "department", "title", "email", "phone", "address", "website")
TIMEOUT_SECONDS = 60.0

SYSTEM_PROMPT = """あなたは名刺画像から情報を正確に書き起こす抽出器です。画像は名刺1枚です。次のルールを厳守して、必ず record_business_card ツールで結果を返してください。

- 名刺に印字されている内容だけを書き起こす。推測・補完・正規化・翻訳は禁止。
- 読み取れない項目、名刺に無い項目は必ず空文字("")にする。
- 電話番号が複数ある場合は、本人の携帯電話または直通番号を優先する。FAX番号は電話番号として出力しない。
- 郵便番号がある場合は住所の先頭に含める。
- メールアドレスやWebサイトURLが複数ある場合は、本人のものと思われる1つだけを出力する。
- 外国語・多言語の名刺も、印字されているとおりそのまま出力する。
- ふりがな・読み仮名は出力しない。
- 項目: name(氏名), company(会社名), department(部署), title(役職), email, phone, address, website。"""

_TOOL = {
    "name": "record_business_card",
    "description": "名刺から読み取った8項目を記録する。読めない・無い項目は空文字。",
    "input_schema": {
        "type": "object",
        "properties": {k: {"type": "string"} for k in FIELDS},
        "required": list(FIELDS),
        "additionalProperties": False,
    },
}


class ExtractionError(Exception):
    """利用者向けメッセージを持つ抽出エラー。詳細は logging のみ。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def extract_card(image_bytes: bytes, mime: str) -> dict:
    if not config.ANTHROPIC_API_KEY:
        logger.error("ANTHROPIC_API_KEY が未設定です")
        raise ExtractionError("AI読み取りの設定がされていません。管理者に連絡してください。")
    try:
        client = anthropic.Anthropic(
            api_key=config.ANTHROPIC_API_KEY, timeout=TIMEOUT_SECONDS, max_retries=1
        )
        resp = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[_TOOL],
            tool_choice={"type": "auto"},  # 一部モデルは tool/any の強制に非対応のため、指示で必ず呼ばせる
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": mime,
                        "data": base64.b64encode(image_bytes).decode("ascii")}},
                    {"type": "text", "text": f"この名刺から8項目を抽出し、必ず {_TOOL['name']} ツールを呼び出して結果を返してください。"},
                ],
            }],
        )
    except anthropic.APITimeoutError:
        logger.error("Anthropic API タイムアウト", exc_info=True)
        raise ExtractionError("読み取りがタイムアウトしました。もう一度お試しください。")
    except anthropic.NotFoundError:
        logger.error("モデル %s が利用できません (404)", config.CLAUDE_MODEL, exc_info=True)
        raise ExtractionError(
            f"AIモデル（{config.CLAUDE_MODEL}）が利用できません。管理者にモデル設定の確認を依頼してください。")
    except anthropic.AuthenticationError:
        logger.error("Anthropic API 認証エラー", exc_info=True)
        raise ExtractionError("AI読み取りの認証に失敗しました。管理者に連絡してください。")
    except anthropic.APIError:
        logger.error("Anthropic API エラー (model=%s)", config.CLAUDE_MODEL, exc_info=True)
        raise ExtractionError("名刺の読み取りに失敗しました。もう一度お試しください。")
    except Exception:
        logger.exception("名刺抽出中に予期しないエラー")
        raise ExtractionError("名刺の読み取りに失敗しました。もう一度お試しください。")

    data = None
    for block in resp.content:
        if getattr(block, "type", "") == "tool_use" and block.name == _TOOL["name"]:
            data = block.input
            break
    if not isinstance(data, dict):
        logger.error("tool_use ブロックが無い/不正 (stop_reason=%s)", getattr(resp, "stop_reason", None))
        raise ExtractionError("読み取り結果を解釈できませんでした。もう一度お試しください。")
    result = {}
    for k in FIELDS:
        v = data.get(k, "")
        if v is None:
            v = ""
        if not isinstance(v, str):
            logger.error("抽出結果の型が不正: %s=%r", k, type(v))
            raise ExtractionError("読み取り結果を解釈できませんでした。もう一度お試しください。")
        result[k] = v.strip()
    return result
