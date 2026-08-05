"""
اسکریپت ارسال آخرین خبر اقتصادی (طلا و ارز) به گروه تلگرام
جدا از اسکریپت قیمت‌هاست و مستقل اجرا می‌شه.

فقط عنوان خبر و لینک اصلی رو می‌فرسته (نه متن کامل مقاله، به‌خاطر کپی‌رایت).
برای جلوگیری از تکرار یه خبر، آخرین خبر ارسال‌شده رو توی یه فایل ذخیره می‌کنه
و فقط وقتی خبر تازه‌ای اومده باشه پیام می‌فرسته.
"""

import os
import json
import requests
import xml.etree.ElementTree as ET

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
RSS_URL = "https://www.eghtesadonline.com/fa/rss/13/mostcommented"
HISTORY_FILE = "news_history.json"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def load_last_sent_link():
    if not os.path.exists(HISTORY_FILE):
        return None
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("last_link")
    except (ValueError, OSError):
        return None


def save_last_sent_link(link):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_link": link}, f, ensure_ascii=False, indent=2)


def get_latest_news():
    response = requests.get(RSS_URL, headers=REQUEST_HEADERS, timeout=15)
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        print(f"پاسخ RSS قابل خوندن نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ (۵۰۰ کاراکتر اول): {response.text[:500]}")
        return None

    item = root.find(".//item")
    if item is None:
        print("هیچ خبری توی فید RSS پیدا نشد.")
        return None

    title = item.findtext("title", default="").strip()
    link = item.findtext("link", default="").strip()

    if not title or not link:
        print(f"عنوان یا لینک خالی بود. آیتم: {ET.tostring(item, encoding='unicode')}")
        return None

    return {"title": title, "link": link}


def send_to_telegram(text):
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    response = requests.post(TELEGRAM_API_URL, data=payload, timeout=15)
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print("پیام خبر با موفقیت ارسال شد.")


def main():
    news = get_latest_news()
    if not news:
        print("خبری دریافت نشد.")
        return

    last_link = load_last_sent_link()
    if news["link"] == last_link:
        print("خبر جدیدی نیومده (همون خبر قبلیه)، پیامی ارسال نمی‌شه.")
        return

    message = (
        "📰 <b>آخرین خبر طلا و ارز</b>\n\n"
        f"{news['title']}\n\n"
        f"🔗 {news['link']}"
    )
    send_to_telegram(message)
    save_last_sent_link(news["link"])


if __name__ == "__main__":
    main()
