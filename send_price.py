"""
اسکریپت ارسال قیمت دلار به گروه تلگرام
این نسخه برای اجرا با GitHub Actions طراحی شده: هر بار اجرا می‌شه،
یک بار قیمت رو می‌گیره و می‌فرسته (زمان‌بندی رو خود گیت‌هاب انجام می‌ده).

توکن و Chat ID از "GitHub Secrets" خونده می‌شن، نه از داخل کد
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


def get_dollar_price():
    params = {"key": BRSAPI_KEY, "section": "currency"}
    response = requests.get(PRICE_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)

    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ سایت JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ (۵۰۰ کاراکتر اول): {response.text[:500]}")
        return None

    print(f"داده‌ی دریافتی از API: {data}")

    currency_list = data.get("currency", data if isinstance(data, list) else [])

    for item in currency_list:
        symbol = str(item.get("symbol", "")).upper()
        name = str(item.get("name", ""))
        if symbol == "USD" or "دلار" in name:
            return item.get("price")

    print("دلار توی داده‌ها پیدا نشد.")
    return None


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


def main():
    price = get_dollar_price()
    if price:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        message = f"💵 <b>قیمت دلار</b>\n\n{price} تومان\n\n🕐 {now}"
        send_to_telegram(message)
    else:
        print("قیمت دریافت نشد.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
