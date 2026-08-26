# -*- coding: utf-8 -*-
"""
主流水线：在线瓦片 → 拼接裁剪 → 重投影（EPSG:4527）→ JPG → DXF

阶段与进度权重：
    下载瓦片   0 ~ 55%
    重投影     55 ~ 75%
    JPG 压缩   75 ~ 90%
    DXF 生成   90 ~ 100%
中间 GeoTIFF 不落盘，最终只产出 1 个 DXF + 1 个 JPG。
"""
from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime

from .config import JPG_MAX_MB, SOURCES, log
from .crs import (
    DEFAULT_TARGET,
    build_merc_transform,
    get_target_label,
    lonlat_to_target,
    reproject_mosaic,
)
from .dxf import generate_dxf
from .image import array_to_pil_image, compress_to_jpg, image_pixel_size
from .mosaic import CancelledError, download_and_merge
from .tiles import TileGrid

_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+")

SOURCE_PREFIX = {"tianditu_satellite": "TDT", "arcgis_satellite": "ARC"}


def make_default_name(source_id: str, zoom: int) -> str:
    prefix = SOURCE_PREFIX.get(source_id, "IMG")
    return f"{prefix}_z{zoom}_{datetime.now().strftime('%m%d_%H%M%S')}"


def sanitize_name(name: str, source_id: str, zoom: int) -> str:
    """清洗文件名：去掉路径分隔符与特殊字符，空则回退默认名"""
    name = (name or "").strip()
    name = _SAFE_NAME.sub("_", name).strip("_")
    return name or make_default_name(source_id, zoom)


