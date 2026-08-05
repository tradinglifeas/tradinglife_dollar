"""
اسکریپت ارسال قیمت دلار، دلار کانادا، طلا و بیت‌کوین به گروه تلگرام
این نسخه برای اجرا با GitHub Actions طراحی شده: هر بار اجرا می‌شه،
یک بار قیمت‌ها رو می‌گیره و می‌فرسته (زمان‌بندی رو خود گیت‌هاب انجام می‌ده).

توکن‌ها از "GitHub Secrets" خونده می‌شن، نه از داخل کد
(برای امنیت بیشتر، این مقادیر داخل خود فایل نوشته نمی‌شن).
"""

import os
import requests
from datetime import datetime

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
PRICE_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency.php"

# بعضی سایت‌ها درخواست‌های بدون هدر مرورگر (خصوصاً User-Agent پیش‌فرض پایتون) رو مسدود می‌کنن
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def find_item(items, symbol=None, name_contains=None):
    """توی یه لیست از آیتم‌ها دنبال یکی با symbol یا بخشی از name می‌گرده"""
    for item in items:
        item_symbol = str(item.get("symbol", "")).upper()
        item_name = str(item.get("name", ""))
        if symbol and item_symbol == symbol.upper():
            return item
        if name_contains and name_contains in item_name:
            return item
    return None


def get_all_prices():
    params = {"key": BRSAPI_KEY, "section": "gold,currency,cryptocurrency"}
    response = requests.get(PRICE_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)

    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ سایت JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ (۵۰۰ کاراکتر اول): {response.text[:500]}")
        return None

    print(f"داده‌ی دریافتی از API: {data}")

    gold_list = data.get("gold", [])
    currency_list = data.get("currency", [])
    crypto_list = data.get("cryptocurrency", [])

    usd = find_item(currency_list, symbol="USD", name_contains="دلار آمریکا")
    cad = find_item(currency_list, symbol="CAD", name_contains="دلار کانادا")
    gold_18 = find_item(gold_list, name_contains="طلای 18") or (gold_list[0] if gold_list else None)
    bahar_azadi = find_item(gold_list, name_contains="بهار آزادی")
    emami = find_item(gold_list, name_contains="امامی")
    half_coin = find_item(gold_list, name_contains="نیم")
    quarter_coin = find_item(gold_list, name_contains="ربع")
    btc = find_item(crypto_list, symbol="BTC", name_contains="بیت")

    return {
        "usd": usd,
        "cad": cad,
        "gold_18": gold_18,
        "bahar_azadi": bahar_azadi,
        "emami": emami,
        "half_coin": half_coin,
        "quarter_coin": quarter_coin,
        "btc": btc,
    }


def send_to_telegram(text):
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    response = requests.post(TELEGRAM_API_URL, data=payload, timeout=15)
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print(f"پیام با موفقیت ارسال شد - {datetime.now()}")


def format_line(label, item, unit="تومان"):
    if item is None:
        return f"{label}: نامشخص"
    price = item.get("price", "نامشخص")
    return f"{label}: {price} {unit}"


def main():
    prices = get_all_prices()
    if not prices:
        print("قیمت‌ها دریافت نشدن.")
        raise SystemExit(1)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "📊 <b>گزارش نرخ ارز و طلا</b>",
        "",
        format_line("💵 دلار آمریکا", prices["usd"]),
        format_line("🇨🇦 دلار کانادا", prices["cad"]),
        format_line("🥇 طلای ۱۸ عیار", prices["gold_18"]),
        format_line("🪙 سکه بهار آزادی", prices["bahar_azadi"]),
        format_line("🪙 سکه امامی", prices["emami"]),
        format_line("🪙 نیم سکه", prices["half_coin"]),
        format_line("🪙 ربع سکه", prices["quarter_coin"]),
        format_line("₿ بیت‌کوین", prices["btc"], unit="دلار"),
        "",
        f"🕐 {now}",
    ]
    message = "\n".join(lines)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
