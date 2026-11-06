"""
اسکریپت ارسال گزارش اشتغال آمریکا (Jobs Report / NFP) به کانال تلگرام، به فارسی.

فقط در «اولین جمعه‌ی هر ماه» (روز انتشار رسمی گزارش اشتغال آمریکا، ساعت ۸:۳۰
صبح به وقت شرقی) واقعاً پیام می‌فرسته؛ بقیه‌ی روزها که این اسکریپت اجرا بشه
(چون هر جمعه صدا زده می‌شه)، بدون ارسال پیام خارج می‌شه.

داده‌ی «واقعی» (NFP، نرخ بیکاری، رشد سالانه‌ی دستمزد، نرخ مشارکت): از API رسمی
FRED (api.stlouisfed.org) که نیاز به یه کلید رایگان داره (سکرت FRED_API_KEY).
داده‌ی «انتظار بازار»، بازنگری دو ماه قبل، و تحلیل اثرات احتمالی روی هر بازار:
از Groq با قابلیت browser_search.
"""

import os
import re
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# برای تست دستی (اجرای اجباری حتی اگر امروز جمعه‌ی اول ماه نباشه)
FORCE_RUN = os.getenv("FORCE_RUN", "false").lower() == "true"

TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"

FONT_REGULAR = "Vazirmatn-Regular.ttf"
FONT_BOLD = "Vazirmatn-Bold.ttf"

BG = (12, 16, 24)
CARD = (22, 27, 38)
CARD2 = (18, 22, 31)
TEXT = (240, 244, 248)
MUTED = (160, 170, 185)
GOLD = (255, 205, 86)
GREEN = (80, 200, 120)
RED = (230, 90, 90)
AMBER = (245, 185, 66)
BLUE = (91, 155, 255)

MONTHS_FA = {
    1: "ژانویه", 2: "فوریه", 3: "مارس", 4: "آوریل", 5: "می", 6: "ژوئن",
    7: "ژوئیه", 8: "اوت", 9: "سپتامبر", 10: "اکتبر", 11: "نوامبر", 12: "دسامبر",
}


def load_font(path, size):
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)


def is_first_friday(dt):
    """چک می‌کنه آیا امروز واقعاً اولین جمعه‌ی ماه (روز انتشار رسمی NFP) هست یا نه"""
    return dt.weekday() == 4 and dt.day <= 7


