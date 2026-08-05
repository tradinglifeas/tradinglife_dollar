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
PRICE_API_URL = "https://brsapi.ir/FreeTsetmcBourseApi/Api_Free_Gold_Currency_v2.json"


def get_dollar_price():
    response = requests.get(PRICE_API_URL, timeout=15)

    # اگه پاسخ JSON نبود (مثلاً یه صفحه خطا برگشته)، کل متن رو چاپ کن
    # تا توی لاگ گیت‌هاب دقیقاً ببینیم مشکل چیه
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ سایت JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ (۵۰۰ کاراکتر اول): {response.text[:500]}")
        return None

    # ساختار این API معمولاً شامل یه لیست currency است
    currency_list = data.get("currency", data if isinstance(data, list) else [])

    for item in currency_list:
        name = str(item.get("name", ""))
        symbol = str(item.get("symbol", "")).upper()
        if "دلار" in name or symbol == "USD":
            return item.get("price")

    print(f"دلار توی داده‌ها پیدا نشد. کل داده: {data}")
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
