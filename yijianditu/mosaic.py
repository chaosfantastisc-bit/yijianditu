# -*- coding: utf-8 -*-
"""
瓦片并发下载 + 拼接 + 精确裁剪

下载策略参考 geo-downloader/src-tauri/src/downloader.rs：
  - 多子域轮转分摊压力
  - 随机 UA + Referer
  - 失败指数退避重试
线程分工：worker 只负责取字节流，贴图统一在主线程完成（PIL 非线程安全）。
"""
from __future__ import annotations

import io
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from PIL import Image

from .config import (
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
    RETRY_TIMES,
    TILE_SIZE,
    USER_AGENT,
    log,
)
from .tiles import TileGrid

Image.MAX_IMAGE_PIXELS = None  # 关闭 PIL 的解压炸弹保护，遥感大图需要


class CancelledError(Exception):
    """用户取消任务"""


def build_tile_url(source: dict, x: int, y: int, z: int) -> str:
    from . import config

    url = source["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    url = url.replace("{tk}", config.TIANDITU_TOKEN)  # 运行时注入当前 token
    subs = source.get("subdomains") or []
    if "{s}" in url:
        sub = subs[(x + y) % len(subs)] if subs else ""
        url = url.replace("{s}", sub)
    return url


_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    """全局复用 Session：连接池 + keep-alive，省去每瓦片重复的 TLS 握手"""
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        retry = Retry(
            total=RETRY_TIMES - 1,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=retry)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
        })
        _SESSION = s
    return _SESSION


def fetch_tile(url: str, referer: str | None = None) -> bytes:
    """下载单张瓦片（连接池复用 + 自动重试）。失败抛异常。"""
    headers = {"Referer": referer} if referer else None
    try:
        resp = _get_session().get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.content
    except Exception as e:  # noqa: BLE001 - 网络异常种类繁多，统一抛
        raise RuntimeError(f"瓦片下载失败: {e}") from e
    if not data:
        raise ValueError("空响应")
    return data


def download_and_merge(
    grid: TileGrid,
    source: dict,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> tuple[Image.Image, int]:
    """
    下载网格内所有瓦片并拼接，返回 (裁剪后的 RGB 图像, 缺失瓦片数)

    progress_cb(done, total, missing) 每完成若干瓦片调用一次
    """
    total = grid.tile_count
    mosaic = Image.new("RGB", (grid.cols * TILE_SIZE, grid.rows * TILE_SIZE), (0, 0, 0))
    referer = source.get("referer")
    workers = max(2, int(source.get("concurrency", 8)))

    done = 0
    missing = 0
    last_report = 0.0

    def worker(task):
        x, y, col, row = task
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        url = build_tile_url(source, x, y, grid.zoom)
        return col, row, fetch_tile(url, referer)

    tasks = list(grid.iter_tiles())
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, t): t for t in tasks}
        try:
            for fut in as_completed(futures):
                if cancel_event is not None and cancel_event.is_set():
                    for f in futures:
                        f.cancel()
                    raise CancelledError()
                try:
                    col, row, data = fut.result()
                    with Image.open(io.BytesIO(data)) as tile:
                        tile = tile.convert("RGB")
                        if tile.size != (TILE_SIZE, TILE_SIZE):
                            tile = tile.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)
                        mosaic.paste(tile, (col * TILE_SIZE, row * TILE_SIZE))
                except CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - 单瓦片失败不应中断整体
                    missing += 1
                    x, y, _c, _r = futures[fut]
                    log(f"[瓦片失败] z={grid.zoom} x={x} y={y}: {e}")
                finally:
                    done += 1

                now = time.time()
                if progress_cb and (now - last_report > 0.2 or done == total):
                    progress_cb(done, total, missing)
                    last_report = now
        except CancelledError:
            pool.shutdown(wait=False, cancel_futures=True)
            raise

    if missing >= total:
        raise RuntimeError("所有瓦片下载失败，请检查网络连接或代理设置")

    cropped = mosaic.crop(grid.crop_box)
    mosaic.close()
    log(f"[拼接完成] 输出 {cropped.width}x{cropped.height} 像素，缺失瓦片 {missing} 张")
    return cropped, missing
