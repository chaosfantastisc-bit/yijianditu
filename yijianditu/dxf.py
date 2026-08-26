# -*- coding: utf-8 -*-
"""
DXF 文件生成模块
功能：根据影像的左下角和右上角坐标生成矩形框，并在矩形框内插入 JPG 影像
依赖: ezdxf

修复记录:
  - ISM图像词典合并失败: add_image_def 的 name 参数改用纯文件名（不含路径），
    IMAGEDEF.filename 仍保留绝对路径供软件定位文件。
  - IMAGE flags 从 7 改为 3（去掉 USE_CLIPPING_BOUNDARY 位），
    避免部分 CAD 软件裁剪区域报错。
  - 设置 set_raster_variables 单位为米，确保 $INSUNITS 与光栅变量一致。
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import ezdxf
except ImportError:
    raise ImportError("请安装 ezdxf: pip install ezdxf")

from .config import log


def generate_dxf(
    ll_x: float, ll_y: float,
    ur_x: float, ur_y: float,
    jpg_path: str,
    output_dxf_path: str,
    image_width_px: int = None,
    image_height_px: int = None,
) -> str:
    """
    生成包含矩形框和内嵌 JPG 影像的 DXF 文件

    参数:
        ll_x, ll_y: 左下角坐标 (CGCS2000/CM111E EPSG:4546, 单位: 米)
        ur_x, ur_y: 右上角坐标 (CGCS2000/CM111E EPSG:4546, 单位: 米)
        jpg_path:   压缩后的 JPG 影像路径
        output_dxf_path: 输出 DXF 文件路径
        image_width_px / image_height_px: 影像像素尺寸，可选
    返回: DXF 文件路径
    """
    doc = ezdxf.new(dxfversion="R2010")
    doc.header["$INSUNITS"] = 6  # 6 = 米 (Meters)

    # 设置光栅变量（单位=米），必须在 add_image_def 之前调用
    doc.objects.set_raster_variables(frame=0, quality=1, units="m")

    msp = doc.modelspace()

    # 创建图层
    doc.layers.new(name="BOUNDARY", dxfattribs={"color": 1})  # 红色
    doc.layers.new(name="IMAGE", dxfattribs={"color": 7})  # 白色/黑色

    # 绘制矩形边界框
    ll = (ll_x, ll_y)
    lr = (ur_x, ll_y)
    ur = (ur_x, ur_y)
    ul = (ll_x, ur_y)

    msp.add_lwpolyline(
        [ll, lr, ur, ul],
        dxfattribs={
            "layer": "BOUNDARY",
            "lineweight": 50,  # 0.50mm
            "closed": True,
        },
    )

    _insert_image(
        doc, msp,
        jpg_path=jpg_path,
        ll_x=ll_x, ll_y=ll_y,
        ur_x=ur_x, ur_y=ur_y,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
    )

    os.makedirs(os.path.dirname(output_dxf_path) or ".", exist_ok=True)
    doc.saveas(output_dxf_path)
    log(f"[DXF生成] 已保存: {output_dxf_path}")
    return output_dxf_path


def _insert_image(
    doc, msp,
    jpg_path: str,
    ll_x: float, ll_y: float,
    ur_x: float, ur_y: float,
    image_width_px: int = None,
    image_height_px: int = None,
):
    """在 DXF 中插入影像：IMAGE 实体，插入点为左下角，尺寸与矩形框匹配"""
    world_width = ur_x - ll_x
    world_height = ur_y - ll_y

    if world_width <= 0 or world_height <= 0:
        raise ValueError(
            f"无效的坐标范围: ll=({ll_x},{ll_y}), ur=({ur_x},{ur_y})"
        )

    if image_width_px is None or image_height_px is None:
        try:
            from PIL import Image

            with Image.open(jpg_path) as img:
                image_width_px, image_height_px = img.size
        except Exception:
            image_width_px = 1024
            image_height_px = 1024

    pixel_size_x = world_width / image_width_px
    pixel_size_y = world_height / image_height_px

    # name 用纯文件名作词典 key（修复 ISM 合并失败，保留不动）
    # filename 用图片的完整路径：CAD 直接按绝对路径定位文件
    jpg_filename = Path(jpg_path).name
    full_path = os.path.abspath(jpg_path)

    image_def = doc.add_image_def(
        filename=full_path,
        size_in_pixel=(image_width_px, image_height_px),
        name=jpg_filename,
    )

    msp.add_image(
        image_def=image_def,
        insert=(ll_x, ll_y, 0),
        size_in_units=(world_width, world_height),
        dxfattribs={
            "layer": "IMAGE",
            # flags=3: SHOW_IMAGE(1) | SHOW_IMAGE_WHEN_NOT_ALIGNED(2)
            "flags": 3,
        },
    )

    log(
        f"[DXF图像] 插入影像: {jpg_filename}, "
        f"尺寸={world_width:.2f}m x {world_height:.2f}m, "
        f"像素={image_width_px}x{image_height_px}"
    )


def verify_dxf(dxf_path: str) -> dict:
    """简单验证 DXF 文件完整性（含 BOUNDARY 矩形与 IMAGE 影像）"""
    result = {
        "valid": False,
        "has_boundary": False,
        "has_image": False,
        "entity_count": 0,
        "errors": [],
    }
    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        entities = list(msp)
        result["entity_count"] = len(entities)

        for e in entities:
            if e.dxftype() == "LWPOLYLINE":
                result["has_boundary"] = True
            if e.dxftype() == "IMAGE":
                result["has_image"] = True

        result["valid"] = result["has_boundary"] and result["has_image"]
    except Exception as ex:
        result["errors"].append(str(ex))

    return result


if __name__ == "__main__":
    import tempfile

    from PIL import Image

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        tmp_jpg = f.name
    img = Image.new("RGB", (100, 100), color=(128, 64, 32))
    img.save(tmp_jpg, "JPEG")

    tmp_dxf = tmp_jpg.replace(".jpg", "_boundary.DXF")
    generate_dxf(
        ll_x=500000.0, ll_y=3900000.0,
        ur_x=501000.0, ur_y=3901000.0,
        jpg_path=tmp_jpg,
        output_dxf_path=tmp_dxf,
    )

    v = verify_dxf(tmp_dxf)
    print(f"验证结果: {v}")
    os.unlink(tmp_jpg)
    os.unlink(tmp_dxf)
