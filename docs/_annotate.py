"""Generate the annotated UI guide image from docs/ui-raw.png.
Run: python3 docs/_annotate.py  (Pillow required)
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SRC = HERE / "ui-raw.png"
OUT = HERE / "ui-guide.png"
OUT_SMALL = HERE / "ui-guide-help.png"   # smaller version embedded in the app
SMALL_WIDTH = 720

# (number, x, y) where to put a circle on the UI; coordinates are in the
# original 1424x2000 screenshot.
CALLOUTS = [
    (1,  330,  300),   # Jazyk dropdown
    (2,  168,  360),   # Vyříznout ticho
    (3,  620,  360),   # Práh dB (numeric block)
    (4,  168,  420),   # Vycpávková slova:
    (5,  168,  600),   # filler-group checkboxes (column)
    (6,  168,  775),   # Vlastní slova entry
    (7,  264,  855),   # Titulky header
    (8,  168,  977),   # Vytvořit titulky při Aplikovat
    (9,  712, 1030),   # Vygenerovat titulky button
    (10, 712, 1100),   # Skupiny pokusů button
    (11, 268, 1165),   # 1. Analyzovat
    (12, 460, 1165),   # Živě
    (13, 1138, 1165),  # 2. Aplikovat střih
    (14, 280, 1235),   # Status / Ke smazání
    (15, 720, 1500),   # Přepis editor
]

# Try a clean macOS font, fall back to default.
def _load_font(size):
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            pass
    return ImageFont.load_default()


def main():
    img = Image.open(SRC).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    font = _load_font(34)
    R = 32  # circle radius

    for n, x, y in CALLOUTS:
        # red filled circle with white border for contrast
        draw.ellipse((x - R, y - R, x + R, y + R), fill=(255, 59, 48, 235),
                     outline=(255, 255, 255, 255), width=4)
        text = str(n)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((x - tw / 2 - bbox[0], y - th / 2 - bbox[1]), text,
                  fill=(255, 255, 255, 255), font=font)

    img.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT}")

    # smaller copy for the in-app Help window (Tk PhotoImage loads PNG directly)
    ratio = SMALL_WIDTH / img.width
    small = img.resize((SMALL_WIDTH, int(img.height * ratio)), Image.LANCZOS)
    small.save(OUT_SMALL, "PNG", optimize=True)
    print(f"wrote {OUT_SMALL}  ({small.size[0]}x{small.size[1]})")


if __name__ == "__main__":
    main()
