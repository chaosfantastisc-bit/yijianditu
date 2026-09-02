# 一键地图 · yijianditu — 交付说明

## 目标
在 geo-downloader（Rust/Tauri 影像下载器）需求基础上，做了一个**精简 Python 重构版**：
在线影像（天地图影像 / ArcGIS 卫星）→ 下载 → 重投影到 **CGCS2000 高斯-克吕格 中央经线 118.5°E** → 只导出 **DXF + JPG** 两个文件。

## 为什么走 Python 而非改 Rust
本机无 Rust 工具链、无 MSVC 编译器，GitHub 直连 443 不通（已用 ghfast.top 镜像克隆过 geo-downloader 做参考）。
因此下载算法用 Python 重写，重投影内核与 DXF 生成直接复用用户已验证的 `D:\Python\claw\yingxiang2cad`。

## 已实现
- **界面**：Leaflet 本地化（vendor 内嵌，无 CDN 依赖）+ 双图源切换 + 网页地图鼠标拉框 / Shift 拖拽选区。
- **两图源**：`tianditu_satellite`（含注记层，最高 18 级）、`arcgis_satellite`（最高 19 级）。
- **坐标链路**：Web Mercator EPSG:3857 → tmerc lon_0=118.5 +ellps=GRS80（crs.py）。
- **输出**：DXF（影像作为底图引用，118.5 坐标）+ JPG（压缩至 ≤30MB）。中间 GeoTIFF 不落盘。
- **服务**：本地 HTTP（127.0.0.1），含 API：config / tile 代理（LRU 缓存）/ estimate / download / task 轮询 / cancel / open。
- **健壮性**：瓦片并发下载+指数退避重试，缺失瓦片留黑；超限范围（12000 瓦片 / 2.6 亿像素）前端拦截；PyInstaller --noconsole 下的日志与 stderr 静默处理。

## 验证结果
- 后端：`selftest_pipeline.py` + `selftest_api.py` 全 PASS（坐标精度误差 <3m，输出恰为 dxf+jpg 两文件）。
- 运行中的服务 live 校验：config/estimate/tile/download/task 的**返回字段与前端 app.js 期望完全一致**；静态资源全部 200；`app.js`、`leaflet.js` 经 `node --check` 语法通过。
- 南京实测：下载 108 瓦片 → 输出 2807×2201 px，左下 (525493, 3547428)、右上 (528332, 3549654)，DXF+JPG 均落盘成功。

## 如何运行
```bash
# 用本机 managed Python
C:/Users/Fantastic/.workbuddy/binaries/python/versions/3.13.12/python.exe -m yijianditu
# 或指定端口： -m yijianditu --port 17877
```
启动后浏览器打开 `http://127.0.0.1:17877/`（当前会话已在 :17877 运行）。

## 已知未覆盖项（非功能，纯可视化）
- 真实浏览器内的 Leaflet 瓦片渲染与拖拽手感：本机无 browser-use / Playwright，未能跑无头浏览器做像素级验证。逻辑与数据契约已逐项核对，建议你在浏览器里点一下确认观感。
- 可选后续：PyInstaller 单 exe 打包、README 文档。

## 关键文件
- `yijianditu/__main__.py` 启动入口　`yijianditu/server.py` HTTP 服务
- `yijianditu/pipeline.py` 主流程　`yijianditu/crs.py` 重投影　`yijianditu/mosaic.py` 瓦片下载拼接
- `yijianditu/tiles.py` 瓦片数学　`yijianditu/dxf.py` DXF 生成　`yijianditu/image.py` JPG 压缩
- `yijianditu/web/*` 前端（index.html / app.js / app.css / vendor/leaflet.*）
