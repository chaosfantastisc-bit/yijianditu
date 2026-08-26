# -*- coding: utf-8 -*-
"""验证目标坐标系选择：config 列表、estimate 角点、epsg4527 真实下载 -> DXF 坐标"""
import json, os, shutil, time, urllib.request, urllib.error
import ezdxf

BASE = "http://127.0.0.1:17890"
OUT = os.path.join(os.path.expanduser("~"), "Desktop", "一键地图输出", "__crstest__")

def get(p):
    with urllib.request.urlopen(BASE + p, timeout=30) as r:
        return json.loads(r.read())

def post(p, obj):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(BASE + p, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())

# 1) config
cfg = get("/api/config")
print("default_target =", cfg["default_target"])
print("target_crs ids =", [c["id"] for c in cfg["target_crs"]])
assert cfg["default_target"] == "epsg4527"
assert {c["id"] for c in cfg["target_crs"]} == {"epsg4527"}

# 2) estimate for 4527
box = {"min_lon":118.77,"min_lat":32.05,"max_lon":118.80,"max_lat":32.07,"zoom":17,"source":"tianditu_satellite"}
e4527 = post("/api/estimate", {**box, "target_crs":"epsg4527"})
print("4527   ll =", e4527["target_ll"], "ur =", e4527["target_ur"])
assert e4527["target_ll"][0] > 39_000_000  # 4527 带号 39 -> 39xxx000

# 3) real download in EPSG:4527
shutil.rmtree(OUT, ignore_errors=True)
dl = {**box, "output_dir": OUT, "name":"crstest_4527", "target_crs":"epsg4527"}
tid = post("/api/download", dl)["task_id"]
for _ in range(60):
    time.sleep(0.5)
    t = get(f"/api/task?id={tid}")
    if t["status"] != "running":
        break
print("4527 download status =", t["status"], "progress =", t["progress"])
assert t["status"] == "done", t
r = t["result"]
print("result.target_crs =", r["target_crs"])
print("result.ll =", r["ll"], "ur =", r["ur"])
print("dxf =", r["dxf_path"], "exists?", os.path.exists(r["dxf_path"]))
print("jpg =", r["jpg_path"], "exists?", os.path.exists(r["jpg_path"]))
assert os.path.exists(r["dxf_path"]) and os.path.exists(r["jpg_path"])
assert r["ll"][0] > 39_000_000  # 4527 坐标

# DXF image path is full path
doc = ezdxf.readfile(r["dxf_path"])
paths = [d.dxf.filename for d in doc.objects.query("IMAGEDEF")]
print("DXF image path =", paths)
assert paths and os.path.isabs(paths[0]) and os.path.exists(paths[0])

shutil.rmtree(OUT, ignore_errors=True)
print("ALL CRS CHECKS PASSED")
