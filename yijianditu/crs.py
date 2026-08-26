# -*- coding: utf-8 -*-
"""
坐标系与重投影：Web Mercator (EPSG:3857) → 用户选定的 CGCS2000 目标坐标系

自动按 3°带分带识别下载区域所在带号，使用对应 CGCS2000 坐标系：
  - 加带号  : zone:N     (N=25~45，中央经线 3N，东偏 x_0 = N*1e6+500000)
  - 不加带号: cm:L       (L=75,78,...,135，东偏 x_0 = 500000)
  - 手动中央经线(不加带号): manual:LON  (任意十进制度，如 118.5)

目标坐标系标识字符串（传给 pipeline / 前端）：
  "zone:39"      -> 加带号第39带
  "cm:117"       -> 不加带号中央经线117°E
  "manual:118.5" -> 不加带号，手动中央经线118.5°E
兼容旧 "epsg4527"/"default" -> 当作 zone:39

分带规则（3°带高斯-克吕格）：
  带号 N 的中央经线 = 3N，经度范围 [3N-1.5, 3N+1.5]
  支持范围：带号 25~45（中央经线 75°E~135°E），有效经度 [73.5, 136.5]
  下载范围横跨多个度带时，以小编号（最西）为准
"""
from __future__ import annotations

import math
import time

import numpy as np
from pyproj import CRS, Transformer

SOURCE_EPSG = 3857  # 所有在线瓦片都是 Web Mercator

# ── 3°带分带范围 ─────────────────────────────────────────
ZONE_MIN, ZONE_MAX = 25, 45          # 带号范围
CM_MIN, CM_MAX = 75, 135            # 中央经线度数（不加带号模式）
LON_MIN = 3 * ZONE_MIN - 1.5        # 73.5  有效经度下界
LON_MAX = 3 * ZONE_MAX + 1.5        # 136.5 有效经度上界
DEFAULT_TARGET = "zone:39"

CROSS_WARN = "下载范围横跨多个度带时，以小编号（最西侧带）为准"


# ── 分带计算 ─────────────────────────────────────────────
def lon_to_zone(lon: float) -> int:
    """经度 → 3°带带号（中央经线 3N，范围 [3N-1.5, 3N+1.5]）"""
    return int(math.floor((lon + 1.5) / 3))


def resolve_zone(min_lon: float, max_lon: float):
    """给定区域经度范围，返回应使用的带号；超出支持范围返回 None。
    横跨多带时以小编号（最西）为准。"""
    if min_lon < LON_MIN or max_lon > LON_MAX:
        return None
    return min(lon_to_zone(min_lon), lon_to_zone(max_lon))


# ── CRS 构造 ─────────────────────────────────────────────
def _zone_crs(zone: int) -> CRS:
    lon0 = 3 * zone
    x0 = zone * 1_000_000 + 500_000  # 含带号东偏
    return CRS.from_proj4(
        f"+proj=tmerc +lat_0=0 +lon_0={lon0} +k=1 +x_0={x0} +y_0=0 "
        f"+ellps=GRS80 +units=m +no_defs +type=crs"
    )


def _cm_crs(lon0: float) -> CRS:
    return CRS.from_proj4(
        f"+proj=tmerc +lat_0=0 +lon_0={lon0} +k=1 +x_0=500000 +y_0=0 "
        f"+ellps=GRS80 +units=m +no_defs +type=crs"
    )


def zone_label(zone: int) -> str:
    return f"CGCS2000_3_Degree_GK_Zone_{zone}（中央经线 {3 * zone}°E，含带号）"


def cm_label(lon0: int) -> str:
    return f"CGCS2000_3_Degree_GK_CM_{lon0}E（中央经线 {lon0}°E）"


def manual_label(lon0: float) -> str:
    return f"CGCS2000_3_Degree_GK_CM_{lon0:.4f}E（手动中央经线）"