def fred_series(series_id, limit=20, max_retries=4):
    """
    از API رسمی FRED (api.stlouisfed.org) آخرین {limit} مشاهده‌ی یک سری رو
    می‌گیره، از قدیم به جدید مرتب می‌کنه (مثل قبل)، تا تمام محاسبات دیگه
    (pct, تغییر ماه به ماه و...) بدون تغییر کار کنن.
    """
    if not FRED_API_KEY:
        raise RuntimeError(
            "سکرت FRED_API_KEY تنظیم نشده. اول باید یک کلید رایگان از "
            "fredaccount.stlouisfed.org/apikeys بگیری و به‌عنوان سکرت "
            "FRED_API_KEY توی گیت‌هاب اضافه کنی."
        )
    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(
                FRED_API_URL,
                params={
                    "series_id": series_id,
                    "api_key": FRED_API_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": limit,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            if "error_message" in payload:
                raise RuntimeError(f"خطای API: {payload['error_message']}")
            rows = []
            for obs in payload.get("observations", []):
                value = obs.get("value")
                if value in (".", "", None):
                    continue
                try:
                    rows.append((obs["date"], float(value)))
                except (ValueError, KeyError):
                    pass
            if not rows:
                raise RuntimeError(f"FRED پاسخ خالی برای {series_id} برگردوند.")
            rows.reverse()  # از قدیم به جدید
            return rows
        except (requests.exceptions.RequestException, RuntimeError, ValueError) as e:
            last_error = e
            wait_seconds = 5 * (attempt + 1)
            print(f"خطا در گرفتن {series_id} از FRED (تلاش {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                print(f"{wait_seconds} ثانیه صبر می‌کنیم و دوباره تلاش می‌کنیم...")
                time.sleep(wait_seconds)
    raise RuntimeError(f"گرفتن {series_id} از FRED بعد از {max_retries} بار تلاش شکست خورد: {last_error}")


def pct(a, b):
    return ((a / b) - 1) * 100 if b not in (None, 0) else None


def get_actual_data():
    """داده‌ی واقعی گزارش اشتغال رو از FRED می‌گیره"""
    data = {}

    payems = fred_series("PAYEMS")
    data["nfp_change_k"] = round(payems[-1][1] - payems[-2][1])
    data["nfp_date"] = payems[-1][0]
    report_date = datetime.strptime(payems[-1][0], "%Y-%m-%d")
    data["report_month_fa"] = MONTHS_FA.get(report_date.month, "")
    data["report_year"] = report_date.year

    unrate = fred_series("UNRATE")
    data["unemployment_rate"] = unrate[-1][1]
    data["unemployment_rate_prev"] = unrate[-2][1]

    earnings = fred_series("CES0500000003")
    data["earnings_yoy_pct"] = pct(earnings[-1][1], earnings[-13][1]) if len(earnings) >= 13 else None
    data["earnings_yoy_prev_pct"] = pct(earnings[-2][1], earnings[-14][1]) if len(earnings) >= 14 else None

    participation = fred_series("CIVPART")
    data["participation_rate"] = participation[-1][1]
    data["participation_rate_prev"] = participation[-2][1]

    print(f"داده‌ی واقعی از FRED: {data}")
    return data


def call_groq_with_retry(payload, headers, max_retries=3):
    for attempt in range(max_retries):
        response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=45)
        try:
            data = response.json()
        except ValueError:
            print(f"پاسخ Groq JSON نبود. کد وضعیت: {response.status_code}")
            print(f"متن پاسخ: {response.text[:500]}")
            return None

        error = data.get("error") if isinstance(data, dict) else None
        if error and error.get("code") == "rate_limit_exceeded":
            match = re.search(r"try again in ([\d.]+)s", error.get("message", ""))
            wait_seconds = float(match.group(1)) + 2 if match else 15
            print(f"محدودیت نرخ Groq - {wait_seconds:.1f} ثانیه صبر می‌کنیم (تلاش {attempt + 1}/{max_retries})")
            time.sleep(wait_seconds)
            continue

        return data

    print("بعد از چند بار تلاش، بازم محدودیت نرخ برطرف نشد.")
    return None


def clean_citations(text):
    return re.sub(r"【[^】]*】", "", text).strip()


# نام فیلدهایی که از Groq می‌خوایم، به ترتیب همون‌طوری که توی پرامپت خواسته می‌شه
FIELD_NAMES = [
    "NFP_FORECAST",
    "UNEMPLOYMENT_FORECAST",
    "REVISION",
    "IMPACT_USD",
    "IMPACT_YIELDS",
    "IMPACT_GOLD",
    "IMPACT_STOCKS",
    "IMPACT_FED",
    "SUMMARY",
]


def get_market_data(actual, today_str):
    """
    از Groq با browser_search می‌خوایم: عدد مورد انتظار بازار (Consensus)،
    میزان بازنگری (revision) دو ماه قبل، و تحلیل اثرات احتمالی روی هر بازار
    (دلار، بازده اوراق، طلا، سهام، فدرال‌رزرو) رو پیدا کنه.
    """
    prompt = (
        f"Today, {today_str}, the US Bureau of Labor Statistics released the "
        f"monthly Employment Situation report (Jobs Report / NFP). The actual "
        f"reported figures are: nonfarm payrolls change of {actual['nfp_change_k']:,}K, "
        f"unemployment rate {actual['unemployment_rate']:.1f}%.\n\n"
        "Search the web for:\n"
        "1. The market consensus/expected forecast for TODAY's report specifically "
        "(nonfarm payrolls forecast and unemployment rate forecast).\n"
        "2. The combined net revision to the prior two months' payrolls figures, "
        "as reported in today's release (e.g. payrolls for the prior two months "
        "were revised up or down by a combined total of some number of thousands).\n"
        "3. Brief analyst commentary on the likely market impact on: the US Dollar, "
        "Treasury bond yields, gold, stocks, and what this means for Federal Reserve "
        "interest rate policy.\n\n"
        "Respond in exactly this format, nothing else:\n"
        "NFP_FORECAST: <number in thousands, just the integer, no commas or K>\n"
        "UNEMPLOYMENT_FORECAST: <percentage, just the number>\n"
        "REVISION: <combined net revision to the prior two months, in thousands, "
        "as a signed integer like +84 or -20; write N/A if not reported or not found>\n"
        "IMPACT_USD: <very short Persian (Farsi) phrase, start with one word: "
        "صعودی / نزولی / خنثی, then a colon, then a short reason clause>\n"
        "IMPACT_YIELDS: <same short Persian format as above, for Treasury bond yields>\n"
        "IMPACT_GOLD: <same short Persian format as above, for gold>\n"
        "IMPACT_STOCKS: <same short Persian format as above, for stocks>\n"
        "IMPACT_FED: <same short Persian format as above, for Federal Reserve rate policy>\n"
        "SUMMARY: <one short Persian sentence, neutral tone, summarizing the overall "
        "market takeaway, no financial advice>\n\n"
        "Every field except NFP_FORECAST, UNEMPLOYMENT_FORECAST and REVISION must be "
        "written in Persian (Farsi), regardless of the language of the source articles. "
        "If you cannot find a specific number, write N/A for that field instead of guessing. "
        "Do not include citation markers."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 900,
        "reasoning_effort": "low",
        "tools": [{"type": "browser_search"}],
        "tool_choice": "required",
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    result = {name: None for name in FIELD_NAMES}

    data = call_groq_with_retry(payload, headers)
    if data is None:
        return result

    print(f"پاسخ Groq: {data}")
    try:
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("reasoning") or "").strip()
        content = clean_citations(content)
    except (KeyError, IndexError):
        return result

    for line in content.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().upper()
        value = value.strip()
        if key in result and value and value.upper() != "N/A":
            result[key] = value

    for key in ("NFP_FORECAST", "UNEMPLOYMENT_FORECAST", "REVISION"):
        raw = result.get(key)
        if raw is None:
            continue
        try:
            result[key] = float(re.sub(r"[^\d.\-+]", "", raw))
        except ValueError:
            result[key] = None

    return result


def add_glow(width, height):
    glow_layer = Image.new("RGB", (width, height), BG)
    glow_draw = ImageDraw.Draw(glow_layer)
    blobs = [
        (width * 0.15, height * 0.04, 260, (91, 155, 255)),
        (width * 0.90, height * 0.12, 220, (255, 205, 86)),
        (width * 0.10, height * 0.55, 200, (91, 155, 255)),
    ]
    for cx, cy, r, color in blobs:
        glow_draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(120))
    base = Image.new("RGB", (width, height), BG)
    return Image.blend(base, glow_layer, alpha=0.16)


def format_signed(value, unit="", decimals=1):
    if value is None:
        return "نامشخص"
    sign = "+" if value > 0 else ("" if value < 0 else "")
    return f"{sign}{value:.{decimals}f}{unit}"


def classify_headline(actual_k, forecast_k):
    if forecast_k is None:
        return "گزارش تازه‌ی اشتغال آمریکا منتشر شد", TEXT
    diff = actual_k - forecast_k
    if diff >= 30:
        return "سورپرایز مثبت؛ بازار کار قوی‌تر از انتظار", GREEN
    if diff <= -30:
        return "سورپرایز منفی؛ بازار کار ضعیف‌تر از انتظار", RED
    return "مطابق انتظار بازار؛ تغییر محسوسی در روند دیده نشد", AMBER


def direction_color(text):
    if not text:
        return MUTED
    head = text.split(":", 1)[0]
    if any(w in head for w in ("صعودی", "افزایش", "مثبت")):
        return GREEN
    if any(w in head for w in ("نزولی", "کاهش", "منفی", "فشار")):
        return RED
    return AMBER


def wrap_text_rtl(draw, text, font, max_width):
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        test_line = f"{current_line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font, direction="rtl")
        if bbox[2] - bbox[0] > max_width and current_line:
            lines.append(current_line)
            current_line = word
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines


def build_image(actual, market, now_text):
    width, max_height = 950, 2200
    img = add_glow(width, max_height)
    draw = ImageDraw.Draw(img)

    title_font = load_font(FONT_BOLD, 40)
    subtitle_font = load_font(FONT_BOLD, 24)
    headline_font = load_font(FONT_BOLD, 26)
    big_font = load_font(FONT_BOLD, 60)
    label_font = load_font(FONT_REGULAR, 23)
    value_font = load_font(FONT_BOLD, 28)
    small_font = load_font(FONT_REGULAR, 19)
    section_font = load_font(FONT_BOLD, 25)
    row_label_font = load_font(FONT_BOLD, 21)
    row_text_font = load_font(FONT_REGULAR, 20)
    summary_font = load_font(FONT_BOLD, 22)
    disclaimer_font = load_font(FONT_REGULAR, 17)

    nfp_forecast = market.get("NFP_FORECAST")
    unemployment_forecast = market.get("UNEMPLOYMENT_FORECAST")
    revision_k = market.get("REVISION")

    margin = 50
    y = 42

    # عنوان + ماه/سال گزارش
    draw.text((width / 2, y), "گزارش اشتغال آمریکا", font=title_font, fill=TEXT,
              direction="rtl", anchor="ma")
    y += 52
    month_year = f"{actual.get('report_month_fa', '')} {actual.get('report_year', '')}".strip()
    subtitle_text = f"{month_year} | تغییر اشتغال غیرکشاورزی (NFP)" if month_year else "تغییر اشتغال غیرکشاورزی (NFP)"
    draw.text((width / 2, y), subtitle_text, font=subtitle_font, fill=GOLD,
              direction="rtl", anchor="ma")
    y += 48

    # بنر تیتر (سورپرایز مثبت/منفی/مطابق انتظار)
    headline_text, headline_color = classify_headline(actual["nfp_change_k"], nfp_forecast)
    banner_h = 64
    draw.rounded_rectangle([margin, y, width - margin, y + banner_h], radius=16,
                            fill=CARD, outline=headline_color, width=2)
    draw.text((width / 2, y + banner_h / 2), headline_text, font=headline_font, fill=headline_color,
              direction="rtl", anchor="mm")
    y += banner_h + 22

    # کارت اصلی: واقعی در برابر انتظار بازار
    card_h = 210
    draw.rounded_rectangle([margin, y, width - margin, y + card_h], radius=20, fill=CARD)

    nfp_color = GREEN if (nfp_forecast is not None and actual["nfp_change_k"] > nfp_forecast) else (
        RED if nfp_forecast is not None else TEXT)

    draw.text((width - margin - 35, y + 30), "واقعی", font=label_font, fill=MUTED,
              direction="rtl", anchor="ra")
    draw.text((width - margin - 35, y + 60), f"{actual['nfp_change_k']:,}K", font=big_font, fill=nfp_color,
              direction="ltr", anchor="ra")

    forecast_text = f"{int(nfp_forecast):,}K" if nfp_forecast is not None else "نامشخص"
    draw.text((margin + 35, y + 30), "انتظار بازار", font=label_font, fill=MUTED,
              direction="rtl", anchor="la")
    draw.text((margin + 35, y + 60), forecast_text, font=big_font, fill=MUTED,
              direction="ltr", anchor="la")

    y += card_h + 22

    # کارت بازنگری دو ماه قبل
    rev_h = 86
    draw.rounded_rectangle([margin, y, width - margin, y + rev_h], radius=16, fill=CARD2)
    draw.text((width - margin - 25, y + 18), "بازنگری اشتغال دو ماه گذشته", font=label_font, fill=MUTED,
              direction="rtl", anchor="ra")
    if revision_k is not None:
        rev_color = GREEN if revision_k >= 0 else RED
        rev_text = f"{format_signed(revision_k, '', 0)} هزار شغل نسبت به برآورد قبلی"
    else:
        rev_color = MUTED
        rev_text = "در این اجرا در دسترس نبود"
    draw.text((width - margin - 25, y + 50), rev_text, font=value_font, fill=rev_color,
              direction="rtl", anchor="ra")
    y += rev_h + 22

    # کارت‌های آماری (نرخ بیکاری، رشد سالانه‌ی دستمزد، نرخ مشارکت)
    unemployment_subs = []
    if unemployment_forecast is not None:
        unemployment_subs.append(f"انتظار: {unemployment_forecast:.1f}%")
    unemployment_subs.append(f"قبلی: {actual['unemployment_rate_prev']:.1f}%")

    earnings_subs = []
    if actual.get("earnings_yoy_prev_pct") is not None:
        earnings_subs.append(f"قبلی: {format_signed(actual['earnings_yoy_prev_pct'], '%', 1)}")

    participation_subs = [f"قبلی: {actual['participation_rate_prev']:.1f}%"]

    stat_cards = [
        ("نرخ بیکاری", f"{actual['unemployment_rate']:.1f}%", unemployment_subs),
        ("رشد سالانه‌ی دستمزد ساعتی", format_signed(actual.get("earnings_yoy_pct"), "%", 1), earnings_subs),
        ("نرخ مشارکت نیروی کار", f"{actual['participation_rate']:.1f}%", participation_subs),
    ]

    card_w = (width - margin * 2 - 20 * 2) / 3
    card_h2 = 180
    x = margin
    for label, value, subs in stat_cards:
        draw.rounded_rectangle([x, y, x + card_w, y + card_h2], radius=16, fill=CARD)
        draw.text((x + card_w / 2, y + 22), label, font=small_font, fill=MUTED,
                  direction="rtl", anchor="ma")
        draw.text((x + card_w / 2, y + 60), value, font=value_font, fill=TEXT,
                  direction="ltr", anchor="ma")
        sub_y = y + 112
        for sub in subs:
            draw.text((x + card_w / 2, sub_y), sub, font=small_font, fill=MUTED,
                      direction="rtl", anchor="ma")
            sub_y += 28
        x += card_w + 20

    y += card_h2 + 32

    # پیامدهای احتمالی برای بازارها
    impact_rows = [
        ("دلار آمریکا", market.get("IMPACT_USD")),
        ("بازده اوراق خزانه", market.get("IMPACT_YIELDS")),
        ("طلا", market.get("IMPACT_GOLD")),
        ("سهام", market.get("IMPACT_STOCKS")),
        ("فدرال رزرو", market.get("IMPACT_FED")),
    ]
    impact_rows = [(label, text) for label, text in impact_rows if text]

    if impact_rows:
        draw.text((width - margin, y), "پیامدهای احتمالی برای بازارها", font=section_font, fill=BLUE,
                  direction="rtl", anchor="ra")
        y += 42

        row_max_width = width - margin * 2 - 40
        wrapped_rows = []
        for label, text in impact_rows:
            detail = text.split(":", 1)[1].strip() if ":" in text else text
            color = direction_color(text)
            lines = wrap_text_rtl(draw, detail, row_text_font, row_max_width)
            wrapped_rows.append((label, color, lines))

        total_rows_h = sum(34 + len(lines) * 26 for _, _, lines in wrapped_rows) + 20
        draw.rounded_rectangle([margin, y, width - margin, y + total_rows_h], radius=16, fill=CARD)
        ry = y + 18
        for label, color, lines in wrapped_rows:
            draw.text((width - margin - 20, ry), f"● {label}", font=row_label_font, fill=color,
                      direction="rtl", anchor="ra")
            ry += 30
            for line in lines:
                draw.text((width - margin - 20, ry), line, font=row_text_font, fill=TEXT,
                          direction="rtl", anchor="ra")
                ry += 26
            ry += 8
        y += total_rows_h + 22

    # بنر جمع‌بندی
    summary_text = market.get("SUMMARY")
    if summary_text:
        summary_lines = wrap_text_rtl(draw, summary_text, summary_font, width - margin * 2 - 50)
        summary_h = 30 + len(summary_lines) * 32
        draw.rounded_rectangle([margin, y, width - margin, y + summary_h], radius=16,
                                fill=CARD, outline=GOLD, width=2)
        sy = y + 15
        for line in summary_lines:
            draw.text((width / 2, sy), line, font=summary_font, fill=GOLD,
                      direction="rtl", anchor="ma")
            sy += 32
        y += summary_h + 18

    # سلب مسئولیت
    draw.text((width / 2, y), "جهت‌گیری محتمل بازار، نه تضمین واقعی قیمت", font=disclaimer_font, fill=MUTED,
              direction="rtl", anchor="ma")
    y += 34

    draw.text((width / 2, y), now_text, font=small_font, fill=MUTED,
              direction="rtl", anchor="ma")
    y += 28
    draw.text((width / 2, y), "منبع: U.S. Bureau of Labor Statistics (FRED)", font=small_font, fill=MUTED,
              direction="ltr", anchor="ma")

    final_height = y + 55
    img = img.crop((0, 0, width, min(final_height, max_height)))

    output_path = "jobs_report.png"
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
    print("گزارش اشتغال با موفقیت ارسال شد.")


def main():
    ny_time = datetime.now(ZoneInfo("America/New_York"))

    if not FORCE_RUN and not is_first_friday(ny_time):
        print(f"امروز ({ny_time.strftime('%Y-%m-%d')}) اولین جمعه‌ی ماه نیست، گزارش اشتغال منتشر نمی‌شه.")
        return

    actual = get_actual_data()
    today_str = ny_time.strftime("%B %d, %Y")
    market = get_market_data(actual, today_str)

    now_text = "بروزرسانی: " + ny_time.strftime("%Y/%m/%d - %H:%M") + " به وقت آمریکا"

    image_path = build_image(actual, market, now_text)
    send_photo(image_path)


if __name__ == "__main__":
    main()
