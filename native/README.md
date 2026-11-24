# 一键下载卫星图到 CAD · Windows 原生版

运行 `dist` 中的 Windows 原生版 EXE。程序使用 Windows x64 和 .NET Framework 4.8，双击后在默认浏览器打开框选界面。

- **加带号 Zone**和**不加带号 CM**均按选区自动识别三度带。
- 从**不加带号 CM**切到**手动设置**时，自动带入当前选区的中央子午线。点击面板里的 **118.5°** 可填入 118°30′E。
- 输出的 DXF 使用米作为单位，引用同目录的 JPG。JPG 的绝对路径保存在 DXF 内。
- 关闭或刷新网页时，已启动的下载继续。双击 EXE 可重新查看最近一次任务。点击网页的 **退出程序** 结束本地服务。

## 构建

在项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\native\build.ps1
```

脚本使用系统的 .NET Framework C# 编译器，将现有 Leaflet 界面和 DXF 模板嵌入一个 EXE。

代码使用 .NET Framework HTTP 客户端和 Windows 图像编解码。CGCS2000 投影使用 GRS80 椭球上的六阶 Krüger 级数。DXF 模板来自项目原有的 DXF 导出流程，写入时补入每次任务的路径、边界和像素参数。

运行日志位于 `%LOCALAPPDATA%\一键地图\运行-原生版.log`。
