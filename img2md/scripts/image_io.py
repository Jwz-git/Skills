"""img2md 共用的图片读取、规范化与编码。"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageOps
except ImportError as exc:
    raise SystemExit(
        f"缺少 Python 依赖 {exc.name}。请运行: {sys.executable} -m pip install -r "
        f"{Path(__file__).resolve().parents[1] / 'requirements.txt'}"
    ) from exc

READABLE_FORMATS = {"JPEG", "MPO", "PNG", "GIF", "WEBP"}
ASSET_SUFFIXES = {".png", ".jpg", ".jpeg"}
# Amazon Bedrock 的单图上限是 5 MB（base64），约合 3.75 MB 原始字节，是官方文档中最严格的一档。
MAX_BYTES = 3_750_000
ORIENTATION = 0x0112
ALPHA_MODES = ("LA", "La", "PA", "RGBa")


@dataclass
class Frame:
    image: Image.Image
    orientation: int | None
    converted_from: str | None


def open_image(path):
    try:
        return Image.open(path)
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError(f"无法打开图片 {path}: {exc}") from exc


def frame_count(image) -> int:
    # 手机拍摄的 JPEG 常被识别为 MPO，附加帧是预览或深度图，不是页面。
    return 1 if image.format == "MPO" else getattr(image, "n_frames", 1)


def read_frame(image, index: int = 0) -> Frame:
    """返回按 EXIF 方向摆正、颜色模式规范化后的单帧。"""
    image.seek(index)
    orientation = image.getexif().get(ORIENTATION, 1)
    # 新版 Pillow 加载 TIFF 时会自行应用方向并删除该标签，所以加载后再判断一次，避免重复旋转。
    image.load()
    if image.getexif().get(ORIENTATION, 1) != 1:
        upright = ImageOps.exif_transpose(image)
    else:
        upright = image.copy()
    normalized, converted_from = _normalize_mode(upright)
    applied = orientation if orientation in range(2, 9) else None
    return Frame(normalized, applied, converted_from)


def _normalize_mode(image):
    mode = image.mode
    if mode in ("1", "L", "RGB", "RGBA") or (mode == "P" and "transparency" not in image.info):
        return image, None
    if mode.startswith(("I", "F")):
        return _to_8bit(image), mode
    if mode in ALPHA_MODES or mode == "P":
        return image.convert("RGBA"), mode
    return image.convert("RGB"), mode


def _to_8bit(image):
    values = np.nan_to_num(np.asarray(image, dtype=np.float64))
    low, high = float(values.min()), float(values.max())
    if image.mode.startswith("I;16") or (image.mode == "I" and low >= 0 and 255 < high <= 65535):
        values = values / 257.0
    elif image.mode == "F" and low >= 0 and high <= 1:
        values = values * 255.0
    elif low < 0 or high > 255:
        values = (values - low) * (255.0 / max(high - low, 1e-9))
    return Image.fromarray(np.clip(np.rint(values), 0, 255).astype(np.uint8))


def flatten(image):
    """转为不含透明通道的 L 或 RGB；透明区域按白底合成。"""
    if image.mode in ("1", "L"):
        return image.convert("L")
    if image.mode == "RGBA":
        canvas = Image.new("RGB", image.size, (255, 255, 255))
        canvas.paste(image, mask=image.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _encode(image, fmt, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, **options)
    return buffer.getvalue()


def encode_for_reading(image):
    """优先无损 PNG；超过单图大小上限时改用逐级降低质量的 JPEG。

    返回 (数据, 扩展名, JPEG 质量或 None)。
    """
    data = _encode(image, "PNG")
    if len(data) <= MAX_BYTES:
        return data, ".png", None
    flat = flatten(image)
    for quality in (95, 90, 85, 80, 70, 60):
        data = _encode(flat, "JPEG", quality=quality)
        if len(data) <= MAX_BYTES:
            return data, ".jpg", quality
    raise ValueError("无法把图片压缩到单图大小上限以内")


def save_asset(image, path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == ".png":
        image.save(path, "PNG")
    elif suffix in (".jpg", ".jpeg"):
        flatten(image).save(path, "JPEG", quality=95)
    else:
        raise ValueError("输出文件扩展名必须是 .png、.jpg 或 .jpeg")
