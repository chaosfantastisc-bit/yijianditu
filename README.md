# 一键下载卫星图到CAD（自动识别中央子午线）

在线卫星影像 → 自动识别 CGCS2000 三度带中央子午线 → 重投影 → 输出 **DXF + JPG** 两个文件，可直接在 CAD 中插入配准好的卫星底图。

网页地图拉框选区，无需任何 GIS 基础，开箱即用。

---

## ⬇️ 下载

最新版本：**[v2.0.1 · Windows 原生版](https://github.com/chaosfantastisc-bit/yijianditu/releases/latest)**

| 文件 | 说明 |
| --- | --- |
| `一键下载卫星图到CAD（Windows原生版）.exe` | **推荐**。单文件 300 KB，免安装 Python、免装依赖，双击即用（Windows x64 / .NET Framework 4.8，Win10/11 自带） |
| `一键下载卫星图到CAD（Windows原生版）-2.0.1-源码.zip` | 原生版完整源码（C# + 内嵌 Leaflet 前端 + DXF 模板） |

> 需要 Python 版？见下方「方式一：源码运行」。

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
- **后台下载**：切换标签、刷新或关闭网页后，下载继续。双击同版 EXE 可重新打开页面并查看最近的任务。使用完毕后，点击网页左下方的「退出程序」结束后台服务。
- **性能优化**：ArcGIS 预览优先由浏览器直连，失败的瓦片回退到本地转发；批量下载复用连接并限制待处理瓦片数量。重投影继续使用 cubic 重采样。

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
build_exe.bat        # 从当前 Python 环境打包两个单文件 EXE 到 dist
```

安装 PyInstaller 和上述依赖后运行打包脚本。也可用 `build_exe.bat "C:\路径\python.exe"` 指定解释器。
双击成品即可运行。自动版默认使用三度带加带号坐标系，118.5 版默认使用手动中央子午线 118°30′E。
EXE 的运行日志位于 `%LOCALAPPDATA%\一键地图`。

### 方式三：Windows 原生版（C#，无需 Python）

源码位于 [`native/`](native/)，用系统自带的 .NET Framework C# 编译器构建：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\native\build.ps1
```

控件与算法要点见 [`native/README.md`](native/README.md)。运行日志位于
`%LOCALAPPDATA%\一键地图\运行-原生版.log`。

---

## 🧱 技术栈

- 后端：Python / `http.server` 轻量服务；原生版为 .NET Framework C#（`HttpListener`）
- 坐标与栅格：`rasterio` + `pyproj`（动态构造 CGCS2000 三度带 CRS）；原生版为 GRS80 椭球六阶 Krüger 级数
- CAD 输出：`ezdxf`；原生版为内嵌 DXF 模板写入
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
