"""The app's icon, drawn rather than shipped: the tray (with a dot for what it's doing), the
window's favicon and the installer's .ico are all this picture."""

from __future__ import annotations

import io

DOTS = {"live": (255, 84, 104, 255), "sending": (79, 227, 216, 255)}


def image(dot: str | None = None, size: int = 64):
    """A gold Q on a dark tile; a red dot while streaming, a teal one while sending live data."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    gold = (246, 197, 82, 255)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(15, 20, 34, 255), outline=gold, width=4)
    d.ellipse((15, 13, 49, 47), outline=gold, width=7)
    d.line((36, 36, 50, 51), fill=gold, width=8)
    if dot in DOTS:
        d.ellipse((40, 4, 60, 24), fill=DOTS[dot], outline=(15, 20, 34, 255), width=3)
    return img if size == 64 else img.resize((size, size), Image.LANCZOS)


def ico() -> bytes:
    """The icon as a Windows .ico, at the sizes Explorer and the taskbar ask for."""
    out = io.BytesIO()
    image(size=256).save(out, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out.getvalue()
