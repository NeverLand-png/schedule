"""
Расписание ВУМК -> текст для виджета (группа GROUP, сегодня и завтра).

Берёт фото расписания с открытой веб-страницы канала (ключи и вход в Telegram не нужны),
распознаёт таблицу (Tesseract OCR), находит строку группы и пишет schedule.txt и schedule.json.
Запускается автоматически через GitHub Actions (см. schedule.yml).
Проверка на готовой картинке:  python schedule_widget.py --test photo.jpg
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

GROUP = os.environ.get("GROUP", "942")
CHANNEL = "RaspisanieVumk"
LANG = os.environ.get("OCR_LANG", "rus")
SCALE = 2
TZ_NAME = os.environ.get("TZ_NAME", "Europe/Moscow")
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("ма", 5), ("июн", 6),
          ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]


def cluster(xs, gap=8):
    """Склеивает соседние пиксели линии в одну координату."""
    out, cur = [], []
    for x in xs:
        if cur and x - cur[-1] > gap:
            out.append(sum(cur) / len(cur))
            cur = []
        cur.append(x)
    if cur:
        out.append(sum(cur) / len(cur))
    return out


def prep(raw):
    """Картинка -> серая, без линий, маски линий и координаты линий таблицы."""
    gray = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError("это не картинка")
    gray = cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = inv.shape
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 20, 20))))
    horiz = cv2.morphologyEx(inv, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 20), 1)))
    lines = cv2.dilate(cv2.bitwise_or(vert, horiz), np.ones((3, 3), np.uint8))
    clean = cv2.bitwise_not(cv2.subtract(inv, lines))
    xs = cluster(np.where(vert.sum(axis=0) > 0)[0], gap=8 * SCALE)
    ys = cluster(np.where(horiz.sum(axis=1) > 0)[0], gap=4 * SCALE)
    return gray, clean, inv, xs, ys


def find_date(clean):
    top = clean[: int(clean.shape[0] * 0.3)]
    text = pytesseract.image_to_string(top, lang=LANG, config="--psm 6")
    m = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]{3,9})\s+(20\d\d)", text)
    if not m:
        return None
    month = next((n for p, n in MONTHS if m.group(2).lower().startswith(p)), None)
    try:
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    except (TypeError, ValueError):
        return None


def find_group_row(clean, xs):
    """Ищет номер группы в первой колонке, возвращает (центр по y, высота цифр)."""
    x0 = int(xs[0]) if xs else 0
    x1 = int(xs[1]) if len(xs) > 1 else int(clean.shape[1] * 0.12)
    d = pytesseract.image_to_data(clean[:, x0:x1], lang="eng", output_type=Output.DICT,
                                  config="--psm 6 -c tessedit_char_whitelist=0123456789")
    for i, t in enumerate(d["text"]):
        if t.strip() == GROUP:
            return d["top"][i] + d["height"][i] / 2, d["height"][i]
    return None


def ocr_cell(gray, x0, x1, y0, y1):
    m = 8
    crop = gray[int(y0) + m:int(y1) - m, int(x0) + m:int(x1) - m]
    if crop.size == 0:
        return ""
    crop = cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (bw < 128).mean() < 0.01:  # пустая ячейка
        return ""
    bw = cv2.copyMakeBorder(bw, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
    text = pytesseract.image_to_string(bw, lang=LANG, config="--psm 7")
    text = " ".join(re.sub(r"[|_\[\]]+", " ", text).split())
    return text if re.search(r"\w", text) else ""


def read_row(gray, clean, inv, xs, ys, anchor, dbg):
    ay, ah = anchor
    above = [y for y in ys if y < ay - ah * 0.4]
    below = [y for y in ys if y > ay + ah * 0.4]
    y0 = max(above) if above else ay - ah
    y1 = min(below) if below else ay + ah
    if y1 - y0 > 3.5 * ah:
        y0, y1 = ay - ah * 0.8, ay + ah * 0.8
    dbg["row_y"] = [int(y0), int(y1)]
    if len(xs) != 12:  # линии таблицы нашлись не все - читаем строку целиком
        dbg["fallback"] = True
        line = clean[int(y0) + 3:int(y1) - 3]
        return [" ".join(pytesseract.image_to_string(line, lang=LANG, config="--psm 7").split())]
    m = 5
    band = inv[int(y0) + m:int(y1) - m]
    ncells = len(xs) - 1
    groups, first = [], 0
    for k in range(ncells - 1):  # граница xs[k+1] между ячейками k и k+1
        x = int(xs[k + 1])
        seg = band[:, max(x - 4, 0):x + 5]
        line_here = k == 0 or (seg.size and (seg.max(axis=1) > 0).mean() > 0.85)
        ink = clean[int(y0) + m:int(y1) - m, max(x - 3, 0):x + 4]
        crossed = ink.size and (ink < 128).any(axis=1).mean() > 0.04  # текст идёт через границу
        if line_here or not crossed:
            groups.append((first, k))
            first = k + 1
    groups.append((first, ncells - 1))
    cells, items = {}, []
    for a, b in groups:
        if a == 0:
            continue
        text = ocr_cell(gray, xs[a], xs[b + 1], y0, y1)
        dbg.setdefault("cells", []).append(f"{a}-{b}: {text}")
        if not text:
            continue
        if a == b:
            cells[a] = text
        else:
            items.append((a, text))
    for n in range(1, 6):
        name, room = cells.get(2 * n - 1, ""), cells.get(2 * n, "")
        if name or room:
            items.append((2 * n - 1, f"{n}. {name}" + (f" ({room})" if room else "")))
    return [t for _, t in sorted(items, key=lambda it: it[0])]


def parse_image(raw):
    gray, clean, inv, xs, ys = prep(raw)
    dbg = {"size": f"{gray.shape[1] // SCALE}x{gray.shape[0] // SCALE}", "vlines": len(xs)}
    date = find_date(clean)
    dbg["date"] = str(date)
    anchor = find_group_row(clean, xs)
    if anchor is None:
        return date, None, dbg
    return date, read_row(gray, clean, inv, xs, ys, anchor, dbg), dbg


def day_title(label, d):
    return f"{label}, {WEEKDAYS[d.weekday()]} {d:%d.%m}"


def build_text(result, today):
    blocks = []
    for label, d in (("Сегодня", today), ("Завтра", today + dt.timedelta(days=1))):
        if d not in result:
            body = ["расписание ещё не выложено"]
        elif result[d] is None:
            body = ["в изменениях нет - по основному расписанию"]
        else:
            body = result[d]
        blocks.append("\n".join([day_title(label, d)] + body))
    return "\n\n".join(blocks)


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def photo_urls():
    """Ссылки на фото со страницы канала, новые первыми."""
    html = http_get(f"https://t.me/s/{CHANNEL}").decode("utf-8", "ignore")
    urls = re.findall(r"background-image:\s*url\(['\"]?(https://[^'\")]+)", html)
    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out[::-1][:24]


def fetch():
    today = dt.datetime.now(ZoneInfo(TZ_NAME)).date()
    wanted = {today, today + dt.timedelta(days=1)}
    result, seen, log = {}, set(), []
    for url in photo_urls():
        try:
            raw = http_get(url)
            date, lessons, dbg = parse_image(raw)
        except Exception as e:  # битая картинка - просто пропускаем
            log.append(f"пропуск: {e}")
            continue
        log.append(str(dbg))
        if date in wanted and date not in result:
            if lessons:
                result[date] = lessons
                with open("debug_page.jpg", "wb") as f:
                    f.write(raw)
            else:
                seen.add(date)  # лист за эту дату есть, но строки группы на нём нет
        if wanted <= set(result):
            break
    for d in wanted:
        if d not in result and d in seen:
            result[d] = None
    return today, result, log


def main():
    today, result, log = fetch()
    text = build_text(result, today)
    with open("schedule.txt", "w", encoding="utf-8") as f:
        f.write(text)
    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in result.items()}, f, ensure_ascii=False, indent=1)
    with open("debug.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(log))
    print(text)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        with open(sys.argv[2], "rb") as f:
            for part in parse_image(f.read()):
                print(part)
    else:
        main()
