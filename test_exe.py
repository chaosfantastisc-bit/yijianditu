# -*- coding: utf-8 -*-
r"""验证 dist\yijianditu.exe 能否独立运行并完成一次小范围下载。
避免与已有 python 服务冲突，使用 --port 17880 --no-browser。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "yijianditu.exe")
PORT = 17880
BASE = f"http://127.0.0.1:{PORT}"
TEST_OUT = os.path.join(os.path.expanduser("~"), "Desktop", "一键地图输出", "__exetest__")


def wait_for_port(timeout: int = 60) -> bool:
    for _ in range(timeout * 4):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.25)
    return False


def http_get(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=30) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


def http_post(path: str, obj: dict):
    data = json.dumps(obj).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), ""


def main() -> int:
    if not os.path.exists(EXE):
        print("ERROR: exe not found:", EXE)
        return 1

    # 清理旧测试输出
    shutil.rmtree(TEST_OUT, ignore_errors=True)

    print("[1/5] 启动 exe ...")
    proc = subprocess.Popen(
        [EXE, "--port", str(PORT), "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        if not wait_for_port():
            print("ERROR: 服务未启动")
            return 1
        print("[2/5] 服务已监听", PORT)

        s, b, _ = http_get("/api/config")
        cfg = json.loads(b)
        print("[3/5] /api/config OK -> sources:", [x["id"] for x in cfg["sources"]])

        bbox = {"min_lon": 118.77, "min_lat": 32.05, "max_lon": 118.80, "max_lat": 32.07,
                "zoom": 17, "source": "tianditu_satellite"}
        s, b, _ = http_post("/api/estimate", bbox)
        est = json.loads(b)
        print("  estimate -> tiles:", est.get("tile_count"), "limit_error:", est.get("limit_error"))

        dl = dict(bbox)
        dl["output_dir"] = TEST_OUT
        dl["name"] = "exetest_nj"
        s, b, _ = http_post("/api/download", dl)
        tid = json.loads(b)["task_id"]
        print("[4/5] 下载任务启动:", tid)

        for _ in range(50):
            time.sleep(0.5)
            s, b, _ = http_get(f"/api/task?id={tid}")
            t = json.loads(b)
            if t["status"] != "running":
                break
        print("  最终状态:", t["status"], "进度:", t["progress"])

        if t["status"] != "done":
            print("ERROR: 任务未成功完成", t)
            return 1
        r = t["result"]
        print("[5/5] 输出文件:")
        for p in (r["dxf_path"], r["jpg_path"]):
            print(" ", p, "exists?", os.path.exists(p))
            if not os.path.exists(p):
                return 1

        # 校验 DXF 内图片引用为完整路径
        try:
            import ezdxf
            doc = ezdxf.readfile(r["dxf_path"])
            paths = [d.dxf.filename for d in doc.objects.query("IMAGEDEF")]
            print("  DXF 图片路径:", paths)
            if not paths or not os.path.isabs(paths[0]):
                print("ERROR: DXF 图片路径非完整路径:", paths)
                return 1
            if not os.path.exists(paths[0]):
                print("WARN: DXF 引用的图片路径不存在:", paths[0])
        except Exception as e:
            print("ERROR: 读取 DXF 失败:", e)
            return 1
        print("✅ exe 独立运行 + 下载 + DXF+JPG + 完整图片路径 全部 OK")

        # 额外验证 EPSG:4527 坐标系下载
        dl4527 = dict(bbox)
        dl4527["output_dir"] = TEST_OUT
        dl4527["name"] = "exetest_4527"
        dl4527["target_crs"] = "epsg4527"
        s, b, _ = http_post("/api/download", dl4527)
        tid2 = json.loads(b)["task_id"]
        for _ in range(50):
            time.sleep(0.5)
            s, b, _ = http_get(f"/api/task?id={tid2}")
            t2 = json.loads(b)
            if t2["status"] != "running":
                break
        if t2["status"] != "done":
            print("ERROR: 4527 任务未成功完成", t2)
            return 1
        r2 = t2["result"]
        print("[6/6] 4527 下载:", r2["target_crs"], "ll=", r2 is not None and r2["ll"])
        if not (r2["ll"][0] > 39_000_000):
            print("ERROR: 4527 东偏不在 39M 区间:", r2["ll"])
            return 1
        print("✅ exe 的 EPSG:4527 坐标系下载 OK (东偏=%.0f，第39带)" % r2["ll"][0])
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        shutil.rmtree(TEST_OUT, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
