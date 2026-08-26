# -*- coding: utf-8 -*-
"""
Web Mercator 瓦片数学（EPSG:3857，标准 XYZ 编号，原点左上）

坐标链路：经纬度 → 墨卡托米 → 全局像素 → 瓦片索引
所有下载的瓦片都在 EPSG:3857 下，这是后续重投影到目标坐标系（默认 EPSG:4527）的唯一源坐标系。
"""
from __future__ import annotations

import math

from .config import MAX_PIXELS, MAX_TILES, TILE_SIZE

EARTH_RADIUS = 6378137.0
ORIGIN_SHIFT = math.pi * EARTH_RADIUS          # 20037508.342789244
MAX_LAT = 85.05112877980659                     # 墨卡托纬度极限


def clamp_lat(lat: float) -> float:
    return max(-MAX_LAT, min(MAX_LAT, lat))


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    """经纬度 → Web Mercator 米"""
    lat = clamp_lat(lat)
    x = math.radians(lon) * EARTH_RADIUS
    y = math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0)) * EARTH_RADIUS
    return x, y


def merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    """Web Mercator 米 → 经纬度"""
    lon = math.degrees(x / EARTH_RADIUS)
    lat = math.degrees(2.0 * math.atan(math.exp(y / EARTH_RADIUS)) - math.pi / 2.0)
    return lon, lat


def resolution(zoom: int) -> float:
    """该级别下每像素代表的墨卡托米数"""
    return (2.0 * ORIGIN_SHIFT) / (TILE_SIZE * (2 ** zoom))


def ground_resolution(zoom: int, lat: float) -> float:
    """该级别在指定纬度的真实地面分辨率（米/像素）"""
    return resolution(zoom) * math.cos(math.radians(clamp_lat(lat)))


def merc_to_pixel(x: float, y: float, zoom: int) -> tuple[float, float]:
    """墨卡托米 → 全局像素坐标（原点左上，向右向下为正）"""
    res = resolution(zoom)
    return (x + ORIGIN_SHIFT) / res, (ORIGIN_SHIFT - y) / res


def pixel_to_merc(px: float, py: float, zoom: int) -> tuple[float, float]:
    """全局像素坐标 → 墨卡托米"""
    res = resolution(zoom)
    return px * res - ORIGIN_SHIFT, ORIGIN_SHIFT - py * res


class TileGrid:
    """
    某个经纬度矩形 + 级别下的瓦片网格计算结果。

    属性：
        zoom                     级别
        tx0, ty0, tx1, ty1       瓦片索引闭区间
        cols, rows, tile_count   网格规模
        crop_box                 在拼接大图上的精确裁剪框 (left, top, right, bottom)
        width, height            裁剪后输出像素尺寸
        merc_bounds              裁剪后精确墨卡托范围 (min_x, min_y, max_x, max_y)
        transform_args           (res, left, top) 供构造 Affine 使用
    """

    def __init__(self, min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int):
        if max_lon <= min_lon or max_lat <= min_lat:
            raise ValueError("范围无效：右上角必须大于左下角")

        self.zoom = int(zoom)
        self.min_lon, self.min_lat = float(min_lon), float(min_lat)
        self.max_lon, self.max_lat = float(max_lon), float(max_lat)

        min_x, min_y = lonlat_to_merc(min_lon, min_lat)
        max_x, max_y = lonlat_to_merc(max_lon, max_lat)

        # 像素坐标：左上 = (px_left, py_top)，右下 = (px_right, py_bottom)
        px_left, py_bottom = merc_to_pixel(min_x, min_y, self.zoom)
        px_right, py_top = merc_to_pixel(max_x, max_y, self.zoom)

        # 输出像素尺寸按四舍五入取整，保证至少 1 像素
        self.width = max(1, int(round(px_right - px_left)))
        self.height = max(1, int(round(py_bottom - py_top)))

        # 对齐到整数像素后反算精确范围，确保 transform 与像素严格自洽
        px_left_i = int(math.floor(px_left))
        py_top_i = int(math.floor(py_top))
        # 允许亚像素偏移，用整数像素起点 + 尺寸描述输出窗口
        self.px_left = px_left_i
        self.py_top = py_top_i

        self.tx0 = px_left_i // TILE_SIZE
        self.ty0 = py_top_i // TILE_SIZE
        self.tx1 = (px_left_i + self.width - 1) // TILE_SIZE
        self.ty1 = (py_top_i + self.height - 1) // TILE_SIZE

        max_index = 2 ** self.zoom - 1
        self.tx0 = max(0, min(max_index, self.tx0))
        self.ty0 = max(0, min(max_index, self.ty0))
        self.tx1 = max(self.tx0, min(max_index, self.tx1))
        self.ty1 = max(self.ty0, min(max_index, self.ty1))

        self.cols = self.tx1 - self.tx0 + 1
        self.rows = self.ty1 - self.ty0 + 1
        self.tile_count = self.cols * self.rows

        # 拼接大图上的裁剪框
        off_x = px_left_i - self.tx0 * TILE_SIZE
        off_y = py_top_i - self.ty0 * TILE_SIZE
        self.crop_box = (off_x, off_y, off_x + self.width, off_y + self.height)

        res = resolution(self.zoom)
        left = px_left_i * res - ORIGIN_SHIFT
        top = ORIGIN_SHIFT - py_top_i * res
        self.merc_bounds = (left, top - self.height * res, left + self.width * res, top)
        self.transform_args = (res, left, top)

    # ── 规模与体检 ────────────────────────────────────────────────────────
    @property
    def pixel_count(self) -> int:
        return self.width * self.height

    @property
    def mosaic_pixel_count(self) -> int:
        return self.cols * TILE_SIZE * self.rows * TILE_SIZE

    def estimate(self) -> dict:
        """返回规模预估，供前端展示与后端拦截"""
        center_lat = (self.min_lat + self.max_lat) / 2.0
        return {
            "zoom": self.zoom,
            "tile_count": self.tile_count,
            "cols": self.cols,
            "rows": self.rows,
            "width": self.width,
            "height": self.height,
            "pixel_count": self.pixel_count,
            "ground_resolution": round(ground_resolution(self.zoom, center_lat), 4),
            "est_download_mb": round(self.tile_count * 22 / 1024.0, 1),
            "est_memory_mb": round(self.mosaic_pixel_count * 3 / 1024.0 / 1024.0, 1),
        }

    def check_limits(self) -> str | None:
        """超限返回中文错误说明，正常返回 None"""
        if self.tile_count > MAX_TILES:
            return (
                f"选区过大：需要 {self.tile_count} 张瓦片（上限 {MAX_TILES}），"
                f"请降低级别或缩小范围"
            )
        if self.mosaic_pixel_count > MAX_PIXELS:
            return (
                f"选区过大：拼接后约 {self.cols * TILE_SIZE}×{self.rows * TILE_SIZE} 像素"
                f"（上限 {MAX_PIXELS // 1000000} 百万），请降低级别或缩小范围"
            )
        return None

    def iter_tiles(self):
        """按行优先遍历 (x, y, col, row)"""
        for row, y in enumerate(range(self.ty0, self.ty1 + 1)):
            for col, x in enumerate(range(self.tx0, self.tx1 + 1)):
                yield x, y, col, row

    def __repr__(self) -> str:
        return (
            f"<TileGrid z={self.zoom} tiles={self.cols}x{self.rows}={self.tile_count} "
            f"out={self.width}x{self.height}>"
        )
