# 一键下载卫星图到CAD（自动识别中央子午线）

在线卫星影像 → 自动识别 CGCS2000 三度带中央子午线 → 重投影 → 输出 **DXF + JPG** 两个文件，可直接在 CAD 中插入配准好的卫星底图。

网页地图拉框选区，无需任何 GIS 基础，开箱即用。

---

## ✨ 功能特性

- **地图拉框选区**：Leaflet 网页地图，鼠标拖拽框选下载范围，实时预估瓦片数与像素量。
- **双图源**：
  - ArcGIS World_Imagery 卫星图（**默认**）
  - 天地图影像（点选天地图时才出现 Key 输入框）
- **自动识别中央子午线（CGCS2000 三度带）**：
  - 按下载区域中心经度自动判定所属 3° 带
  - **加带号模式** `Zone 25 ~ 45`（如 `CGCS2000_3_Degree_GK_Zone_40`，东偏移含带号）
  - **不加带号模式** `CM 75E ~ 135E`（如 `CGCS2000_3_Degree_GK_CM_117E`，东偏移不含带号）
  - 不加带号模式下支持**手动设置中央子午线**（按 度 / 分 / 秒 填写）
  - 区域横跨多个度带时**取小编号**，并在界面上提示
  - 选择区域超出支持范围（73.5°E ~ 136.5°E）时提示「选择区域超出范围」
- **天地图 Key 管理**：
  - 默认使用内置**公共 Key**；输入框留空即走公共 Key
  - 提示文案：「默认用公共key，如天地图不可用，请自己申请key填入」
  - 填入的 Key 会本地记住，下次启动自动生效（仅本机 `~/.yijianditu_token`）
- **输出 DXF + JPG**：
  - JPG 为裁剪后的影像
  - DXF 内以**完整绝对路径**引用 JPG，CAD 打开即见底图，四角写入投影坐标实现配准
- **干净退出**：关闭网页后后台进程自动退出（前端心跳 + 卸载时关闭信号 + 空闲超时兜底），不残留进程。
- **性能优化**：瓦片下载使用连接池复用 TCP/TLS；重投影默认 cubic 重采样；进度全程平滑无空窗。

---

## 🚀 使用方式

### 方式一：源码运行（需 Python 3.13+）

```bash
pip install rasterio pyproj ezdxf pillow numpy requests
python -m yijianditu            # 自动起本地服务并打开浏览器
python -m yijianditu --port 17800 --no-browser
```

打开 `http://127.0.0.1:17800`，在地图上框选区域 → 选图源与坐标系 → 点下载，
结果（DXF + JPG）保存到输出目录。

### 方式二：打包单文件 exe

```bash
build_exe.bat        # 生成 dist\yijianditu.exe（--onefile --noconsole）
```

双击 `yijianditu.exe` 即可，无需安装 Python 环境。

---

## 🧱 技术栈

- 后端：Python / `http.server` 轻量服务
- 坐标与栅格：`rasterio` + `pyproj`（动态构造 CGCS2000 三度带 CRS）
- CAD 输出：`ezdxf`
- 影像处理：`Pillow` + `numpy`
- 瓦片下载：`requests` 连接池
- 前端：Leaflet + 原生 JS / CSS

---

## 🙏 致谢 / 署名

本项目的**地图下载引擎与图源策略**参考自开源项目
[**geo-downloader**](https://github.com/gaopengbin/geo-downloader)（gaopengbin，MIT 许可）。
在原项目基础上，本工具重做为网页拉框交互、增加了 **CGCS2000 三度带自动识别中央子午线**、
坐标系动态切换（加/不加带号、手动中央经线）、DXF+JPG 双文件输出与干净退出等能力。

感谢原作者的优秀基础工作。

---

## 📄 许可

本仓库代码以 MIT 许可发布。
