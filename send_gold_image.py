"""
اسکریپت ساخت و ارسال تصویر روزانه‌ی قیمت طلا و سکه (بازار ایران) به تلگرام.
مستقل از اسکریپت‌های نقره و بیت‌کوینه.
"""

import os
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont
import arabic_reshaper
from bidi.algorithm import get_display

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]

TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
GOLD_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency.php"
HISTORY_FILE = "gold_history.json"

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


def reshape(text):
    return get_display(arabic_reshaper.reshape(text))


def find_item(items, symbol=None, name_contains=None):
    for item in items:
        item_symbol = str(item.get("symbol", "")).upper()
        item_name = str(item.get("name", ""))
        if symbol and item_symbol == symbol.upper():
            return item
        if name_contains and name_contains in item_name:
            return item
    return None


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


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
        "gold_18": find_item(gold_list, name_contains="طلای 18") or (gold_list[0] if gold_list else None),
        "bahar_azadi": find_item(gold_list, name_contains="بهار آزادی"),
        "emami": find_item(gold_list, name_contains="امامی"),
        "half_coin": find_item(gold_list, name_contains="نیم"),
        "quarter_coin": find_item(gold_list, name_contains="ربع"),
    }


def format_number(value):
    try:
        return f"{int(float(value)):,}"
    except (ValueError, TypeError):
        return str(value)


def compute_change(history, key, current_price, today_str):
    entry = history.get(key, {})
    last_date = entry.get("last_date")
    last_price = entry.get("last_price")
    yesterday_price = entry.get("yesterday_price")

    if last_date != today_str:
        if last_price is not None:
            yesterday_price = last_price
        entry["yesterday_price"] = yesterday_price

    entry["last_date"] = today_str
    entry["last_price"] = current_price
    history[key] = entry

    if yesterday_price is None or current_price is None:
        return None, None
    try:
        current_price = float(current_price)
        yesterday_price = float(yesterday_price)
    except (ValueError, TypeError):
        return None, None
    if yesterday_price == 0:
        return None, None
    diff = current_price - yesterday_price
    percent = (diff / yesterday_price) * 100
    return diff, percent


def draw_row(draw, y, margin, width, label, item, history, key, today_str,
             unit, label_font, value_font, change_font, card_color,
             text_color, label_color, card_height=95):
    draw.rounded_rectangle([margin, y, width - margin, y + card_height], radius=16, fill=card_color)

    label_text = reshape(label)
    bbox = draw.textbbox((0, 0), label_text, font=label_font)
    draw.text((width - margin - 30 - (bbox[2] - bbox[0]), y + 16), label_text, font=label_font, fill=label_color)

    price = item.get("price") if item else None
    value_text = reshape(f"{format_number(price)} {unit}" if price else "نامشخص")
    draw.text((margin + 30, y + 14), value_text, font=value_font, fill=text_color)

    diff, percent = compute_change(history, key, price, today_str)
    if diff is not None:
        sign = "+" if diff > 0 else ("-" if diff < 0 else "")
        change_color = UP_COLOR if diff > 0 else (DOWN_COLOR if diff < 0 else NEUTRAL_COLOR)
        change_text = f"{sign}{abs(percent):.2f}%  ({sign}{format_number(abs(diff))} {unit})"
    else:
        change_color = NEUTRAL_COLOR
        change_text = reshape("داده‌ی دیروز نداریم")

    draw.text((margin + 30, y + 58), change_text, font=change_font, fill=change_color)
    return y + card_height + 16


def build_image(gold, history, today_str, now_text):
    width, max_height = 900, 900
    bg_color = (18, 18, 24)
    card_color = (30, 30, 40)
    text_color = (240, 240, 240)
    label_color = (170, 170, 180)

    img = Image.new("RGB", (width, max_height), bg_color)
    draw = ImageDraw.Draw(img)

    title_font = ImageFont.truetype(FONT_BOLD, 50)
    label_font = ImageFont.truetype(FONT_REGULAR, 30)
    value_font = ImageFont.truetype(FONT_BOLD, 32)
    change_font = ImageFont.truetype(FONT_REGULAR, 24)
    footer_font = ImageFont.truetype(FONT_REGULAR, 22)

    margin = 60
    y = 45

    title = reshape("قیمت طلا و سکه (بازار ایران)")
    bbox = draw.textbbox((0, 0), title, font=title_font)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, y), title, font=title_font, fill=GOLD_COLOR)
    y += 100

    gold_rows = [
        ("طلای ۱۸ عیار (هر گرم)", gold.get("gold_18"), "gold_18"),
        ("سکه بهار آزادی", gold.get("bahar_azadi"), "bahar_azadi"),
        ("سکه امامی", gold.get("emami"), "emami"),
        ("نیم سکه", gold.get("half_coin"), "half_coin"),
        ("ربع سکه", gold.get("quarter_coin"), "quarter_coin"),
    ]
    for label, item, key in gold_rows:
        y = draw_row(draw, y, margin, width, label, item, history, key, today_str,
                     "تومان", label_font, value_font, change_font, card_color, text_color, label_color)

    y += 15
    footer = reshape(now_text)
    bbox = draw.textbbox((0, 0), footer, font=footer_font)
    footer_y = y + 10
    draw.text(((width - (bbox[2] - bbox[0])) / 2, footer_y), footer, font=footer_font, fill=label_color)

    final_height = footer_y + 60
    img = img.crop((0, 0, width, min(final_height, max_height)))

    output_path = "gold_price.png"
    img.save(output_path)
    return output_path


def send_photo(image_path):
    with open(image_path, "rb") as photo:
        response = requests.post(
            TELEGRAM_PHOTO_URL, data={"chat_id": CHAT_ID}, files={"photo": photo}, timeout=30,
        )
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال عکس: {result}")
        raise SystemExit(1)
    print("عکس طلا با موفقیت ارسال شد.")


def main():
    iran_time = datetime.now(ZoneInfo("Asia/Tehran"))
    now_text = "بروزرسانی: " + iran_time.strftime("%Y/%m/%d - %H:%M") + " به وقت ایران"
    today_str = iran_time.strftime("%Y-%m-%d")

    gold = get_gold_prices()
    history = load_history()

    image_path = build_image(gold, history, today_str, now_text)
    save_history(history)
    send_photo(image_path)


if __name__ == "__main__":
    main()
