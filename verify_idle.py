# -*- coding: utf-8 -*-
"""验证兜底：无 close 信号、网页无活动超时后进程自动退出"""
import json
import subprocess
import sys
import time
import urllib.request

PY = r"C:\Users\Fantastic\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ROOT = r"D:\Python\claw\yijianditu"
PORT = "17901"
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, timeout=10):
    r = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def wait_up(seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        try:
            req("GET", "/api/config")
            return True
        except Exception:
            time.sleep(0.3)
    return False


def main():
    p = subprocess.Popen(
        [PY, "-m", "yijianditu", "--no-browser", "--port", PORT],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_up():
            print("FAIL: 服务未启动")
            p.terminate()
            return 1
        # 触发一次客户端活动（_has_client=True），之后完全静默
        req("GET", "/api/config")
        print("已建立连接并保持静默，等待 idle 超时（约 20s）…")
        deadline = time.time() + 30
        exited = False
        while time.time() < deadline:
            if p.poll() is not None:
                exited = True
                break
            time.sleep(0.5)
        if not exited:
            print("FAIL: 静默 30s 后进程仍未退出")
            p.terminate()
            return 1
        print(f"OK: 无活动超时后进程自动退出 (returncode={p.poll()})")
        return 0
    finally:
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
