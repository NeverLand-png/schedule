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

GROUP = "942"
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
    """Картинка -> (очищенная от линий картинка, x-координаты вертикальных линий)."""
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    img = cv2.resize(img, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_CUBIC)
    _, inv = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    h, w = inv.shape
    vert = cv2.morphologyEx(inv, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 20, 20))))
    horiz = cv2.morphologyEx(inv, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 8, 20), 1)))
    lines = cv2.dilate(cv2.bitwise_or(vert, horiz), np.ones((3, 3), np.uint8))
    clean = cv2.bitwise_not(cv2.subtract(inv, lines))
    bounds = cluster(np.where(vert.sum(axis=0) > 0)[0], gap=8 * SCALE)
    return clean, bounds


def ocr_words(clean):
    d = pytesseract.image_to_data(clean, lang=LANG, config="--psm 6", output_type=Output.DICT)
    words = []
    for i, t in enumerate(d["text"]):
        t = t.strip()
        if t and float(d["conf"][i]) >= 0:
            words.append(dict(t=t, x=d["left"][i] + d["width"][i] / 2,
                              y=d["top"][i] + d["height"][i] / 2, h=d["height"][i]))
    return words


def find_date(words):
    text = " ".join(w["t"] for w in words[:120])
    m = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]{3,9})\s+(20\d\d)", text)
    if not m:
        return None
    month = next((n for p, n in MONTHS if m.group(2).lower().startswith(p)), None)
    try:
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    except (TypeError, ValueError):
        return None


def row_cells(words, bounds, width):
    """Находит строку группы и раскладывает слова по ячейкам таблицы."""
    anchors = [w for w in words if w["t"] == GROUP and w["x"] < width * 0.15]
    if not anchors:
        return None
    a = anchors[0]
    tol = a["h"] * 0.55
    row = sorted((w for w in words if abs(w["y"] - a["y"]) <= tol), key=lambda w: w["x"])
    cells = {}
    for w in row:
        idx = sum(1 for b in bounds if b < w["x"]) - 1
        cells.setdefault(idx, []).append(w["t"])
    return {i: " ".join(v) for i, v in cells.items()}, [w["t"] for w in row]


def lessons_from_cells(cells, raw_row, n_bounds):
    """Ячейки: 0 группа, затем пара1, каб1, пара2, каб2 ... пара5, каб5."""
    if n_bounds != 12:  # линии таблицы нашлись не все - отдаём строку как есть
        return [" ".join(raw_row[1:])]
    out = []
    for n in range(1, 6):
        name, room = cells.get(2 * n - 1, ""), cells.get(2 * n, "")
        if name or room:
            out.append(f"{n}. {name}" + (f" ({room})" if room else ""))
    return out


def parse_image(raw):
    clean, bounds = prep(raw)
    words = ocr_words(clean)
    date = find_date(words)
    found = row_cells(words, bounds, clean.shape[1])
    if found is None:
        return date, None
    cells, raw_row = found
    return date, lessons_from_cells(cells, raw_row, len(bounds))


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
    result, seen = {}, set()
    for url in photo_urls():
        try:
            date, lessons = parse_image(http_get(url))
        except Exception as e:  # битая картинка - просто пропускаем
            print("пропуск:", e)
            continue
        if date in wanted and date not in result:
            if lessons:
                result[date] = lessons
            else:
                seen.add(date)  # лист за эту дату есть, но строки группы на нём нет
        if wanted <= set(result):
            break
    for d in wanted:
        if d not in result and d in seen:
            result[d] = None
    return today, result


def main():
    today, result = fetch()
    text = build_text(result, today)
    with open("schedule.txt", "w", encoding="utf-8") as f:
        f.write(text)
    with open("schedule.json", "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in result.items()}, f, ensure_ascii=False, indent=1)
    print(text)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        with open(sys.argv[2], "rb") as f:
            print(parse_image(f.read()))
    else:
        main()
