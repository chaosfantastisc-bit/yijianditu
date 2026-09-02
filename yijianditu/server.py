# -*- coding: utf-8 -*-
"""
本地 HTTP 服务：静态页面 + JSON API + 瓦片代理

仅绑定 127.0.0.1。瓦片预览走本地代理转发，规避浏览器 Referer / CORS 限制，
并顺手做一层内存缓存减少重复请求。
"""
from __future__ import annotations

import json
import mimetypes
import os
import socket
import threading
import time
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, pipeline
from .config import (
    DEFAULT_SOURCE,
    DEFAULT_ZOOM,
    JPG_MAX_MB,
    MAX_PIXELS,
    MAX_TILES,
    SOURCES,
    current_tianditu_token,
    default_output_dir,
    log,
    set_tianditu_token,
)
from .crs import (
    CM_MAX,
    CM_MIN,
    CROSS_WARN,
    DEFAULT_TARGET,
    ZONE_MAX,
    ZONE_MIN,
    cm_label,
    manual_label,
    resolve_zone,
    zone_label,
)
from .mosaic import build_tile_url, fetch_tile

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# ── 任务表 ────────────────────────────────────────────────────────────────
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# ── 预览瓦片缓存（简单 LRU）────────────────────────────────────────────────
_tile_cache: OrderedDict[str, bytes] = OrderedDict()
_tile_cache_lock = threading.Lock()
_TILE_CACHE_MAX = 800

# ── 客户端活动跟踪（用于「关闭网页即退出」，避免 exe 后台残留）────────────
_last_activity = None       # 最近一次客户端请求时间（monotonic，秒）
_has_client = False         # 是否曾收到客户端请求
_close_requested = False    # 网页主动要求退出
_IDLE_TIMEOUT = 20.0        # 网页无活动多久后自动退出（秒）


def _touch() -> None:
    global _last_activity, _has_client
    _last_activity = time.monotonic()
    _has_client = True


def _has_running_task() -> bool:
    with _tasks_lock:
        return any(t.get("status") == "running" for t in _tasks.values())


def _watchdog(httpd) -> None:
    """看门狗：网页关闭或无活动超时后退出服务，防止后台进程残留。"""
    while True:
        time.sleep(2)
        if _close_requested:
            log("[服务] 收到网页关闭信号，退出")
            httpd.shutdown()
            return
        if _has_client and _last_activity is not None:
            idle = time.monotonic() - _last_activity
            if idle > _IDLE_TIMEOUT and not _has_running_task():
                log(f"[服务] 网页无活动 {idle:.0f}s，自动退出")
                httpd.shutdown()
                return


def _cache_get(key: str):
    with _tile_cache_lock:
        if key in _tile_cache:
            _tile_cache.move_to_end(key)
            return _tile_cache[key]
    return None


def _cache_put(key: str, data: bytes):
    with _tile_cache_lock:
        _tile_cache[key] = data
        _tile_cache.move_to_end(key)
        while len(_tile_cache) > _TILE_CACHE_MAX:
            _tile_cache.popitem(last=False)


def _new_task() -> str:
    tid = uuid.uuid4().hex[:12]
    with _tasks_lock:
        _tasks[tid] = {
            "id": tid,
            "status": "running",     # running | done | error | cancelled
            "progress": 0.0,
            "message": "任务已创建",
            "logs": [],
            "result": None,
            "cancel": threading.Event(),
        }
    return tid


def _update_task(tid: str, **kw):
    with _tasks_lock:
        task = _tasks.get(tid)
        if not task:
            return
        msg = kw.get("message")
        if msg and (not task["logs"] or task["logs"][-1] != msg):
            task["logs"].append(msg)
            del task["logs"][:-200]
        task.update({k: v for k, v in kw.items() if k != "cancel"})


def _task_view(tid: str):
    with _tasks_lock:
        task = _tasks.get(tid)
        if not task:
            return None
        return {
            "id": task["id"],
            "status": task["status"],
            "progress": task["progress"],
            "message": task["message"],
            "logs": task["logs"][-40:],
            "result": task["result"],
        }


