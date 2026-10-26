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
