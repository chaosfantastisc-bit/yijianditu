# -*- coding: utf-8 -*-
"""计时：estimate 耗时 / download 返回耗时 / 任务进度时间线（首包延迟）"""
import time, json, urllib.request

BASE = "http://127.0.0.1:17899"
BBOX = {"min_lon": 118.74, "min_lat": 32.02, "max_lon": 118.79, "max_lat": 32.07,
        "zoom": 17, "source": "tianditu_satellite", "target_crs": "epsg4527"}


def post(path, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        return time.time() - t, json.loads(r.read())


def get(path):
    t = time.time()
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return time.time() - t, json.loads(r.read())


print("=== 1) /api/estimate 耗时 ===")
dt, info = post("/api/estimate", BBOX)
print("  %.3fs  tiles=%s cols=%s rows=%s" % (dt, info["tile_count"], info["cols"], info["rows"]))

print("\n=== 2) /api/download 返回耗时 ===")
dt2, res = post("/api/download", {**BBOX,
    "output_dir": "C:/Users/Fantastic/Desktop/一键地图输出/__timing__", "name": "timing"})
print("  %.3fs  task_id=%s" % (dt2, res.get("task_id")))

print("\n=== 3) 任务进度时间线（看首包何时开始动）===")
tid = res["task_id"]
prev = None
start = time.time()
while True:
    tt, t = get("/api/task?id=" + tid)
    p = t.get("progress", 0)
    st = t.get("status")
    now = time.time() - start
    if prev is None or abs(p - prev) > 1.0 or st != "running":
        print("  +%.2fs  prog=%.1f  %s | %s" % (now, p, st, t.get("message")))
    prev = p
    if st != "running":
        print("  >>> 结束 +%.2fs %s" % (now, st))
        r = t.get("result") or {}
        if r:
            print("  像素=%s 左下=%s 右上=%s 缺图=%s" % (r.get("pixels"), r.get("ll"), r.get("ur"), r.get("missing_tiles")))
        break
    if now > 150:
        print("  >>> 超时"); break
    time.sleep(0.2)
