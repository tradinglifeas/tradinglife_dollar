"""
اسکریپت ساخت و ارسال تصویر روزانه‌ی قیمت نقره (بازار جهانی) به تلگرام.
مستقل از اسکریپت‌های طلا و بیت‌کوینه.

تغییر «امروز» مستقیماً از خود API گرفته می‌شه.
تغییر «ماه گذشته» و «سال گذشته» از یه تاریخچه‌ی خودساخته محاسبه می‌شه.
یه نمودار ساده از روند قیمت هم به تصویر اضافه می‌شه.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]

TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
COMMODITY_API_URL = "https://Api.BrsApi.ir/Market/Commodity.php"
HISTORY_FILE = "silver_history_series.json"
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
SILVER_COLOR = (200, 200, 210)


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


def get_silver_price():
    params = {"key": BRSAPI_KEY}
    response = requests.get(COMMODITY_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ کامودیتی JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return None

    print(f"داده‌ی کامودیتی: {data}")
    all_items = []
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                all_items.extend(value)
    elif isinstance(data, list):
        all_items = data

    return find_item(all_items, symbol="XAGUSD") or find_item(all_items, symbol="XAG", name_contains="نقره")


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


def update_history(records, today_str, price):
    if records and records[-1]["date"] == today_str:
        records[-1]["price"] = price
    else:
        records.append({"date": today_str, "price": price})
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


def get_period_change(records, current_price, days_ago):
    if current_price is None:
        return None, None
    today = datetime.now(ZoneInfo("Asia/Tehran")).date()
    target_date = today - timedelta(days=days_ago)
    record = find_record_near(records, target_date, tolerance_days=5)
    if not record:
        return None, None
    old_price = record.get("price")
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
    sign = "+" if percent > 0 else ("-" if percent < 0 else "")
    color = UP_COLOR if percent > 0 else (DOWN_COLOR if percent < 0 else NEUTRAL_COLOR)
    if diff is not None:
        text = f"{sign}{abs(percent):.1f}% ({sign}{unit}{format_number(abs(diff))})"
    else:
        text = f"{sign}{abs(percent):.1f}%"
    return text, color


def add_silver_glow(bg_color, width, height):
    glow_layer = Image.new("RGB", (width, height), bg_color)
    glow_draw = ImageDraw.Draw(glow_layer)
    blobs = [
        (width * 0.15, height * 0.05, 240, (210, 215, 225)),
        (width * 0.90, height * 0.15, 200, (190, 195, 205)),
        (width * 0.10, height * 0.55, 180, (210, 215, 225)),
    ]
    for cx, cy, r, color in blobs:
        glow_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(110))
    base = Image.new("RGB", (width, height), bg_color)
    return Image.blend(base, glow_layer, alpha=0.18)


def draw_chart(draw, x, y, chart_width, chart_height, records, font, color, label_color):
    points = [float(rec["price"]) for rec in records if rec.get("price") is not None]

    draw.text((x + chart_width, y - 40), "روند قیمت (بازه‌ی ثبت‌شده)", font=font, fill=label_color,
              direction="rtl", anchor="ra")

    if len(points) < 2:
        draw.text((x + chart_width / 2, y + chart_height / 2), "هنوز داده‌ی کافی برای نمودار نداریم",
                  font=font, fill=label_color, direction="rtl", anchor="mm")
        return

    min_p, max_p = min(points), max(points)
    if min_p == max_p:
        max_p = min_p + 1
    step_x = chart_width / (len(points) - 1)

    coords = []
    for i, p in enumerate(points):
        px = x + i * step_x
        py = y + chart_height - ((p - min_p) / (max_p - min_p)) * chart_height
        coords.append((px, py))

    draw.line(coords, fill=color, width=3, joint="curve")
    for cx, cy in [coords[0], coords[-1]]:
        r = 4
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def build_image(silver, records, now_text):
    width, max_height = 900, 800
    bg_color = (18, 18, 24)
    card_color = (30, 30, 40)
    text_color = (240, 240, 240)
    label_color = (170, 170, 180)

    img = add_silver_glow(bg_color, width, max_height)
    draw = ImageDraw.Draw(img)

    title_font = load_font(FONT_BOLD, 46)
    label_font = load_font(FONT_REGULAR, 30)
    value_font = load_font(FONT_BOLD, 34)
    small_font = load_font(FONT_REGULAR, 22)
    footer_font = load_font(FONT_REGULAR, 22)

    margin = 55
    y = 45

    draw.text((width / 2, y), "قیمت نقره (بازار جهانی)", font=title_font, fill=SILVER_COLOR,
              direction="rtl", anchor="ma")
    y += 100

    card_height = 130
    draw.rounded_rectangle([margin, y, width - margin, y + card_height], radius=16, fill=card_color)

    draw.text((width - margin - 30, y + 18), "هر اونس نقره", font=label_font, fill=label_color,
              direction="rtl", anchor="ra")

    price = silver.get("price") if silver else None
    value_text = f"${format_number(price)}" if price else "نامشخص"
    draw.text((margin + 30, y + 16), value_text, font=value_font, fill=text_color,
              direction="ltr", anchor="la")

    day_diff, day_percent = get_daily_change(silver)
    day_result = change_line(day_diff, day_percent, "$")
    month_diff, month_percent = get_period_change(records, price, 30)
    month_result = change_line(month_diff, month_percent, "$")
    year_diff, year_percent = get_period_change(records, price, 365)
    year_result = change_line(year_diff, year_percent, "$")

    labels = ["روز:", "ماه:", "سال:"]
    results = [day_result, month_result, year_result]
    row_y = y + 65
    cursor_x = width - margin - 30
    for lbl, res in zip(labels, results):
        if res == "—":
            piece = f"{lbl} —"
            color = NEUTRAL_COLOR
        else:
            text, color = res
            piece = f"{lbl} {text}"
        bbox = draw.textbbox((0, 0), piece, font=small_font, direction="ltr")
        piece_width = bbox[2] - bbox[0]
        draw.text((cursor_x - piece_width, row_y), piece, font=small_font, fill=color, direction="ltr", anchor="la")
        cursor_x -= piece_width + 25

    y += card_height + 30

    chart_x = margin
    chart_y = y + 50
    chart_w = width - margin * 2
    chart_h = 200
    draw_chart(draw, chart_x, chart_y, chart_w, chart_h, records, small_font, SILVER_COLOR, label_color)
    y = chart_y + chart_h + 20

    y += 15
    draw.text((width / 2, y + 10), now_text, font=footer_font, fill=label_color,
              direction="rtl", anchor="ma")

    final_height = y + 10 + 60
    img = img.crop((0, 0, width, min(final_height, max_height)))

    output_path = "silver_price.png"
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
    print("عکس نقره با موفقیت ارسال شد.")


def main():
    iran_time = datetime.now(ZoneInfo("Asia/Tehran"))
    now_text = "بروزرسانی: " + iran_time.strftime("%Y/%m/%d - %H:%M") + " به وقت ایران"
    today_str = iran_time.strftime("%Y-%m-%d")

    silver = get_silver_price()
    records = load_history()

    price = silver.get("price") if silver else None
    if price is not None:
        records = update_history(records, today_str, price)

    image_path = build_image(silver, records, now_text)
    save_history(records)
    send_photo(image_path)


if __name__ == "__main__":
    main()
