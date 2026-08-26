@echo off
REM 一键地图 单文件 exe 打包脚本（在 D:\Python\claw\yijianditu 下运行）
set PY=C:\Users\Fantastic\.workbuddy\binaries\python\versions\3.13.12\python.exe
"%PY%" -m PyInstaller --noconsole --onefile --clean --name yijianditu --icon assets\yijianditu.ico ^
  --add-data "yijianditu/web;yijianditu/web" ^
  --collect-all rasterio ^
  --collect-all pyproj ^
  --hidden-import pyproj --hidden-import rasterio ^
  --hidden-import requests --collect-all requests ^
  app_launcher.py
echo.
echo 打包完成：dist\yijianditu.exe
pause
