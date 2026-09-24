"""
اسکریپت ساخت و ارسال گزارش «مهم‌ترین اخبار کلان بازار» به کانال تلگرام (انگلیسی).

از قابلیت browser_search خود Groq استفاده می‌کنه تا مستقیماً وب رو جستجو کنه
و مهم‌ترین رویدادهای بازارساز روز (فدرال رزرو، بانک‌های مرکزی دیگر،
ژئوپلیتیک، نفت، داده‌های اقتصادی کلیدی و...) رو پیدا و خلاصه کنه.

پیام فقط به‌صورت متن (نه عکس) ارسال می‌شه.
"""

import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def ask_groq_for_news():
    prompt = (
        "Search the web for today's most important market-moving news that would matter "
        "to a broad financial audience (for example: central bank decisions or signals "
        "such as the Federal Reserve, ECB, or BOJ; major economic data releases like "
        "inflation or jobs numbers; significant geopolitical developments; notable moves "
        "in oil or energy markets; or major stock market events). "
        "Pick the 3 to 5 most important items you find. "
        "For each item, write one short, neutral sentence explaining what happened and "
        "why it matters to markets. Do not quote sources verbatim; paraphrase in your "
        "own words. Do not give financial advice or predictions.\n\n"
        "Format your answer as a plain bullet list, one bullet per item, each starting "
        "with a relevant short label in bold-like fashion (e.g. 'Fed:', 'Oil:', "
        "'Geopolitics:') followed by a colon and the sentence. Do not add any "
        "introduction or conclusion, just the bullet list."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 700,
        "reasoning_effort": "low",
        "tools": [{"type": "browser_search"}],
        "tool_choice": "required",
    }
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=45)
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
    print("پیام اخبار کلان با موفقیت ارسال شد.")


def main():
    news = ask_groq_for_news()
    if not news:
        print("خبری دریافت نشد.")
        return

    ny_time = datetime.now(ZoneInfo("America/New_York"))
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    lines = ["📰 <b>Key Market News</b>", "", news, "", f"🕐 Updated: {now_text}"]
    message = "\n".join(lines)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
