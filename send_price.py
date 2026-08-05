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
ALANCHAND_TOKEN = os.environ["ALANCHAND_TOKEN"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
PRICE_API_URL = "https://api.alanchand.com/"


def get_dollar_price():
    params = {"type": "currencies", "token": ALANCHAND_TOKEN}
    response = requests.get(PRICE_API_URL, params=params, timeout=15)

    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ سایت JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ (۵۰۰ کاراکتر اول): {response.text[:500]}")
        return None

    # چاپ کامل داده برای دیباگ (اگه دلار پیدا نشد، این کمک می‌کنه بفهمیم ساختار دقیق چیه)
    print(f"داده‌ی دریافتی از API: {data}")

    # حالت ۱: دیکشنری با کلید usd
    if isinstance(data, dict):
        if "usd" in data:
            item = data["usd"]
            if isinstance(item, dict):
                return item.get("value") or item.get("price") or item.get("sell")
            return item
        # حالت ۲: دیکشنری با یه لیست داخلش (مثلاً data["currencies"])
        for value in data.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and str(item.get("symbol", "")).lower() == "usd":
                        return item.get("value") or item.get("price") or item.get("sell")

    # حالت ۳: خود پاسخ یه لیست باشه
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and str(item.get("symbol", "")).lower() == "usd":
                return item.get("value") or item.get("price") or item.get("sell")

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
