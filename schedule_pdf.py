"""
Расписание ВУМК -> текст для виджета (группа GROUP, сегодня и завтра).

Берёт PDF «Изменения к стабильному расписанию» прямо с сайта колледжа
(http://www.vumk.ru/rasp/raspisanie.pdf). Это настоящий текст, а не картинка,
поэтому распознавание (OCR) не нужно. Пишет schedule.txt, schedule.json, debug.txt.
Запускается автоматически через GitHub Actions (см. schedule.yml).
Проверка на готовом файле:  python schedule_pdf.py --test raspisanie.pdf
"""
import datetime as dt
import io
import json
import os
import re
import sys
import urllib.request
from zoneinfo import ZoneInfo

import pdfplumber

PDF_URL = os.environ.get("PDF_URL", "http://www.vumk.ru/rasp/raspisanie.pdf")
GROUP = os.environ.get("GROUP", "942")
TZ_NAME = os.environ.get("TZ_NAME", "Europe/Moscow")
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = [("янв", 1), ("фев", 2), ("мар", 3), ("апр", 4), ("ма", 5), ("июн", 6),
          ("июл", 7), ("авг", 8), ("сен", 9), ("окт", 10), ("ноя", 11), ("дек", 12)]
GAP = 4.5  # слова ближе этого расстояния (в пунктах) считаем одной фразой


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def find_date(text):
    m = re.search(r"(\d{1,2})\s+([А-Яа-яЁё]{3,9})\s+(20\d\d)", text)
    if not m:
        return None
    month = next((n for p, n in MONTHS if m.group(2).lower().startswith(p)), None)
    try:
        return dt.date(int(m.group(3)), month, int(m.group(1)))
    except (TypeError, ValueError):
        return None


def header_cells(words):
    """Центры колонок из шапки: '1 пара', 'каб', '2 пара', ... -> {('p',1): x, ('k',1): x ...}."""
    pars = [w for w in words if w["text"].lower() == "пара"]
    if not pars:
        return None
    top = max(set(round(w["top"]) for w in pars), key=lambda t: sum(round(w["top"]) == t for w in pars))
    row = [w for w in words if abs(w["top"] - top) < 3]
    cells = {}
    for w in row:
        if w["text"].lower() == "пара":
            digit = [d for d in row if d["text"] in "12345" and 0 <= w["x0"] - d["x1"] < 8]
            if digit:
                cells[("p", int(digit[0]["text"]))] = (digit[0]["x0"] + w["x1"]) / 2
    kabs = sorted((w for w in row if w["text"].lower() == "каб"), key=lambda w: w["x0"])
    for i, w in enumerate(kabs[:5], start=1):
        cells[("k", i)] = (w["x0"] + w["x1"]) / 2
    if ("p", 5) in cells and ("k", 5) not in cells and ("p", 4) in cells and ("k", 4) in cells:
        cells[("k", 5)] = cells[("p", 5)] + (cells[("k", 4)] - cells[("p", 4)])
    return cells if len(cells) >= 8 else None


def phrases(row_words):
    out = []
    for w in sorted(row_words, key=lambda w: w["x0"]):
        if out and w["x0"] - out[-1]["x1"] < GAP:
            out[-1]["text"] += " " + w["text"]
            out[-1]["x1"] = w["x1"]
        else:
            out.append({"text": w["text"], "x0": w["x0"], "x1": w["x1"]})
    return out


def from_grid(page, words, dbg):
    anchors = [w for w in words if w["text"] == GROUP and w["x0"] < page.width * 0.25]
    cells = header_cells(words)
    if not anchors:
        return None
    a = anchors[0]
    tol = (a["bottom"] - a["top"]) * 0.6
    row = [w for w in words if w is not a and abs(w["top"] - a["top"]) <= tol and w["x0"] > a["x1"]]
    ph = phrases(row)
    dbg["row"] = " | ".join(p["text"] for p in ph)
    if not ph:
        return []
    if cells is None:
        return [" ".join(p["text"] for p in ph)]
    if re.match(r"^(УП|ПП)\b", ph[0]["text"]):  # практика: одна общая запись на всю строку
        return [" ".join(p["text"] for p in ph)]
    filled = {}
    for p in ph:
        c = (p["x0"] + p["x1"]) / 2
        key = min(cells, key=lambda k: abs(cells[k] - c))
        filled[key] = (filled.get(key, "") + " " + p["text"]).strip()
    out = []
    for n in range(1, 6):
        name, room = filled.get(("p", n), ""), filled.get(("k", n), "")
        if name or room:
            out.append(f"{n}. {name}" + (f" ({room})" if room else ""))
    return out


def from_text(text):
    for line in text.splitlines():
        m = re.match(rf"^{re.escape(GROUP)}\s+(.+)$", line.strip())
        if m:
            return [m.group(1)]
    return None


def parse_pdf(data):
    result, seen, log = {}, set(), []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            date = find_date(text)
            dbg = {"page": i, "date": str(date)}
            if date is None:
                log.append(str(dbg))
                continue
            words = page.extract_words(x_tolerance=2, y_tolerance=2)
            lessons = from_grid(page, words, dbg) or from_text(text)
            log.append(str(dbg))
            if date in result:
                continue
            if lessons:
                result[date] = lessons
            else:
                seen.add(date)  # лист за эту дату есть, но строки группы на нём нет
    for d in seen:
        result.setdefault(d, None)
    return result, log


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


def main():
    today = dt.datetime.now(ZoneInfo(TZ_NAME)).date()
    result, log = parse_pdf(download(PDF_URL))
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
            res, log = parse_pdf(f.read())
        print("\n".join(log))
        for d in sorted(res):
            print(d, res[d])
    else:
        main()