def run(
    min_lon: float,
    min_lat: float,
    max_lon: float,
    max_lat: float,
    zoom: int,
    source_id: str,
    output_dir: str,
    name: str | None = None,
    target_crs: str | None = None,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """
    执行完整流程，返回结果字典：
        {success, jpg_path, dxf_path, ll, ur, size_m, pixels, missing_tiles, target_crs, elapsed, error}
    """
    if not target_crs:
        target_crs = DEFAULT_TARGET
    t_start = time.time()
    result = {
        "success": False,
        "jpg_path": None,
        "dxf_path": None,
        "ll": None,
        "ur": None,
        "size_m": None,
        "pixels": None,
        "missing_tiles": 0,
        "target_crs": get_target_label(target_crs),
        "elapsed": 0.0,
        "error": None,
    }

    def prog(pct: float, msg: str):
        if progress_cb:
            progress_cb(round(pct, 1), msg)
        else:
            log(f"[{pct:5.1f}%] {msg}")

    try:
        source = SOURCES.get(source_id)
        if source is None:
            raise ValueError(f"未知图源: {source_id}")

        zoom = int(zoom)
        if not (source["min_zoom"] <= zoom <= source["max_zoom"]):
            raise ValueError(
                f"{source['name']} 支持的级别范围为 "
                f"{source['min_zoom']}~{source['max_zoom']}，当前 {zoom}"
            )

        grid = TileGrid(min_lon, min_lat, max_lon, max_lat, zoom)
        limit_err = grid.check_limits()
        if limit_err:
            raise ValueError(limit_err)

        os.makedirs(output_dir, exist_ok=True)
        stem = sanitize_name(name, source_id, zoom)
        jpg_path = os.path.join(output_dir, f"{stem}.jpg")
        dxf_path = os.path.join(output_dir, f"{stem}.dxf")

        est = grid.estimate()
        prog(
            1,
            f"准备下载 {est['tile_count']} 张瓦片（{est['cols']}×{est['rows']}），"
            f"输出 {est['width']}×{est['height']} 像素，"
            f"地面分辨率约 {est['ground_resolution']} m/px",
        )

        # ── 1. 下载 + 拼接 + 裁剪 ──
        prog(1, f"开始下载 {est['tile_count']} 张瓦片（连接复用，首批稍慢）…")
        def tile_progress(done, total, missing):
            pct = 1 + 54.0 * done / max(1, total)
            extra = f"，失败 {missing}" if missing else ""
            prog(pct, f"下载瓦片 {done}/{total}{extra}")

        merged, missing = download_and_merge(
            grid, source, progress_cb=tile_progress, cancel_event=cancel_event
        )
        result["missing_tiles"] = missing

        # ── 2. 重投影到目标坐标系 ──
        prog(57, f"重投影：Web Mercator → {get_target_label(target_crs)}")
        merc_transform = build_merc_transform(*grid.transform_args)
        # 心跳：重投影期间进度条持续小幅推进，消除“卡在 57%”的错觉
        _done_evt = threading.Event()

        def _reproj_heartbeat():
            step = 0
            while not _done_evt.is_set():
                step += 1
                prog(57 + min(16.0, step * 0.8), "重投影中…")
                if _done_evt.wait(0.3):
                    break

        _hb = threading.Thread(target=_reproj_heartbeat, daemon=True)
        _hb.start()
        try:
            data, _dst_transform, bounds = reproject_mosaic(
                merged, merc_transform, target_id=target_crs, progress_cb=prog)
        finally:
            _done_evt.set()
            _hb.join(timeout=1)
        merged.close()

        ll = (bounds[0], bounds[1])
        ur = (bounds[2], bounds[3])
        result["ll"], result["ur"] = ll, ur
        result["size_m"] = (round(ur[0] - ll[0], 2), round(ur[1] - ll[1], 2))
        result["pixels"] = (int(data.shape[2]), int(data.shape[1]))
        prog(
            75,
            f"重投影完成 {data.shape[2]}×{data.shape[1]} 像素 | "
            f"左下 ({ll[0]:.3f}, {ll[1]:.3f}) 右上 ({ur[0]:.3f}, {ur[1]:.3f})",
        )

        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()

        # ── 3. JPG 压缩 ──
        prog(78, f"生成 JPG（上限 {JPG_MAX_MB:.0f}MB）")
        pil_img = array_to_pil_image(data)
        del data
        compress_to_jpg(pil_img, jpg_path, max_size_mb=JPG_MAX_MB)
        pil_img.close()
        jpg_mb = os.path.getsize(jpg_path) / 1024 / 1024
        jpg_w, jpg_h = image_pixel_size(jpg_path)
        result["jpg_path"] = jpg_path
        prog(90, f"JPG 完成：{jpg_w}×{jpg_h}，{jpg_mb:.2f}MB")

        # ── 4. DXF 生成（边界矩形 + 内嵌影像）──
        prog(92, "生成 DXF")
        generate_dxf(
            ll_x=ll[0], ll_y=ll[1], ur_x=ur[0], ur_y=ur[1],
            jpg_path=jpg_path,
            output_dxf_path=dxf_path,
            image_width_px=jpg_w,
            image_height_px=jpg_h,
        )
        result["dxf_path"] = dxf_path

        result["elapsed"] = round(time.time() - t_start, 1)
        result["success"] = True
        prog(100, f"完成，耗时 {result['elapsed']}s")

    except CancelledError:
        result["error"] = "已取消"
        prog(0, "任务已取消")
    except MemoryError:
        result["error"] = "内存不足：请降低级别或缩小选区范围"
        prog(0, result["error"])
    except Exception as e:  # noqa: BLE001 - 顶层兜底，错误回传前端
        import traceback

        result["error"] = str(e) or e.__class__.__name__
        log(traceback.format_exc())
        prog(0, f"失败：{result['error']}")

    return result


def estimate(min_lon, min_lat, max_lon, max_lat, zoom, source_id, target_crs=None) -> dict:
    """供前端实时预估规模，并做超限校验"""
    if not target_crs:
        target_crs = DEFAULT_TARGET
    source = SOURCES.get(source_id) or SOURCES[next(iter(SOURCES))]
    zoom = max(source["min_zoom"], min(source["max_zoom"], int(zoom)))
    grid = TileGrid(min_lon, min_lat, max_lon, max_lat, zoom)
    info = grid.estimate()
    info["limit_error"] = grid.check_limits()
    # 选区在目标坐标系下的角点，便于用户提前核对
    ll = lonlat_to_target(min_lon, min_lat, target_crs)
    ur = lonlat_to_target(max_lon, max_lat, target_crs)
    info["target_ll"] = [round(ll[0], 3), round(ll[1], 3)]
    info["target_ur"] = [round(ur[0], 3), round(ur[1], 3)]
    return info
