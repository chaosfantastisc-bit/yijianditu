# -*- coding: utf-8 -*-
"""HTTP 层自测：静态资源、config、estimate、tile 代理、download 任务全流程"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 17877}"
fails = []


def check(cond, label, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {label} {detail}")
    if not cond:
        fails.append(label)


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=40) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def post(path, obj):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


print("=" * 66)
print("1) 静态资源")
for p, key in [("/", b"<title>"), ("/static/app.js", b"applyBBox"),
               ("/static/app.css", b"--accent"), ("/static/vendor/leaflet.js", b"Leaflet")]:
    st, body, ct = get(p)
    check(st == 200 and key in body, f"GET {p}", f"{st}, {len(body)}B, {ct.split(';')[0]}")

st, body, _ = get("/static/../server.py")
check(st == 404, "静态目录穿越被拦截", f"{st}")

print("=" * 66)
print("2) /api/config")
st, body, _ = get("/api/config")
cfg = json.loads(body)
print("   ", {k: v for k, v in cfg.items() if k != "sources"})
check(st == 200, "config 200")
check(len(cfg["sources"]) == 2, "仅两个影像源", [s["name"] for s in cfg["sources"]])
check("118.5" in cfg["target_crs"], "目标坐标系为 118.5")
check(cfg["default_source"] == "tianditu_satellite", "默认天地图影像")

print("=" * 66)
print("3) 瓦片代理（含缓存命中）")
for src in ("tianditu_satellite", "arcgis_satellite"):
    t0 = time.time()
    st, body, ct = get(f"/api/tile?src={src}&layer=base&z=13&x=6799&y=3325")
    ms1 = (time.time() - t0) * 1000
    t0 = time.time()
    st2, body2, _ = get(f"/api/tile?src={src}&layer=base&z=13&x=6799&y=3325")
    ms2 = (time.time() - t0) * 1000
    ok = st == 200 and len(body) > 500 and body[:2] in (b"\xff\xd8", b"\x89P")
    check(ok, f"{src} 瓦片代理", f"{len(body)}B {ct} 首次{ms1:.0f}ms 缓存{ms2:.0f}ms")
    check(body == body2 and ms2 < max(60, ms1), f"{src} 缓存生效")

st, body, ct = get("/api/tile?src=tianditu_satellite&layer=label&z=13&x=6799&y=3325")
check(st == 200 and len(body) > 100, "天地图注记图层代理", f"{len(body)}B {ct}")

print("=" * 66)
print("4) /api/estimate")
bbox = {"min_lon": 118.78, "min_lat": 32.05, "max_lon": 118.79, "max_lat": 32.058}
st, info = post("/api/estimate", dict(bbox, zoom=17, source="tianditu_satellite"))
print("   ", info)
check(st == 200 and info["tile_count"] > 0, "预估正常")
check(info["limit_error"] is None, "小范围不触发限制")
check(abs(info["target_ll"][0] - 526443.758) < 1, "预估角点与理论一致", info["target_ll"])

st, big = post("/api/estimate", {"min_lon": 116, "min_lat": 30, "max_lon": 121, "max_lat": 34,
                                 "zoom": 18, "source": "tianditu_satellite"})
check(bool(big.get("limit_error")), "超大范围被拦截", (big.get("limit_error") or "")[:40])

st, bad = post("/api/estimate", dict(bbox, zoom=17, source="tianditu_satellite", min_lon=999))
check(st == 200 or "error" in bad, "非法范围有错误返回")

print("=" * 66)
print("5) 完整下载任务（ArcGIS 卫星，z=16）")
outdir = os.path.join(os.environ.get("TEMP", "/tmp"), "yijianditu_apitest")
st, res = post("/api/download", dict(bbox, zoom=16, source="arcgis_satellite",
                                     output_dir=outdir, name="api_test_arc"))
check(st == 200 and "task_id" in res, "任务已创建", res)
tid = res["task_id"]

final = None
for _ in range(150):
    time.sleep(0.5)
    st, body, _ = get("/api/task?id=" + tid)
    t = json.loads(body)
    if t["status"] != "running":
        final = t
        break
check(final is not None, "任务在限时内结束")
if final:
    print(f"    状态={final['status']} 进度={final['progress']}")
    for line in final["logs"][-6:]:
        print("    ", line)
    check(final["status"] == "done", "任务成功", final.get("message"))
    r = final.get("result") or {}
    if r.get("success"):
        check(os.path.isfile(r["jpg_path"]), "JPG 落盘", os.path.basename(r["jpg_path"]))
        check(os.path.isfile(r["dxf_path"]), "DXF 落盘", os.path.basename(r["dxf_path"]))
        check(r["dxf_path"].endswith(".dxf") and r["jpg_path"].endswith(".jpg"), "输出恰为 dxf+jpg")
        check(len(os.listdir(outdir)) == 2, "输出目录仅 2 个文件", os.listdir(outdir))
        check(r["missing_tiles"] == 0, "无瓦片缺失")
        check(abs(r["ll"][0] - 526443.758) < 5, "左下角坐标符合 118.5 理论值", r["ll"])

print("=" * 66)
st, body, _ = get("/api/task?id=notexist")
check(st in (404, 200), "查询不存在任务不崩溃")

print("=" * 66)
if fails:
    print(f"结论: {len(fails)} 项失败 -> {fails}")
    sys.exit(1)
print("结论: HTTP 层全部通过")
