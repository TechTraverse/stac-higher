# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "mercantile", "pillow"]
# ///
"""Visual check: z5 CONUS mosaic, cube tiles (60 %) over OSM, for one frame."""
import io, httpx, mercantile
from PIL import Image
T = open("times.txt").read().strip().split(",")[4]
Q = "variables=CMI&style=raster/gray&colorscalerange=190,310&width=256&height=256&f=png"
z = 5
tiles = list(mercantile.tiles(-125, 24, -66, 50, [z]))
xs = sorted({t.x for t in tiles}); ys = sorted({t.y for t in tiles})
img = Image.new("RGBA", (256 * len(xs), 256 * len(ys)))
c = httpx.Client(timeout=120, headers={"User-Agent": "stac-higher-z1-spike/0.1 (research test)"})
for t in tiles:
    osm = Image.open(io.BytesIO(c.get(f"https://tile.openstreetmap.org/{z}/{t.x}/{t.y}.png").content)).convert("RGBA")
    cube = Image.open(io.BytesIO(c.get(f"http://localhost:9100/datasets/goes-c13-m/tiles/WebMercatorQuad/{z}/{t.y}/{t.x}?{Q}&t={T}").content)).convert("RGBA")
    a = cube.getchannel("A").point(lambda v: int(v * 0.6)); cube.putalpha(a)
    img.paste(Image.alpha_composite(osm, cube), ((t.x - xs[0]) * 256, (t.y - ys[0]) * 256))
img.convert("RGB").resize((img.width // 2, img.height // 2)).save("tiles/mosaic-z5.jpg", quality=85)
print(T, img.size)
