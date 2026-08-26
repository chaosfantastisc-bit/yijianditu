# -*- coding: utf-8 -*-
"""
全局配置：图源定义、下载参数、安全阈值

图源 URL 模板取自 geo-downloader/src-tauri/src/config.rs
"""
from __future__ import annotations

import os
import sys

# ── 天地图公开 Token（与 geo-downloader 内置默认值一致，可用环境变量覆盖）──
# 运行时可由界面修改（set_tianditu_token），URL 模板用 {tk} 占位符，下载时注入。
# 用户填入的 Key 会持久化到本地文件，下次启动自动回填（记住上次输入）。
TIANDITU_TOKEN = os.environ.get("TIANDITU_TOKEN", "436ce7e50d27eede2f2929307e6b33c0")

# Key 持久化文件（用户主目录下隐藏文件，仅本机使用）
TIANDITU_TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".yijianditu_token")


def _load_token_file() -> None:
    """启动时读取上次保存的 Key，覆盖内置默认 Key（文件优先于内置）"""
    global TIANDITU_TOKEN
    try:
        if os.path.isfile(TIANDITU_TOKEN_FILE):
            with open(TIANDITU_TOKEN_FILE, "r", encoding="utf-8") as f:
                tok = (f.read() or "").strip()
            if tok:
                TIANDITU_TOKEN = tok
    except Exception:
        pass


def set_tianditu_token(token: str) -> None:
    """运行时覆盖天地图 Token（界面输入生效），并持久化到本地文件"""
    global TIANDITU_TOKEN
    token = (token or "").strip()
    if not token:
        return
    TIANDITU_TOKEN = token
    _save_token_file(token)


def _save_token_file(token: str) -> None:
    """将 Key 写入本地文件（明文，用户自有 Key，仅本机使用）"""
    try:
        with open(TIANDITU_TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(token)
    except Exception:
        pass


def current_tianditu_token() -> str:
    """当前生效的天地图 Token（供 /api/config 回显）"""
    return TIANDITU_TOKEN


_load_token_file()  # 模块加载即生效

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# ── 图源：仅保留两个影像源 ────────────────────────────────────────────────
SOURCES = {
    "tianditu_satellite": {
        "id": "tianditu_satellite",
        "name": "天地图 影像",
        "url": (
            "https://t{s}.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={tk}"
        ),
        "subdomains": ["0", "1", "2", "3", "4", "5", "6", "7"],
        "referer": "https://www.tianditu.gov.cn/",
        "min_zoom": 1,
        "max_zoom": 18,
        "concurrency": 16,
        "attribution": "© 天地图",
        # 预览用注记图层（仅地图预览叠加，不参与下载）
        "label_url": (
            "https://t{s}.tianditu.gov.cn/cia_w/wmts?SERVICE=WMTS&REQUEST=GetTile"
            "&VERSION=1.0.0&LAYER=cia&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles"
            "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={tk}"
        ),
    },
    "arcgis_satellite": {
        "id": "arcgis_satellite",
        "name": "ArcGIS 卫星",
        "url": (
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery"
            "/MapServer/tile/{z}/{y}/{x}"
        ),
        "subdomains": [],
        "referer": None,
        "min_zoom": 1,
        "max_zoom": 19,
        "concurrency": 8,
        "attribution": "© Esri",
        "label_url": None,
    },
}

DEFAULT_SOURCE = "arcgis_satellite"
DEFAULT_ZOOM = 17

# ── 下载参数 ──────────────────────────────────────────────────────────────
TILE_SIZE = 256
REQUEST_TIMEOUT = 10          # 单瓦片超时（秒）——过大易卡死整批进度
RETRY_TIMES = 2               # 单瓦片重试次数——少重试避免慢瓦片拖死
RETRY_BACKOFF = 0.3           # 重试退避基数（秒）

# ── 安全阈值：防止一次拉取过大区域把内存打爆 ───────────────────────────────
MAX_TILES = 12000             # 瓦片数上限
MAX_PIXELS = 260_000_000      # 拼接后像素总数上限（约 16000 x 16000）

# ── 输出 ──────────────────────────────────────────────────────────────────
JPG_MAX_MB = 30.0             # DXF 内嵌 JPG 体积上限


def default_output_dir() -> str:
    """默认输出目录：桌面/一键地图输出"""
    home = os.path.expanduser("~")
    for name in ("Desktop", "桌面", "OneDrive/Desktop"):
        cand = os.path.join(home, name)
        if os.path.isdir(cand):
            return os.path.join(cand, "一键地图输出")
    return os.path.join(home, "一键地图输出")


def log(msg: str) -> None:
    """
    安全日志：PyInstaller --noconsole 打包后 sys.stdout / sys.stderr 为 None，
    直接 print 会抛 AttributeError，务必静默兜底。
    """
    try:
        if sys.stdout is not None:
            sys.stdout.write(f"{msg}\n")
            sys.stdout.flush()
    except Exception:
        pass
