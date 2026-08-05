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

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
# این یه کلید عمومی و رایگان است (بدون نیاز به ثبت‌نام یا انقضا) که تا ۱۵۰۰ درخواست در روز مجازه
PRICE_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency_Pro.php"


def get_dollar_price():
    params = {"key": "FreeSV0E1LSgB9RDjuf0QorSLViX8pPG", "section": "currency"}
    response = requests.get(PRICE_API_URL, params=params, timeout=15)

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
