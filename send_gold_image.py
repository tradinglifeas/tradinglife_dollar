"""
اسکریپت ساخت و ارسال تصویر روزانه‌ی قیمت طلا و سکه (بازار ایران) به تلگرام.
مستقل از اسکریپت‌های نقره و بیت‌کوینه.

تغییر قیمت نسبت به دیروز مستقیماً از خود API گرفته می‌شه (فیلدهای
change_value و change_percent)، نیازی به ذخیره‌ی تاریخچه نیست.
"""

import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]

TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
GOLD_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency.php"

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


def format_number(value):
    try:
        return f"{int(round(float(value))):,}"
    except (ValueError, TypeError):
        return str(value)


def get_change(item):
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


def draw_row(draw, y, margin, width, label, item, unit, label_font, value_font,
             change_font, card_color, text_color, label_color, card_height=95):
    draw.rounded_rectangle([margin, y, width - margin, y + card_height], radius=16, fill=card_color)

    draw.text((width - margin - 30, y + 16), label, font=label_font, fill=label_color,
              direction="rtl", anchor="ra")

    price = item.get("price") if item else None
    value_text = f"{format_number(price)} {unit}" if price else "نامشخص"
    draw.text((margin + 30, y + 14), value_text, font=value_font, fill=text_color,
              direction="ltr", anchor="la")

    change_value, change_percent = get_change(item)
    if change_percent is not None:
        sign = "+" if change_percent > 0 else ("-" if change_percent < 0 else "")
        change_color = UP_COLOR if change_percent > 0 else (DOWN_COLOR if change_percent < 0 else NEUTRAL_COLOR)
        if change_value is not None:
            change_text = f"{sign}{abs(change_percent):.2f}%  ({sign}{format_number(abs(change_value))} {unit})"
        else:
            change_text = f"{sign}{abs(change_percent):.2f}%"
    else:
        change_color = NEUTRAL_COLOR
        change_text = "داده‌ی تغییرات نداریم"

    draw.text((margin + 30, y + 58), change_text, font=change_font, fill=change_color,
              direction="ltr", anchor="la")

    return y + card_height + 16


def build_image(gold, now_text):
    width, max_height = 900, 900
    bg_color = (18, 18, 24)
    card_color = (30, 30, 40)
    text_color = (240, 240, 240)
    label_color = (170, 170, 180)

    img = Image.new("RGB", (width, max_height), bg_color)
    draw = ImageDraw.Draw(img)

    title_font = load_font(FONT_BOLD, 50)
    label_font = load_font(FONT_REGULAR, 30)
    value_font = load_font(FONT_BOLD, 32)
    change_font = load_font(FONT_REGULAR, 24)
    footer_font = load_font(FONT_REGULAR, 22)

    margin = 60
    y = 45

    title = "قیمت طلا و سکه (بازار ایران)"
    draw.text((width / 2, y), title, font=title_font, fill=GOLD_COLOR,
              direction="rtl", anchor="ma")
    y += 100

    gold_rows = [
        ("طلای ۱۸ عیار (هر گرم)", gold.get("gold_18")),
        ("سکه بهار آزادی", gold.get("bahar_azadi")),
        ("سکه امامی", gold.get("emami")),
        ("نیم سکه", gold.get("half_coin")),
        ("ربع سکه", gold.get("quarter_coin")),
    ]
    for label, item in gold_rows:
        y = draw_row(draw, y, margin, width, label, item, "تومان",
                     label_font, value_font, change_font, card_color, text_color, label_color)

    y += 15
    draw.text((width / 2, y + 10), now_text, font=footer_font, fill=label_color,
              direction="rtl", anchor="ma")

    final_height = y + 10 + 60
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

    gold = get_gold_prices()
    image_path = build_image(gold, now_text)
    send_photo(image_path)


if __name__ == "__main__":
    main()
