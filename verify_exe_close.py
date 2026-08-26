# -*- coding: utf-8 -*-
"""验证打包后的 exe：ping 保活 + close 自动退出（防止后台残留）"""
import json
import subprocess
import sys
import time
import urllib.request

EXE = r"D:\Python\claw\yijianditu\dist\yijianditu.exe"
PORT = "17910"
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, timeout=10):
    r = urllib.request.Request(BASE + path, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def wait_up(seconds=25):
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
        [EXE, "--no-browser", "--port", PORT],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_up():
            print("FAIL: exe 未启动")
            p.terminate()
            return 1
        assert req("GET", "/api/ping").get("ok") is True, "ping 失败"
        time.sleep(1.0)
        assert p.poll() is None, "ping 后 exe 不该退出"
        print("OK: ping 正常，exe 保活运行中")

        assert req("POST", "/api/close", {}).get("ok") is True, "close 失败"
        deadline = time.time() + 6
        while time.time() < deadline and p.poll() is None:
            time.sleep(0.3)
        rc = p.poll()
        if rc is None:
            print("FAIL: close 后 exe 未退出")
            p.terminate()
            return 1
        print(f"OK: 关闭网页信号后 exe 已退出 (returncode={rc})")
        return 0
    finally:
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
