# -*- coding: utf-8 -*-
"""无网络验证：动态 proj4 CRS（zone:40 / cm:120 / manual）在 rasterio 重投影中正常工作"""
from PIL import Image

from affine import Affine
from pyproj import Transformer

from yijianditu import crs

tr = Transformer.from_crs(4326, 3857, always_xy=True)
x0, y0 = tr.transform(118.78, 32.05)   # 左上
x1, y1 = tr.transform(118.79, 32.04)   # 右下
res = 10
w, h = int((x1 - x0) / res), int((y0 - y1) / res)
merc = Affine(res, 0, x0, 0, -res, y0)
img = Image.new("RGB", (w, h), (120, 160, 200))

for tid in ("zone:40", "cm:120", "manual:118.5"):
    data, _, bounds = crs.reproject_mosaic(img, merc, target_id=tid)
    print(f"{tid:12s} -> shape={data.shape} bounds={[round(v, 1) for v in bounds]}")
    assert data.shape[0] > 0 and data.shape[1] > 0

# zone:40 左下 x 应含带号前缀 40
_, _, b40 = crs.reproject_mosaic(img, merc, target_id="zone:40")
assert 40_000_000 < b40[0] < 50_000_000, "zone 应含带号前缀"
# cm:120 左下 x 不应含带号前缀
_, _, b120 = crs.reproject_mosaic(img, merc, target_id="cm:120")
assert b120[0] < 1_000_000, "cm 不应含带号前缀"
print("REPROJECT_OK")
