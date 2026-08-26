# -*- coding: utf-8 -*-
"""连通性探测：天地图影像 WMTS / ArcGIS World_Imagery"""
import math
import time
import urllib.request

TK = "436ce7e50d27eede2f2929307e6b33c0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def deg2tile(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    lat_r = math.radians(lat)
    y = int((1.0 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2.0 * n)
    return x, y


def fetch(url, referer=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if referer:
        req.add_header("Referer", referer)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    return r.status, len(data), data[:4], (time.time() - t0) * 1000


z = 16
x, y = deg2tile(118.8, 32.06, z)
print(f"tile z={z} x={x} y={y}")

targets = [
    ("天地图影像 img_w",
     f"https://t0.tianditu.gov.cn/img_w/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
     f"&LAYER=img&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={TK}",
     "https://www.tianditu.gov.cn/"),
    ("ArcGIS World_Imagery",
     f"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
     None),
]

for name, url, ref in targets:
    try:
        st, ln, magic, ms = fetch(url, ref)
        print(f"[OK]   {name}: HTTP {st}, {ln} bytes, magic={magic!r}, {ms:.0f}ms")
    except Exception as e:
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