def parse_target(target_crs: str | None):
    """解析目标坐标系标识字符串 -> (CRS, 友好label, 标准化id)"""
    s = (target_crs or "").strip()
    if not s or s in ("epsg4527", "default"):
        return _zone_crs(39), zone_label(39), "zone:39"
    if s.startswith("zone:"):
        zone = int(s[len("zone:"):])
        if not (ZONE_MIN <= zone <= ZONE_MAX):
            raise ValueError(f"带号应在 {ZONE_MIN}~{ZONE_MAX} 之间")
        return _zone_crs(zone), zone_label(zone), s
    if s.startswith("cm:"):
        lon0 = float(s[len("cm:"):])
        if (abs(lon0 - round(lon0)) > 1e-9
                or not (CM_MIN <= round(lon0) <= CM_MAX)
                or (int(round(lon0)) - CM_MIN) % 3 != 0):
            raise ValueError(f"中央经线应为 3° 倍数且落在 {CM_MIN}~{CM_MAX}°E")
        lon0 = int(round(lon0))
        return _cm_crs(lon0), cm_label(lon0), f"cm:{lon0}"
    if s.startswith("manual:"):
        lon0 = float(s[len("manual:"):])
        if not (0 < lon0 < 180):
            raise ValueError("手动中央经线应在 0~180° 之间")
        return _cm_crs(lon0), manual_label(lon0), s
    raise ValueError(f"未知坐标系: {s}")


# ── 兼容旧接口（pipeline / server 使用）────────────────────
def get_target_crs(target_id=None) -> CRS:
    return parse_target(target_id)[0]


def get_target_label(target_id=None) -> str:
    return parse_target(target_id)[1]


def get_source_crs() -> CRS:
    return CRS.from_epsg(SOURCE_EPSG)


def lonlat_to_target(lon: float, lat: float, target_id=None) -> tuple[float, float]:
    """经纬度 → 目标平面坐标（米），用于精度校验与预估角点"""
    tr = Transformer.from_crs(CRS.from_epsg(4326), get_target_crs(target_id), always_xy=True)
    return tr.transform(lon, lat)


def reproject_mosaic(
    pil_image,
    merc_transform,
    target_id=None,
    resampling: str = "cubic",
    progress_cb=None,
) -> tuple[np.ndarray, object, tuple[float, float, float, float]]:
    """
    将墨卡托拼接影像重投影到指定目标坐标系

    参数:
        pil_image      : 裁剪后的 PIL RGB 影像
        merc_transform : 源影像的 affine 变换（EPSG:3857）
        target_id      : 目标坐标系标识字符串（zone:N / cm:L / manual:LON）
    返回:
        (data 数组 shape=(3,h,w), dst_transform, (min_x, min_y, max_x, max_y))
    """
    from rasterio.transform import array_bounds
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    src_crs = get_source_crs()
    dst_crs = get_target_crs(target_id)

    arr = np.asarray(pil_image.convert("RGB"), dtype=np.uint8)
    src_h, src_w = arr.shape[0], arr.shape[1]
    src_data = np.transpose(arr, (2, 0, 1))  # (3, h, w)

    left, bottom, right, top = array_bounds(src_h, src_w, merc_transform)

    dst_transform, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, src_w, src_h, left, bottom, right, top
    )

    method = getattr(Resampling, resampling, Resampling.lanczos)
    dst_data = np.zeros((3, dst_h, dst_w), dtype=np.uint8)
    last_report = {"t": 0.0}

    def _cb(pct, msg, aux):
        # rasterio 进度回调：pct ∈ [0,1]，映射到全局 57%~75%
        now = time.time()
        if progress_cb and (now - last_report["t"] > 0.25 or pct >= 1.0):
            last_report["t"] = now
            progress_cb(57.0 + 18.0 * pct, f"重投影 {int(pct * 100)}%")

    # 多波段一次性重投影（比逐波段循环更快，且能在过程中回报进度）
    reproject(
        source=src_data,
        destination=dst_data,
        src_transform=merc_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=method,
        callback=_cb,
    )

    bounds = array_bounds(dst_h, dst_w, dst_transform)  # (left, bottom, right, top)
    out_bounds = (
        round(bounds[0], 4), round(bounds[1], 4),
        round(bounds[2], 4), round(bounds[3], 4),
    )
    return dst_data, dst_transform, out_bounds


def build_merc_transform(res: float, left: float, top: float):
    """构造源墨卡托 affine 变换"""
    from affine import Affine

    return Affine(res, 0.0, left, 0.0, -res, top)
