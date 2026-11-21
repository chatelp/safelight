"""Contact sheets: many Lightroom previews in one image, numbered, for culling and comparing."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, ImageOps

BACKGROUND = (24, 24, 26)
CAPTION_COLOR = (235, 235, 235)
MUTED = (150, 150, 155)
PICK_COLOR = (230, 230, 230)
REJECT_COLOR = (220, 70, 60)
LABEL_COLORS = {
    "red": (214, 64, 54),
    "yellow": (230, 196, 48),
    "green": (82, 168, 82),
    "blue": (64, 120, 214),
    "purple": (150, 90, 200),
}


@dataclass
class SheetPhoto:
    number: int
    jpeg: bytes | None
    caption: str
    rating: int = 0
    pick: int = 0
    label: str | None = None


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(photos: list[SheetPhoto], *, columns: int = 6, cell: int = 256) -> bytes:
    """JPEG of a grid: each cell shows the preview, its number, stars, flag and label."""
    columns = max(1, min(columns, len(photos) or 1))
    rows = max(1, math.ceil(len(photos) / columns))
    pad = max(6, cell // 32)
    caption_h = max(18, cell // 9)
    width = columns * (cell + pad) + pad
    height = rows * (cell + caption_h + pad) + pad
    sheet = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    number_font = _font(max(12, cell // 12))
    caption_font = _font(max(10, cell // 18))

    for index, photo in enumerate(photos):
        col, row = index % columns, index // columns
        x = pad + col * (cell + pad)
        y = pad + row * (cell + caption_h + pad)
        if photo.jpeg:
            try:
                image = ImageOps.exif_transpose(Image.open(io.BytesIO(photo.jpeg))).convert("RGB")
                image.thumbnail((cell, cell))
                sheet.paste(image, (x + (cell - image.width) // 2, y + (cell - image.height) // 2))
            except OSError:
                draw.rectangle((x, y, x + cell, y + cell), outline=MUTED)
        else:
            draw.rectangle((x, y, x + cell, y + cell), outline=MUTED)
            draw.text((x + 8, y + cell // 2), "no preview", fill=MUTED, font=caption_font)

        # Number badge, top left.
        badge = str(photo.number)
        box = draw.textbbox((0, 0), badge, font=number_font)
        bw, bh = box[2] - box[0] + 10, box[3] - box[1] + 8
        draw.rectangle((x, y, x + bw, y + bh), fill=(0, 0, 0))
        draw.text((x + 5, y + 2), badge, fill=CAPTION_COLOR, font=number_font)
        # Label stripe and flag, top right.
        if photo.label in LABEL_COLORS:
            draw.rectangle((x + cell - 10, y, x + cell, y + 28), fill=LABEL_COLORS[photo.label])
        if photo.pick == 1:
            draw.rectangle((x + cell - 26, y + 4, x + cell - 16, y + 18), fill=PICK_COLOR)
        elif photo.pick == -1:
            draw.line((x + cell - 28, y + 4, x + cell - 14, y + 18), fill=REJECT_COLOR, width=3)
            draw.line((x + cell - 28, y + 18, x + cell - 14, y + 4), fill=REJECT_COLOR, width=3)

        stars = "★" * photo.rating if photo.rating else ""
        caption = f"{photo.caption}  {stars}".strip()
        draw.text((x, y + cell + 3), caption[:60], fill=CAPTION_COLOR, font=caption_font)

    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=82)
    return out.getvalue()
