"""Draw assets/icon.png (1024 px) -- the same design as assets/icon.svg, for the app builds
(PyInstaller turns it into the Windows .ico and the macOS .icns)."""
from pathlib import Path
from PIL import Image, ImageDraw

S = 1024
k = S / 256


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


stops = [(0, (0x8b, 0x7c, 0xff)), (0.55, (0x5f, 0xa8, 0xff)), (1, (0x38, 0xd9, 0xf5))]
grad = Image.new("RGB", (S, S))
px = grad.load()
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t <= t1:
                px[x, y] = lerp(c0, c1, (t - t0) / (t1 - t0))
                break

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=58 * k, fill=(0x0b, 0x0f, 0x18, 255))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle((20 * k, 20 * k, 236 * k, 236 * k), radius=46 * k, fill=255)
img.paste(grad, (0, 0), mask)
bolt = [(141, 40), (70, 146), (126, 146), (115, 216), (186, 110), (130, 110)]
d.polygon([(x * k, y * k) for x, y in bolt], fill=(0x0b, 0x0d, 0x14, 255))
out = Path(__file__).resolve().parent.parent / "assets" / "icon.png"
img.save(out)
print(out)
