"""
Рисует картинку schedule.png (сегодня и завтра) из schedule.json для виджета на телефоне.
Запускается в GitHub Actions после schedule_pdf.py. Нужна библиотека pillow.
"""
import datetime as dt
import json
import os
import re
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

TZ_NAME = os.environ.get("TZ_NAME", "Europe/Moscow")
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
S = 2  # рисуем в двойном размере и уменьшаем: края получаются гладкими
W, H = 1200, 600

BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"]
REG = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
       "/usr/share/fonts/dejavu/DejaVuSans.ttf"]

BG = (20, 23, 30, 242)
TEXT = (240, 243, 248, 255)
MUTED = (140, 150, 165, 255)
LINE = (52, 60, 76, 255)
BADGE = (40, 48, 64, 255)
BLUE = (96, 165, 250, 255)
AMBER = (251, 191, 36, 255)


def font(paths, size):
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size * S)
    return ImageFont.load_default()


def fit(draw, text, paths, size, max_w, min_size=20):
    """Подбирает размер шрифта, чтобы текст влез по ширине; в крайнем случае обрезает."""
    while size >= min_size:
        f = font(paths, size)
        if draw.textlength(text, font=f) <= max_w * S:
            return f, text
        size -= 2
    f = font(paths, min_size)
    while len(text) > 1 and draw.textlength(text + "…", font=f) > max_w * S:
        text = text[:-1]
    return f, text + "…"


def parse(line):
    """'2. МДК 0401 (11/2к)' -> ('2', 'МДК 0401', '11/2к')."""
    m = re.match(r"^(\d)\.\s*(.*)$", line)
    if not m:
        return None, line, ""
    n, rest = m.group(1), m.group(2)
    r = re.match(r"^(.*?)\s*\(([^()]*)\)$", rest)
    return (n, r.group(1), r.group(2)) if r else (n, rest, "")


def column(d, x, colw, label, day, data, accent):
    key = str(day)
    d.text((x * S, 60 * S), label, font=font(BOLD, 46), fill=TEXT, anchor="lm")
    d.text(((x + colw) * S, 66 * S), f"{WEEKDAYS[day.weekday()]} {day:%d.%m}",
           font=font(REG, 30), fill=accent, anchor="rm")
    d.rounded_rectangle([x * S, 100 * S, (x + 70) * S, 106 * S], radius=3 * S, fill=accent)
    if key not in data:
        notes = ["расписание ещё", "не выложено"]
    elif data[key] is None:
        notes = ["в изменениях нет,", "по основному", "расписанию"]
    else:
        notes = None
    if notes:
        for i, t in enumerate(notes):
            d.text((x * S, (170 + i * 48) * S), t, font=font(REG, 34), fill=MUTED, anchor="lm")
        return
    for i, line in enumerate(data[key][:6]):
        yc = 160 + i * 72
        n, name, room = parse(line)
        if n is None:
            f, t = fit(d, name, REG, 34, colw)
            d.text((x * S, yc * S), t, font=f, fill=TEXT, anchor="lm")
            continue
        cx = x + 22
        d.ellipse([(cx - 22) * S, (yc - 22) * S, (cx + 22) * S, (yc + 22) * S], fill=BADGE)
        d.text((cx * S, yc * S), n, font=font(BOLD, 26), fill=accent, anchor="mm")
        room_w = d.textlength(room, font=font(REG, 30)) / S if room else 0
        f, t = fit(d, name, BOLD, 36, colw - 64 - room_w - (16 if room else 0))
        d.text(((x + 64) * S, yc * S), t, font=f, fill=TEXT, anchor="lm")
        if room:
            d.text(((x + colw) * S, yc * S), room, font=font(REG, 30), fill=accent, anchor="rm")


def main():
    now = dt.datetime.now(ZoneInfo(TZ_NAME))
    today = now.date()
    try:
        with open("schedule.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, W * S - 1, H * S - 1], radius=44 * S, fill=BG)
    pad, gap = 48, 56
    colw = (W - 2 * pad - gap) // 2
    column(d, pad, colw, "Сегодня", today, data, BLUE)
    column(d, pad + colw + gap, colw, "Завтра", today + dt.timedelta(days=1), data, AMBER)
    mid = (pad + colw + gap // 2) * S
    d.line([mid, 44 * S, mid, (H - 70) * S], fill=LINE, width=2 * S)
    d.text(((W - pad) * S, (H - 34) * S), f"обновлено {now:%H:%M}",
           font=font(REG, 20), fill=MUTED, anchor="rm")
    img.resize((W, H), Image.LANCZOS).save("schedule.png", optimize=True)


if __name__ == "__main__":
    main()
