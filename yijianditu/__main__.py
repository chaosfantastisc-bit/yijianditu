# -*- coding: utf-8 -*-
"""
启动入口：起本地服务并打开浏览器

    python -m yijianditu              # 默认端口自动选择
    python -m yijianditu --port 8080  # 指定端口
    python -m yijianditu --no-browser # 不自动打开浏览器
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .config import log
from .server import serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="yijianditu", description="一键地图：在线影像 → CGCS2000 4527 DXF")
    parser.add_argument("--port", type=int, default=None, help="指定端口（默认自动寻找空闲端口）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)

    try:
        httpd, port = serve(args.port)
    except OSError as e:
        log(f"启动失败：端口不可用 - {e}")
        return 1

    url = f"http://127.0.0.1:{port}/"
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    log("=" * 56)
    log("  一键地图 · 在线影像 → CGCS2000 4527 DXF + JPG")
    log(f"  界面地址: {url}")
    log("  关闭网页即自动退出，无需手动结束进程")
    log("=" * 56)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("正在退出…")
    finally:
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
