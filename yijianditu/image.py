# -*- coding: utf-8 -*-
"""
numpy 数组 → PIL Image → JPG 压缩（体积上限内二分搜索最高质量）
移植自 yingxiang2cad/image.py，输入固定为 uint8 三波段，去掉了多 dtype 拉伸分支。
"""
from __future__ import annotations

import io
import os

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def array_to_pil_image(data: np.ndarray) -> Image.Image:
    """(3, h, w) uint8 数组 → PIL RGB Image"""
    if data.ndim != 3:
        raise ValueError(f"期望 (bands, h, w) 三维数组，实际 {data.shape}")
    bands = data.shape[0]
    if data.dtype != np.uint8:
        arr = data.astype(np.float32)
        lo, hi = float(np.nanmin(arr)), float(np.nanmax(arr))
        arr = (arr - lo) / (hi - lo) * 255.0 if hi > lo else np.clip(arr, 0, 255)
        data = np.clip(arr, 0, 255).astype(np.uint8)

    if bands == 1:
        return Image.fromarray(data[0], mode="L").convert("RGB")
    return Image.fromarray(np.transpose(data[:3], (1, 2, 0)), mode="RGB")


def _encoded_size(img: Image.Image, quality: int) -> int:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.tell()


def _best_quality(img: Image.Image, max_bytes: int) -> tuple[int, int]:
    """二分搜索满足体积上限的最高质量，返回 (quality, size)"""
    lo, hi = 10, 95
    best_q, best_size = lo, _encoded_size(img, lo)
    if best_size > max_bytes:
        return best_q, best_size

    while lo <= hi:
        mid = (lo + hi) // 2
        size = _encoded_size(img, mid)
        if size <= max_bytes:
            best_q, best_size = mid, size
            lo = mid + 1
        else:
            hi = mid - 1
    return best_q, best_size


def compress_to_jpg(pil_image: Image.Image, output_path: str, max_size_mb: float = 30.0) -> str:
    """
    保存为 JPG 并确保 ≤ max_size_mb：先压质量，仍超限则按比例降分辨率。
    """
    max_bytes = int(max_size_mb * 1024 * 1024)
    img = pil_image if pil_image.mode == "RGB" else pil_image.convert("RGB")

    quality, size = _best_quality(img, max_bytes)

    # 最低质量仍超限 → 缩小分辨率后再压一轮（最多两轮，避免过度降质）
    for _ in range(2):
        if size <= max_bytes:
            break
        scale = max(0.2, (max_bytes / size) ** 0.5 * 0.9)
        new_w = max(200, int(img.width * scale))
        new_h = max(200, int(img.height * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
        quality, size = _best_quality(img, max_bytes)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, format="JPEG", quality=quality, optimize=True)
    return output_path


def image_pixel_size(path: str) -> tuple[int, int]:
    with Image.open(path) as im:
        return im.size
