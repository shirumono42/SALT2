"""画像の検証・変換・保存。保存先は IMAGE_DIR（静的公開しない）。"""
import io
import logging
import re
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
import pillow_heif

from . import config

pillow_heif.register_heif_opener()
# 巨大画素数の画像によるメモリ枯渇対策: 25M画素(約5000x5000)超は DecompressionBombError で拒否
Image.MAX_IMAGE_PIXELS = 12_500_000
logger = logging.getLogger(__name__)

MAX_LONG_EDGE = 1568
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
_SAVED_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|png)$")
_EXT = {"image/jpeg": ".jpg", "image/png": ".png"}


class ImageError(Exception):
    """利用者向けメッセージを持つ画像エラー。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _ext_of(filename: str) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    return Path(name).suffix.lower()


def process_upload(filename: str, data: bytes) -> tuple[bytes, str]:
    """検証して Claude 送信/保存用の JPEG(長辺<=1568px, <=5MB) を返す。"""
    if _ext_of(filename) not in config.ALLOWED_EXTENSIONS:
        raise ImageError("対応していないファイル形式です。JPEG・PNG・HEIC の画像を選択してください。")
    if not data:
        raise ImageError("ファイルが空です。画像を選択してください。")
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise ImageError(f"ファイルサイズが上限（{config.MAX_UPLOAD_MB}MB）を超えています。")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        logger.warning("画像として開けないファイルを受信: %s", filename, exc_info=True)
        raise ImageError("画像として読み込めませんでした。ファイルが壊れているか、形式が正しくありません。")
    except Exception:
        logger.exception("画像読み込み中に予期しないエラー")
        raise ImageError("画像として読み込めませんでした。")
    try:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.getchannel("A"))
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        long_edge = max(w, h)
        if long_edge > MAX_LONG_EDGE:
            scale = MAX_LONG_EDGE / long_edge
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        quality = 90
        while True:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            if buf.tell() <= MAX_OUTPUT_BYTES or quality <= 40:
                break
            quality -= 10
        if buf.tell() > MAX_OUTPUT_BYTES:
            raise ImageError("画像を5MB以下に縮小できませんでした。別の画像をお試しください。")
        return buf.getvalue(), "image/jpeg"
    except ImageError:
        raise
    except Exception:
        logger.exception("画像変換に失敗")
        raise ImageError("画像の変換に失敗しました。別の画像をお試しください。")


def save_image(data: bytes, mime: str) -> str:
    ext = _EXT.get(mime)
    if not ext:
        raise ImageError("対応していない画像形式です。")
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (config.IMAGE_DIR / name).write_bytes(data)
    return name


def image_path(file: str) -> Path:
    """uuid(hex)+拡張子のみ許可。パストラバーサルは ValueError。"""
    if not isinstance(file, str) or not _SAVED_RE.fullmatch(file):
        raise ValueError("invalid image file name")
    return config.IMAGE_DIR / file


def delete_image(file: str) -> None:
    try:
        image_path(file).unlink(missing_ok=True)
    except Exception:
        logger.warning("画像の削除に失敗: %s", file, exc_info=True)
