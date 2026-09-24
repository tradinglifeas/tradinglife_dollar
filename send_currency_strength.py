"""
اسکریپت ساخت و ارسال گزارش «قدرت ارزهای G10» به کانال تلگرام (انگلیسی).

داده‌ی عددی: از Frankfurter API (رایگان، بدون نیاز به کلید) - نرخ رسمی بانک مرکزی اروپا.
تحلیل خبری: از Groq با قابلیت browser_search - مدل خودش وب رو جستجو می‌کنه.

پیام فقط به‌صورت متن (نه عکس) ارسال می‌شه.
"""

import os
import re
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
FRANKFURTER_URL = "https://api.frankfurter.app"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

def call_groq_with_retry(payload, headers, max_retries=3):
    """
    درخواست رو به Groq می‌فرسته. اگه به خطای rate_limit_exceeded خورد،
    مدت زمانی که خودش پیشنهاد داده (یا ۱۵ ثانیه) صبر می‌کنه و دوباره امتحان می‌کنه.
    """
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


G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def clean_citations(text):
    """علامت‌های استناد داخلی مدل (مثل 【3†L163-L170】) رو از متن نهایی پاک می‌کنه"""
    return re.sub(r"【[^】]*】", "", text).strip()


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
    yesterday = today - timedelta(days=3)  # چند روز عقب‌تر تا مطمئن باشیم روز کاری بوده

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
        percent_change = -((rate_today - rate_yesterday) / rate_yesterday) * 100
        results.append({"currency": currency, "percent": percent_change})

    results.sort(key=lambda x: x["percent"], reverse=True)
    return results


def ask_groq_for_analysis(strength_summary, today_str):
    """
    از قابلیت browser_search خود Groq استفاده می‌کنیم تا مدل مستقیماً
    وب رو جستجو کنه و اخبار تازه‌ی فارکس رو ببینه.
    """
    prompt = (
        f"Today's date is {today_str}. Search the web for the most important "
        f"forex/currency market news from the last 24 to 48 hours (do not use "
        f"older news). If your search results are not clearly from around "
        f"{today_str}, search again with a more specific recent query. "
        "Then, based on what you find, write a short, neutral 2-3 sentence analysis "
        "in English explaining what appears to be driving today's currency market moves. "
        "Do not quote sources verbatim; paraphrase in your own words. "
        "Do not give financial advice.\n\n"
        f"Today's G10 currency strength ranking (vs USD):\n{strength_summary}\n\n"
        "Write only the analysis paragraph, nothing else. Do not include any "
        "citation markers or brackets, only plain text."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 600,
        "reasoning_effort": "low",
        "tools": [{"type": "browser_search"}],
        "tool_choice": "required",
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    data = call_groq_with_retry(payload, headers)
    if data is None:
        return None

    print(f"پاسخ Groq: {data}")
    try:
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        if not content:
            content = (message.get("reasoning") or "").strip()
        return clean_citations(content) if content else None
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

    strength_summary_lines = [f"{r['currency']}: {r['percent']:+.2f}%" for r in strength]
    strength_summary = "\n".join(strength_summary_lines)

    ny_time = datetime.now(ZoneInfo("America/New_York"))
    today_str = ny_time.strftime("%B %d, %Y")
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    analysis = ask_groq_for_analysis(strength_summary, today_str)

    lines = ["💱 <b>G10 Currency Strength (vs USD)</b>", ""]
    for i, r in enumerate(strength, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        lines.append(f"{i}. {emoji} {r['currency']}: {r['percent']:+.2f}%")

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
