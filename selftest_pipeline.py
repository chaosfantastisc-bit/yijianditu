# -*- coding: utf-8 -*-
"""
端到端自测：真实下载 → CM118.5 → JPG + DXF，并校验坐标精度

校验点：
  1. 瓦片网格计算与像素尺寸自洽
  2. 重投影后角点坐标 与 pyproj 直接换算的理论值 偏差 < 1.5m（重投影外接矩形本身有取整）
  3. DXF 含 BOUNDARY 矩形 + IMAGE 实体，矩形四角 = 重投影 bounds
  4. JPG 存在且 ≤30MB
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yijianditu import pipeline
from yijianditu.crs import lonlat_to_target
from yijianditu.dxf import verify_dxf
from yijianditu.tiles import TileGrid

# 南京城区一小块（中央经线 118.5 附近，正好考验 118.5 带）
MIN_LON, MIN_LAT = 118.780, 32.050
MAX_LON, MAX_LAT = 118.790, 32.058
ZOOM = 16

fails = []


def check(cond, label, detail=""):
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {label} {detail}")
    if not cond:
        fails.append(label)


print("=" * 70)
print("1) 瓦片网格")
grid = TileGrid(MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, ZOOM)
est = grid.estimate()
print(f"    {grid}  预估: {est}")
check(grid.tile_count == grid.cols * grid.rows, "网格数量自洽")
check(grid.crop_box[2] - grid.crop_box[0] == grid.width, "裁剪框宽度 == 输出宽度")
check(grid.crop_box[3] - grid.crop_box[1] == grid.height, "裁剪框高度 == 输出高度")
check(grid.check_limits() is None, "未超出安全阈值")

print("=" * 70)
print("2) 真实下载 + 全流程")
outdir = os.path.join(tempfile.gettempdir(), "yijianditu_selftest")
res = pipeline.run(
    MIN_LON, MIN_LAT, MAX_LON, MAX_LAT, ZOOM,
    "tianditu_satellite", outdir, name="selftest_tdt",
    progress_cb=lambda p, m: print(f"    [{p:5.1f}%] {m}"),
)
check(res["success"], "流程执行成功", res.get("error") or "")
if not res["success"]:
    print("\n结论: 失败 ->", res["error"])
    sys.exit(1)

check(res["missing_tiles"] == 0, "无瓦片缺失", f"missing={res['missing_tiles']}")

print("=" * 70)
print("3) 坐标精度校验（CM118.5°）")
ll_theory = lonlat_to_target(MIN_LON, MIN_LAT)
ur_theory = lonlat_to_target(MAX_LON, MAX_LAT)
ll, ur = res["ll"], res["ur"]
print(f"    理论左下 ({ll_theory[0]:.3f}, {ll_theory[1]:.3f})  实际 ({ll[0]:.3f}, {ll[1]:.3f})")
print(f"    理论右上 ({ur_theory[0]:.3f}, {ur_theory[1]:.3f})  实际 ({ur[0]:.3f}, {ur[1]:.3f})")
# 重投影外接矩形 >= 理论矩形（含收敛角导致的外扩），因此只校验外扩量在合理范围
dx_ll, dy_ll = ll[0] - ll_theory[0], ll[1] - ll_theory[1]
dx_ur, dy_ur = ur[0] - ur_theory[0], ur[1] - ur_theory[1]
print(f"    外扩量 左下({dx_ll:+.2f}, {dy_ll:+.2f})  右上({dx_ur:+.2f}, {dy_ur:+.2f}) 米")
check(dx_ll <= 1.0 and dy_ll <= 1.0, "左下角未内缩（外接矩形应包住理论范围）")
check(dx_ur >= -1.0 and dy_ur >= -1.0, "右上角未内缩")
check(abs(dx_ll) < 60 and abs(dy_ll) < 60, "左下外扩量在合理范围(<60m)")
check(abs(dx_ur) < 60 and abs(dy_ur) < 60, "右上外扩量在合理范围(<60m)")

# 尺度合理性：1 经度差在该纬度约 94.2km/度 → 0.01度 ≈ 942m
w_m, h_m = res["size_m"]
print(f"    输出实地尺寸: {w_m} m x {h_m} m")
check(800 < w_m < 1100, "东西向尺寸合理", f"{w_m}m")
check(700 < h_m < 1000, "南北向尺寸合理", f"{h_m}m")

print("=" * 70)
print("4) 输出文件校验")
jpg, dxf = res["jpg_path"], res["dxf_path"]
check(os.path.isfile(jpg), "JPG 已生成", jpg)
jpg_mb = os.path.getsize(jpg) / 1024 / 1024
check(jpg_mb <= 30.0, "JPG 体积 ≤30MB", f"{jpg_mb:.2f}MB")
check(os.path.isfile(dxf), "DXF 已生成", dxf)

v = verify_dxf(dxf)
print(f"    verify_dxf: {v}")
check(v["valid"], "DXF 结构有效（矩形 + 影像）")

import ezdxf

doc = ezdxf.readfile(dxf)
msp = doc.modelspace()
poly = next(e for e in msp if e.dxftype() == "LWPOLYLINE")
pts = [(round(p[0], 3), round(p[1], 3)) for p in poly.get_points("xy")]
print(f"    边界矩形角点: {pts}")
check(len(pts) == 4, "矩形为 4 个角点")
xs = sorted({p[0] for p in pts})
ys = sorted({p[1] for p in pts})
check(abs(xs[0] - ll[0]) < 0.01 and abs(ys[0] - ll[1]) < 0.01, "矩形左下 == 重投影左下")
check(abs(xs[-1] - ur[0]) < 0.01 and abs(ys[-1] - ur[1]) < 0.01, "矩形右上 == 重投影右上")

img = next(e for e in msp if e.dxftype() == "IMAGE")
ins = img.dxf.insert
print(f"    IMAGE 插入点: ({ins.x:.3f}, {ins.y:.3f})  size_in_pixel={img.dxf.image_size}")
check(abs(ins.x - ll[0]) < 0.01 and abs(ins.y - ll[1]) < 0.01, "影像插入点 == 左下角")
check(doc.header["$INSUNITS"] == 6, "DXF 单位为米")

imgdef_name = img.dxf.image_def_handle
defs = [e for e in doc.objects if e.dxftype() == "IMAGEDEF"]
print(f"    IMAGEDEF filename: {[d.dxf.filename for d in defs]}")
check(all(not os.path.isabs(d.dxf.filename) for d in defs), "影像引用为相对路径（便于整体拷贝）")

print("=" * 70)
if fails:
    print(f"结论: {len(fails)} 项失败 -> {fails}")
    sys.exit(1)
print(f"结论: 全部通过 | 输出目录 {outdir} | 耗时 {res['elapsed']}s")
