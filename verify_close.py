# -*- coding: utf-8 -*-
"""验证「关闭网页即退出」：启动服务 → ping → close → 进程退出"""
import json
import subprocess
import sys
import time
import urllib.request

PY = r"C:\Users\Fantastic\.workbuddy\binaries\python\versions\3.13.12\python.exe"
ROOT = r"D:\Python\claw\yijianditu"
PORT = "17899"
BASE = f"http://127.0.0.1:{PORT}"


def req(method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
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

        # 1) 心跳 ping 应成功且进程不退出
        assert req("GET", "/api/ping").get("ok") is True, "ping 失败"
        time.sleep(1.0)
        assert p.poll() is None, "ping 后进程不该退出"
        print("OK: ping 正常，进程保持运行")

        # 2) 发 close → 看门狗应在 ~2s 内关闭服务
        assert req("POST", "/api/close", {}).get("ok") is True, "close 失败"
        deadline = time.time() + 6
        while time.time() < deadline:
            if p.poll() is not None:
                break
            time.sleep(0.3)
        rc = p.poll()
        if rc is None:
            print("FAIL: close 后进程未退出")
            p.terminate()
            return 1
        print(f"OK: close 后进程已退出 (returncode={rc})")
        return 0
    finally:
        if p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
