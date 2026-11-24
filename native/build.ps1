param(
    [ValidatePattern('^[^\\/:*?"<>|]+\.exe$')]
    [string]$OutputName = '一键下载卫星图到CAD（Windows原生版）.exe'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$compiler = Join-Path $framework 'csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw '找不到 64 位 .NET Framework C# 编译器'
}
$dist = Join-Path $project 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$output = Join-Path $dist $OutputName
$web = Join-Path $project 'yijianditu\web'
$references = @(
    'System.dll', 'System.Core.dll', 'System.Net.Http.dll', 'System.Drawing.dll',
    'System.Web.dll', 'System.Web.Extensions.dll', 'System.Windows.Forms.dll'
) | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$resources = @(
    'index.html', 'app.js', 'app.css', 'logo.svg', 'vendor/leaflet.js', 'vendor/leaflet.css'
) | ForEach-Object { '/resource:' + (Join-Path $web $_) + ',' + $_ }
$resources += '/resource:' + (Join-Path $PSScriptRoot 'CadImage.dxf.template') + ',CadImage.dxf'
$sources = @('Program.cs', 'MapApi.cs', 'MapJob.cs', 'Projection.cs', 'Raster.cs') |
    ForEach-Object { Join-Path $PSScriptRoot $_ }
$icon = Join-Path $project 'assets\yijianditu.ico'

& $compiler /nologo /target:winexe /platform:x64 /optimize+ /unsafe+ /codepage:65001 `
    ("/out:$output") ("/win32icon:$icon") $references $resources $sources
if ($LASTEXITCODE -ne 0) { throw '原生版编译失败' }
Get-Item -LiteralPath $output | Select-Object FullName, Length
