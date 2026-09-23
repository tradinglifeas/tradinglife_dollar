"""
اسکریپت ساخت و ارسال تصویر روزانه‌ی قیمت طلا و سکه (بازار ایران) + طلای جهانی به تلگرام.
مستقل از اسکریپت‌های نقره و بیت‌کوینه.

تغییر «امروز» مستقیماً از خود API گرفته می‌شه.
تغییر «ماه گذشته» و «سال گذشته» از یه تاریخچه‌ی خودساخته (که هر روز
یه رکورد جدید بهش اضافه می‌شه) محاسبه می‌شه - برای همین، مقایسه‌ی ماه
از حدود ۳۰ روز دیگه، و مقایسه‌ی سال از حدود یک سال دیگه کامل می‌شه.
یه نمودار ساده از روند قیمت طلای ۱۸ عیار هم به تصویر اضافه می‌شه.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]

TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
GOLD_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency.php"
COMMODITY_API_URL = "https://BrsApi.ir/Api/Market/Commodity.php"
HISTORY_FILE = "gold_history_series.json"
MAX_HISTORY_DAYS = 400

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

FONT_REGULAR = "Vazirmatn-Regular.ttf"
FONT_BOLD = "Vazirmatn-Bold.ttf"

UP_COLOR = (80, 200, 120)
DOWN_COLOR = (230, 90, 90)
NEUTRAL_COLOR = (170, 170, 180)
GOLD_COLOR = (255, 205, 86)


def load_font(path, size):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


def find_item(items, symbol=None, name_contains=None):
    for item in items:
        item_symbol = str(item.get("symbol", "")).upper()
        item_name = str(item.get("name", ""))
        if symbol and item_symbol == symbol.upper():
            return item
        if name_contains and name_contains in item_name:
            return item
    return None


def get_gold_prices():
    params = {"key": BRSAPI_KEY, "section": "gold"}
    response = requests.get(GOLD_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ طلا JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return {}

    print(f"داده‌ی طلا: {data}")
    gold_list = data.get("gold", [])
    return {
        "gold_18": find_item(gold_list, symbol="IR_GOLD_18K") or find_item(gold_list, name_contains="طلای 18") or (gold_list[0] if gold_list else None),
        "bahar_azadi": find_item(gold_list, symbol="IR_COIN_BAHAR") or find_item(gold_list, name_contains="بهار آزادی"),
        "emami": find_item(gold_list, symbol="IR_COIN_EMAMI") or find_item(gold_list, name_contains="امامی"),
        "half_coin": find_item(gold_list, symbol="IR_COIN_HALF") or find_item(gold_list, name_contains="نیم"),
        "quarter_coin": find_item(gold_list, symbol="IR_COIN_QUARTER") or find_item(gold_list, name_contains="ربع"),
    }


def get_global_gold_price():
    params = {"key": BRSAPI_KEY}
    response = requests.get(COMMODITY_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ کامودیتی JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return None

    print(f"داده‌ی کامودیتی (طلای جهانی): {data}")
    all_items = []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                all_items.extend(value)
    elif isinstance(data, list):
        all_items = data

    return find_item(all_items, symbol="XAU", name_contains="طلا")


def format_number(value):
    try:
        return f"{int(round(float(value))):,}"
    except (ValueError, TypeError):
        return str(value)


def get_daily_change(item):
    if not item:
        return None, None
    change_percent = item.get("change_percent")
    change_value = item.get("change_value")
    if change_percent is None:
        return None, None
    try:
        change_percent = float(change_percent)
    except (ValueError, TypeError):
        return None, None
    try:
        change_value = float(change_value) if change_value is not None else None
    except (ValueError, TypeError):
        change_value = None
    return change_value, change_percent


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return []


def save_history(records):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(records[-MAX_HISTORY_DAYS:], f, ensure_ascii=False, indent=2)


def update_history(records, today_str, snapshot):
    if records and records[-1]["date"] == today_str:
        records[-1]["prices"] = snapshot
    else:
        records.append({"date": today_str, "prices": snapshot})
    return records


def find_record_near(records, target_date, tolerance_days=5):
    best = None
    best_diff = None
    for rec in records:
        try:
            rec_date = datetime.strptime(rec["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        diff = abs((rec_date - target_date).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best = rec
            best_diff = diff
    return best


def get_period_change(records, key, current_price, days_ago):
    if current_price is None:
        return None, None
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    target_date = today - timedelta(days=days_ago)
    record = find_record_near(records, target_date, tolerance_days=5)
    if not record:
        return None, None
    old_price = record.get("prices", {}).get(key)
    if old_price is None:
        return None, None
    try:
        old_price = float(old_price)
        current_price = float(current_price)
    except (ValueError, TypeError):
        return None, None
    if old_price == 0:
        return None, None
    diff = current_price - old_price
    percent = (diff / old_price) * 100
    return diff, percent


def change_line(diff, percent, unit):
    if percent is None:
        return "—"
    sign = "+" if percent
