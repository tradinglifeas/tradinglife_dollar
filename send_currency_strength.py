"""
اسکریپت ساخت و ارسال گزارش «قدرت ارزهای G10» به کانال تلگرام (انگلیسی).

داده‌ی عددی: از Frankfurter API (رایگان، بدون نیاز به کلید) - نرخ رسمی بانک مرکزی اروپا.
تحلیل خبری: از Groq (رایگان) - خلاصه‌ای از اخبار فارکس روز.

پیام فقط به‌صورت متن (نه عکس) ارسال می‌شه.
"""

import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
FRANKFURTER_URL = "https://api.frankfurter.app"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NEWS_RSS_URL = "https://news.google.com/rss/search?q=forex%20market%20currencies&hl=en-US&gl=US&ceid=US:en"

G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def get_rates(date_str):
    """نرخ ارزهای G10 نسبت به دلار رو در یه تاریخ مشخص می‌گیره"""
    url = f"{FRANKFURTER_URL}/{date_str}"
    params = {"from": "USD", "to": ",".join(G10_CURRENCIES)}
    response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=15)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ Frankfurter JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return None
    print(f"داده‌ی نرخ {date_str}: {data}")
    return data.get("rates", {})


def compute_strength():
    """درصد تغییر هر ارز نسبت به دلار (نسبت به دیروز) رو حساب می‌کنه"""
    today = datetime.now(ZoneInfo("America/New_York")).date()
    yesterday = today - timedelta(days=3)  # چند روز عقب‌تر تا مطمئن باشیم روز کاری بوده (آخر هفته و تعطیلات)

    today_rates = get_rates(today.strftime("%Y-%m-%d"))
    yesterday_rates = get_rates(yesterday.strftime("%Y-%m-%d"))

    if not today_rates or not yesterday_rates:
        return []

    results = []
    for currency in G10_CURRENCIES:
        rate_today = today_rates.get(currency)
        rate_yesterday = yesterday_rates.get(currency)
        if rate_today is None or rate_yesterday is None:
            continue
        # rate = مقدار این ارز به‌ازای ۱ دلار. اگه این عدد کم شده، یعنی ارز قوی‌تر شده (دلار ضعیف‌تر شده نسبت بهش)
        percent_change = -((rate_today - rate_yesterday) / rate_yesterday) * 100
        results.append({"currency": currency, "percent": percent_change})

    results.sort(key=lambda x: x["percent"], reverse=True)
    return results


def get_news_headlines(limit=6):
    response = requests.get(NEWS_RSS_URL, headers=REQUEST_HEADERS, timeout=15)
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        print(f"پاسخ RSS قابل خوندن نبود. کد وضعیت: {response.status_code}")
        return []

    headlines = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title", default="").strip()
        if title:
            headlines.append(title)
    print(f"عنوان‌های خبری فارکس: {headlines}")
    return headlines


def ask_groq_for_analysis(headlines, strength_summary):
    if not headlines:
        return None

    headlines_text = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        "You are a financial news summarizer. Based ONLY on the following news headlines "
        "(do not invent facts not implied by them), write a short, neutral 2-3 sentence "
        "analysis in English explaining what appears to be driving today's currency market "
        "moves. Do not quote headlines verbatim; paraphrase in your own words. "
        "Do not give financial advice.\n\n"
        f"Today's G10 currency strength ranking (vs USD):\n{strength_summary}\n\n"
        f"Recent forex news headlines:\n{headlines_text}\n\n"
        "Write only the analysis paragraph, nothing else."
    )

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 220,
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=30)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ Groq JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return None

    print(f"پاسخ Groq: {data}")
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return None


def send_to_telegram(text):
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    response = requests.post(TELEGRAM_API_URL, data=payload, timeout=15)
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print("پیام قدرت ارزها با موفقیت ارسال شد.")


def main():
    strength = compute_strength()
    if not strength:
        print("داده‌ی قدرت ارزها دریافت نشد.")
        return

    strongest = strength[:3]
    weakest = strength[-3:][::-1]

    strength_summary_lines = [f"{r['currency']}: {r['percent']:+.2f}%" for r in strength]
    strength_summary = "\n".join(strength_summary_lines)

    headlines = get_news_headlines()
    analysis = ask_groq_for_analysis(headlines, strength_summary)

    ny_time = datetime.now(ZoneInfo("America/New_York"))
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    lines = ["💱 <b>G10 Currency Strength</b>", ""]
    lines.append("🟢 <b>Strongest:</b>")
    for i, r in enumerate(strongest, 1):
        lines.append(f"{i}. {r['currency']}: {r['percent']:+.2f}%")
    lines.append("")
    lines.append("🔴 <b>Weakest:</b>")
    for i, r in enumerate(weakest, 1):
        lines.append(f"{i}. {r['currency']}: {r['percent']:+.2f}%")

    if analysis:
        lines.append("")
        lines.append("📰 <b>What's driving it:</b>")
        lines.append(analysis)

    lines.append("")
    lines.append(f"🕐 Updated: {now_text}")

    message = "\n".join(lines)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
