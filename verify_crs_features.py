# -*- coding: utf-8 -*-
"""验证新坐标系功能：分带识别 / 跨带取小 / 超范围 / cm / 手动经线 / token 覆盖"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

PY = r"C:\Users\Fantastic\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ROOT = r"D:\Python\claw\yijianditu"
PORT = "17920"
BASE = f"http://127.0.0.1:{PORT}"


def post(path, body, timeout=40):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}")


def get(path, timeout=40):
    req = urllib.request.Request(BASE + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait_up(seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        try:
            get("/api/config")
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    p = subprocess.Popen([PY, "-m", "yijianditu", "--no-browser", "--port", PORT],
                         cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        assert wait_up(), "服务未启动"
        base = {"min_lat": 32.0, "max_lat": 32.1, "zoom": 17,
                "source": "tianditu_satellite", "min_lon": 118.7, "max_lon": 118.9}

        # 1) zone 单带（南京 118.7~118.9 -> 带号40，中央经线120，含带号 x0=400500000）
        r = post("/api/estimate", {**base, "crs_mode": "zone"})
        print("zone单带:", r["target_id"], "| x0≈", r["target_ll"][0])
        assert r["target_id"] == "zone:40" and r["zone"] == 40
        assert 40_000_000 < r["target_ll"][0] < 50_000_000, "zone 应含带号前缀(40)"

        # 2) 跨带（116.8~120.8 -> 39与40，取小编号39）
        r = post("/api/estimate", {**base, "min_lon": 116.8, "max_lon": 120.8, "crs_mode": "zone"})
        print("跨带:", r["target_id"], "| zone", r["zone"])
        assert r["target_id"] == "zone:39" and r["zone"] == 39

        # 3) 超范围（min_lon=70）
        r = post("/api/estimate", {**base, "min_lon": 70, "max_lon": 71, "crs_mode": "zone"})
        print("超范围:", r.get("error"), "| out_of_range:", r.get("out_of_range"))
        assert r.get("out_of_range") is True

        # 4) cm 模式（不加带号，x0=500000）
        r = post("/api/estimate", {**base, "crs_mode": "cm"})
        print("cm:", r["target_id"], "| x0≈", r["target_ll"][0])
        assert r["target_id"] == "cm:120"
        assert r["target_ll"][0] < 1_000_000, "cm 不应含带号前缀"

        # 5) manual 中央经线 118.5
        r = post("/api/estimate", {**base, "crs_mode": "manual", "manual_meridian": 118.5})
        print("manual:", r["target_id"], "|", r["target_label"])
        assert r["target_id"] == "manual:118.5"

        # 6) token 运行时覆盖（界面改 key）
        post("/api/estimate", {**base, "crs_mode": "zone", "tianditu_token": "MY_TEST_TOKEN_XYZ"})
        cfg = get("/api/config")
        print("token回显:", cfg.get("tianditu_token"))
        assert cfg.get("tianditu_token") == "MY_TEST_TOKEN_XYZ"

        print("ALL_OK")
        return 0
    finally:
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