def _run_task(tid: str, params: dict):
    with _tasks_lock:
        cancel = _tasks[tid]["cancel"]

    def prog(pct, msg):
        _update_task(tid, progress=pct, message=msg)

    res = pipeline.run(
        params["min_lon"], params["min_lat"], params["max_lon"], params["max_lat"],
        params["zoom"], params["source"], params["output_dir"],
        name=params.get("name"), target_crs=params.get("target_crs"),
        progress_cb=prog, cancel_event=cancel,
    )
    if res["success"]:
        _update_task(tid, status="done", progress=100.0, result=res, message="全部完成")
    elif res.get("error") == "已取消":
        _update_task(tid, status="cancelled", result=res, message="任务已取消")
    else:
        _update_task(tid, status="error", result=res, message=f"失败：{res.get('error')}")


class Handler(BaseHTTPRequestHandler):
    server_version = f"yijianditu/{__version__}"

    # 关键：PyInstaller --noconsole 下 sys.stderr 为 None，
    # 默认 log_message 会往 stderr 写导致整个请求线程崩溃，必须静默。
    def log_message(self, fmt, *args):
        return

    def log_error(self, fmt, *args):
        return

    # ── 响应工具 ──────────────────────────────────────────────────────────
    def _send(self, code: int, body: bytes, ctype: str, cache: str | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", "no-store")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    # ── 路由 ──────────────────────────────────────────────────────────────
    def do_GET(self):
        _touch()
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            return self._serve_static("index.html")
        if path.startswith("/static/"):
            return self._serve_static(path[len("/static/"):])
        if path == "/api/config":
            return self._api_config()
        if path == "/api/ping":
            return self._api_ping()
        if path == "/api/task":
            tid = (query.get("id") or [""])[0]
            view = _task_view(tid)
            return self._json(view or {"error": "任务不存在"}, 200 if view else 404)
        if path == "/api/tile":
            return self._api_tile(query)
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        _touch()
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/estimate":
            return self._api_estimate(body)
        if path == "/api/download":
            return self._api_download(body)
        if path == "/api/cancel":
            return self._api_cancel(body)
        if path == "/api/open":
            return self._api_open(body)
        if path == "/api/close":
            return self._api_close()
        return self._json({"error": "not found"}, 404)

    # ── 静态文件 ──────────────────────────────────────────────────────────
    def _serve_static(self, rel: str):
        rel = rel.replace("\\", "/").lstrip("/")
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            data = f.read()
        self._send(200, data, ctype, "no-cache")

    # ── API ───────────────────────────────────────────────────────────────
    def _api_config(self):
        sources = [
            {
                "id": s["id"],
                "name": s["name"],
                "min_zoom": s["min_zoom"],
                "max_zoom": s["max_zoom"],
                "attribution": s["attribution"],
                "has_label": bool(s.get("label_url")),
                # 子域列表：天地图用数字 0~7（t0~t7），ArcGIS 无子域。
                # 必须下发，前端据此传给 Leaflet，否则 Leaflet 默认用 a/b/c
                # 字母子域，t{s} 会变成 ta/tb/tc，天地图不认 → 瓦片全黑。
                "subdomains": s.get("subdomains", []),
            }
            for s in SOURCES.values()
        ]
        self._json({
            "version": __version__,
            "sources": sources,
            "default_source": DEFAULT_SOURCE,
            "default_zoom": DEFAULT_ZOOM,
            "output_dir": default_output_dir(),
            "default_target": DEFAULT_TARGET,
            "crs_default_mode": "zone",
            "crs_zone_range": [ZONE_MIN, ZONE_MAX],
            "crs_cm_range": [CM_MIN, CM_MAX],
            "crs_note": CROSS_WARN,
            "tianditu_token": current_tianditu_token(),
            "limits": {"max_tiles": MAX_TILES, "max_pixels": MAX_PIXELS, "jpg_max_mb": JPG_MAX_MB},
        })

    def _api_tile(self, query):
        try:
            src_id = (query.get("src") or [DEFAULT_SOURCE])[0]
            layer = (query.get("layer") or ["base"])[0]
            z = int((query.get("z") or ["0"])[0])
            x = int((query.get("x") or ["0"])[0])
            y = int((query.get("y") or ["0"])[0])
        except (TypeError, ValueError):
            return self._json({"error": "参数错误"}, 400)

        source = SOURCES.get(src_id)
        if source is None:
            return self._json({"error": "未知图源"}, 404)

        if layer == "label":
            label_url = source.get("label_url")
            if not label_url:
                return self._send(404, b"", "text/plain")
            src = dict(source, url=label_url)
        else:
            src = source

        key = f"{src_id}:{layer}:{z}:{x}:{y}"
        cached = _cache_get(key)
        if cached is not None:
            return self._send(200, cached, "image/jpeg", "max-age=86400")

        try:
            data = fetch_tile(build_tile_url(src, x, y, z), src.get("referer"))
        except Exception:
            return self._send(404, b"", "text/plain")
        _cache_put(key, data)
        ctype = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
        self._send(200, data, ctype, "max-age=86400")

    def _build_target(self, body):
        """根据 crs_mode + 经度范围 + 手动中央经线，构造目标坐标系标识字符串。

        返回 (target_id, label, zone) 或抛出 ValueError（含范围超出）。"""
        min_lon = float(body["min_lon"])
        max_lon = float(body["max_lon"])
        mode = str(body.get("crs_mode") or "zone")
        manual = body.get("manual_meridian")

        zone = resolve_zone(min_lon, max_lon)
        if zone is None:
            raise ValueError(
                f"选择区域超出范围（仅支持 CGCS2000 3°带 第{ZONE_MIN}~{ZONE_MAX}带 / "
                f"中央经线 {CM_MIN}°E~{CM_MAX}°E）"
            )
        if mode == "zone":
            tid = f"zone:{zone}"
            label = zone_label(zone)
        elif mode == "cm":
            tid = f"cm:{3 * zone}"
            label = cm_label(3 * zone)
        elif mode == "manual":
            if manual is None:
                raise ValueError("未提供手动中央子午线")
            tid = f"manual:{float(manual)}"
            label = manual_label(float(manual))
        else:
            raise ValueError(f"未知坐标系模式: {mode}")
        return tid, label, zone

    def _api_estimate(self, body):
        try:
            if body.get("tianditu_token") is not None:
                set_tianditu_token(str(body["tianditu_token"]))
            target_id, label, zone = self._build_target(body)
            info = pipeline.estimate(
                float(body["min_lon"]), float(body["min_lat"]),
                float(body["max_lon"]), float(body["max_lat"]),
                int(body["zoom"]), str(body.get("source") or DEFAULT_SOURCE),
                target_crs=target_id,
            )
        except KeyError as e:
            return self._json({"error": f"缺少参数: {e}"}, 400)
        except ValueError as e:
            return self._json({"error": str(e), "out_of_range": True}, 400)
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        info["target_id"] = target_id
        info["target_label"] = label
        info["zone"] = zone
        info["meridian"] = 3 * zone
        self._json(info)

    def _api_download(self, body):
        try:
            if body.get("tianditu_token") is not None:
                set_tianditu_token(str(body["tianditu_token"]))
            target_id, _label, _zone = self._build_target(body)
            params = {
                "min_lon": float(body["min_lon"]),
                "min_lat": float(body["min_lat"]),
                "max_lon": float(body["max_lon"]),
                "max_lat": float(body["max_lat"]),
                "zoom": int(body["zoom"]),
                "source": str(body.get("source") or DEFAULT_SOURCE),
                "output_dir": str(body.get("output_dir") or default_output_dir()),
                "name": body.get("name") or None,
                "target_crs": target_id,
            }
        except (KeyError, TypeError, ValueError) as e:
            return self._json({"error": str(e), "out_of_range": True}, 400)

        try:
            os.makedirs(params["output_dir"], exist_ok=True)
        except OSError as e:
            return self._json({"error": f"输出目录不可用: {e}"}, 400)

        tid = _new_task()
        threading.Thread(target=_run_task, args=(tid, params), daemon=True).start()
        self._json({"task_id": tid})

    def _api_cancel(self, body):
        tid = str(body.get("id") or "")
        with _tasks_lock:
            task = _tasks.get(tid)
            if task:
                task["cancel"].set()
        self._json({"ok": bool(tid)})

    def _api_open(self, body):
        target = str(body.get("path") or "")
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        if not os.path.isdir(folder):
            return self._json({"error": "目录不存在"}, 400)
        try:
            os.startfile(folder)  # noqa: S606 - Windows 资源管理器
        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 500)
        self._json({"ok": True})

    def _api_ping(self):
        self._json({"ok": True})

    def _api_close(self):
        global _close_requested
        _close_requested = True
        self._json({"ok": True})


def find_free_port(start: int = 17800, tries: int = 40) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口")


def serve(port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    port = port or find_free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=_watchdog, args=(httpd,), daemon=True).start()
    log(f"[服务] http://127.0.0.1:{port}")
    return httpd, port
