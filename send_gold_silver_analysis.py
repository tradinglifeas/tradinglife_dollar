"""
اسکریپت ساخت و ارسال گزارش تحلیلی «طلا و نقره» به کانال تلگرام (انگلیسی، بازار جهانی).

داده‌ی عددی: از BrsApi (طلا از Gold_Currency.php، نقره از Commodity.php).
تحلیل خبری: از Groq (رایگان) - خلاصه‌ای از اخبار طلا/نقره‌ی روز.

پیام فقط به‌صورت متن (نه عکس) ارسال می‌شه.
"""

import os
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
BRSAPI_KEY = os.environ["BRSAPI_KEY"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
GOLD_API_URL = "https://Api.BrsApi.ir/Market/Gold_Currency.php"
COMMODITY_API_URL = "https://Api.BrsApi.ir/Market/Commodity.php"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
NEWS_RSS_URL = "https://news.google.com/rss/search?q=gold%20silver%20market&hl=en-US&gl=US&ceid=US:en"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def find_item(items, symbol=None, name_contains=None):
    for item in items:
        item_symbol = str(item.get("symbol", "")).upper()
        item_name = str(item.get("name", ""))
        if symbol and item_symbol == symbol.upper():
            return item
        if name_contains and name_contains in item_name:
            return item
    return None


def get_gold_price():
    params = {"key": BRSAPI_KEY, "section": "gold"}
    response = requests.get(GOLD_API_URL, params=params, headers=REQUEST_HEADERS, timeout=15)
    try:
        data = response.json()
    except ValueError:
        print(f"پاسخ طلا JSON نبود. کد وضعیت: {response.status_code}")
        print(f"متن پاسخ: {response.text[:500]}")
        return None
    print(f"داده‌ی طلا: {data}")
    gold_list = data.get("gold", [])
    return find_item(gold_list, symbol="XAUUSD") or find_item(gold_list, name_contains="Gold Ounce")


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
    return find_item(all_items, symbol="XAGUSD") or find_item(all_items, symbol="XAG", name_contains="Silver")


def format_number(value):
    try:
        return f"{float(value):,.2f}"
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
    print(f"عنوان‌های خبری طلا/نقره: {headlines}")
    return headlines


def ask_groq_for_analysis(headlines, gold_price, silver_price):
    if not headlines:
        return None

    headlines_text = "\n".join(f"- {h}" for h in headlines)
    prompt = (
        "You are a financial news summarizer. Based ONLY on the following news headlines "
        "(do not invent facts not implied by them), write a short, neutral 2-3 sentence "
        "analysis in English explaining what appears to be driving today's gold and silver "
        "market moves. Do not quote headlines verbatim; paraphrase in your own words. "
        "Do not give financial advice.\n\n"
        f"Gold: ${gold_price}/oz | Silver: ${silver_price}/oz\n\n"
        f"Recent gold/silver news headlines:\n{headlines_text}\n\n"
        "Write only the analysis paragraph, nothing else."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 600,
        "reasoning_effort": "low",
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
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if content:
            return content
        reasoning = (message.get("reasoning") or "").strip()
        return reasoning if reasoning else None
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
    print("پیام طلا/نقره با موفقیت ارسال شد.")


def main():
    gold = get_gold_price()
    silver = get_silver_price()

    gold_price = gold.get("price") if gold else None
    silver_price = silver.get("price") if silver else None

    headlines = get_news_headlines()
    analysis = ask_groq_for_analysis(headlines, gold_price, silver_price)

    ny_time = datetime.now(ZoneInfo("America/New_York"))
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    gold_diff, gold_percent = get_change(gold)
    silver_diff, silver_percent = get_change(silver)

    lines = ["🥇 <b>Gold & Silver Market Update</b>", ""]

    if gold_price is not None:
        gold_line = f"Gold: ${format_number(gold_price)}/oz"
        if gold_percent is not None:
            gold_line += f" ({gold_percent:+.2f}%)"
        lines.append(gold_line)
    else:
        lines.append("Gold: N/A")

    if silver_price is not None:
        silver_line = f"Silver: ${format_number(silver_price)}/oz"
        if silver_percent is not None:
            silver_line += f" ({silver_percent:+.2f}%)"
        lines.append(silver_line)
    else:
        lines.append("Silver: N/A")

    if analysis:
        lines.append("")
        lines.append("📰 <b>Analysis:</b>")
        lines.append(analysis)

    lines.append("")
    lines.append(f"🕐 Updated: {now_text}")

    message = "\n".join(lines)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
