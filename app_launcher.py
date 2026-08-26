# -*- coding: utf-8 -*-
"""PyInstaller 入口：调用 yijianditu 的 main()，并对崩溃做落盘记录。

无控制台窗口（--noconsole）下，若启动期异常无法直接看到，
这里把 traceback 写到 exe 同目录的 yijianditu_crash.log 便于排查。
"""
from __future__ import annotations

import os
import sys
import traceback


def _app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    try:
        from yijianditu.__main__ import main as run_main
        return run_main()
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        try:
            with open(os.path.join(_app_dir(), "yijianditu_crash.log"), "w", encoding="utf-8") as f:
                f.write(tb)
        except Exception:
            pass
        if sys.stdout is not None:
            sys.stdout.write(tb)
        return 1


if __name__ == "__main__":
    sys.exit(main())
