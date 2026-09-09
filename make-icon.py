#!/usr/bin/env python3
"""Generates the Nabatt app icon (web/icon.png + web/Nabatt.ico).

Palette is Petra sandstone -- amber at the top falling to rose-red -- with a
cream bolt knocked out of it. Run once; re-run only to change the look.
"""
import os
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
os.makedirs(WEB, exist_ok=True)

SS = 4                      # supersample factor, for clean curves
TOP = (255, 190, 92)        # sandstone in sunlight
BOT = (183, 58, 42)         # rose-red, the colour Petra is cut from
BOLT = (255, 249, 240)      # cream

# A lightning bolt in 0..1 space. Slightly wider and squarer than a stock
# bolt so it still reads at 16 px in the notification area.
SHAPE = [(0.60, 0.03), (0.19, 0.56), (0.44, 0.56), (0.37, 0.97),
         (0.81, 0.42), (0.55, 0.42), (0.65, 0.03)]


def _bolt(n, pad_frac):
    pad = n * pad_frac
    inner = n - pad * 2
    return [(pad + x * inner, pad + y * inner) for x, y in SHAPE]


def make(size):
    n = size * SS
    r = int(n * 0.235)

    # vertical sandstone gradient, clipped to a rounded square
    grad = Image.new("RGB", (1, n))
    gd = ImageDraw.Draw(grad)
    for y in range(n):
        t = y / max(1, n - 1)
        gd.point((0, y), tuple(int(TOP[i] + (BOT[i] - TOP[i]) * t) for i in range(3)))
    grad = grad.resize((n, n))

    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, n - 1, n - 1], radius=r, fill=255)

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)

    # soft light from the top-left, as a radial alpha -- no hard edges
    glow = Image.radial_gradient("L").resize((int(n * 1.9), int(n * 1.9)))
    glow = Image.eval(glow, lambda v: max(0, 62 - v // 3))
    sheen = Image.new("RGBA", (n, n), (255, 255, 255, 0))
    sheen.putalpha(glow.crop((int(n * 0.62), int(n * 0.62),
                              int(n * 0.62) + n, int(n * 0.62) + n)))
    sheen.putdata([(255, 255, 255, a) for a in sheen.getchannel("A").getdata()])
    img.alpha_composite(Image.composite(sheen, Image.new("RGBA", (n, n), (0, 0, 0, 0)), mask))

    # bolt, with a soft drop shadow so it lifts off the sandstone
    pts = _bolt(n, 0.145)
    sh = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(sh).polygon([(x, y + n * 0.022) for x, y in pts], fill=(90, 20, 10, 105))
    sh = sh.filter(ImageFilter.GaussianBlur(n * 0.022))
    img.alpha_composite(Image.composite(sh, Image.new("RGBA", (n, n), (0, 0, 0, 0)), mask))
    ImageDraw.Draw(img).polygon(pts, fill=BOLT + (255,))

    return img.resize((size, size), Image.LANCZOS)


def make_small(size):
    """16/24 px: no shadow, fatter bolt, so it stays legible when tiny."""
    n = size * SS
    r = int(n * 0.22)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, n - 1, n - 1], radius=r,
                        fill=(206, 86, 52, 255))
    d.polygon(_bolt(n, 0.10), fill=BOLT + (255,))
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    make(512).save(os.path.join(WEB, "icon.png"))

    ico = os.path.join(WEB, "Nabatt.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [(make_small(s) if s <= 24 else make(s)) for s in sizes]
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in sizes], append_images=frames[:-1])

    # a contact sheet, so the small sizes can actually be eyeballed
    sheet = Image.new("RGBA", (560, 300), (245, 246, 249, 255))
    x = 20
    for s in sizes:
        f = frames[sizes.index(s)]
        sheet.alpha_composite(f, (x, 150 - s // 2))
        x += s + 22
    sheet.alpha_composite(make(256), (300, 22))
    sheet.save(os.path.join(WEB, "_icon-preview.png"))

    print("wrote", os.path.join(WEB, "icon.png"))
    print("wrote", ico)
