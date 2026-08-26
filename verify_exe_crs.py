# -*- coding: utf-8 -*-
"""验证 dist/yijianditu.exe 实产出 EPSG:4527（默认下载）。端口从 17800 起探测。"""
import json, os, time, tempfile, shutil, urllib.request, ezdxf

def find_base():
    for port in range(17800, 17820):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return f"http://127.0.0.1:{port}"
        except Exception:
            continue
    return None

BASE = find_base()
if not BASE:
    print("ERROR: exe 服务未起来"); raise SystemExit(1)
print("服务地址:", BASE)

cfg = json.loads(urllib.request.urlopen(BASE + "/api/config", timeout=10).read())
tc = cfg.get("target_crs")
print("[config] target_crs =", tc)
print("[config] default_target =", cfg.get("default_target"))

out = os.path.join(tempfile.gettempdir(), "__exeverify__")
shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
body = {"min_lon":118.78,"min_lat":32.05,"max_lon":118.81,"max_lat":32.07,
        "zoom":16,"source":"tianditu_satellite","output_dir":out,"name":"exeverify"}
req = urllib.request.Request(BASE + "/api/download",
            data=json.dumps(body).encode(),
            headers={"Content-Type":"application/json"})
res = json.loads(urllib.request.urlopen(req, timeout=60).read())
tid = res["task_id"]
for _ in range(80):
    time.sleep(0.5)
    t = json.loads(urllib.request.urlopen(BASE + "/api/task?id=" + tid, timeout=10).read())
    if t["status"] != "running":
        break
print("[task] status =", t["status"], "progress =", t["progress"])
r = t["result"]
print("[result] ll(左下) =", [round(v,3) for v in r["ll"]])
print("[result] ur(右上) =", [round(v,3) for v in r["ur"]])

doc = ezdxf.readfile(r["dxf_path"])
for img in doc.query("IMAGE"):
    ins = [round(v,3) for v in img.dxf.insert]
    print("[DXF] IMAGE 插入点 =", ins, " <- 东偏>3900万 即 4527 第39带")

ok = r["ll"][0] > 39_000_000
print("\n结论: exe 默认下载产出 EPSG:4527 坐标 =", ok,
      "（东偏 %.0f 万）" % (r["ll"][0]/10000))
