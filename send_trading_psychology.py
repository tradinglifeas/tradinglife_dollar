"""
اسکریپت ارسال یه نکته‌ی کوتاه روانشناسی معامله‌گری، هر روز نزدیک به
شروع بازار بورس آمریکا.

برخلاف بقیه‌ی اسکریپت‌ها، این یکی به هیچ فید خبری وابسته نیست - چون
موضوع «همیشگی» و آموزشیه، نه وابسته به اتفاق امروز بازار. یه لیست از
موضوعات رایج روانشناسی معامله‌گری داریم و هر روز (بر اساس روز سال، تا
تکراری نشه) یکیشون رو انتخاب و ازGroq می‌خوایم دربارش بنویسه.
"""

import os
import requests
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

TOPICS = [
    "Loss aversion",
    "FOMO (fear of missing out)",
    "Revenge trading",
    "Overconfidence after a winning streak",
    "The importance of a trading plan",
    "Confirmation bias",
    "Position sizing and risk management mindset",
    "Patience and waiting for setups",
    "Anchoring bias on entry price",
    "The sunk cost fallacy in open positions",
    "Overtrading and boredom",
    "Journaling trades for self-awareness",
    "Managing emotions after a big loss",
    "The danger of averaging down without a plan",
    "Recency bias in market outlook",
    "Discipline vs. impulsiveness",
    "Accepting uncertainty in the markets",
    "The role of routine in consistent performance",
    "Herd mentality and crowd psychology",
    "Dealing with drawdowns mentally",
    "The illusion of control in trading",
    "Setting realistic expectations",
    "Detaching self-worth from P&L",
    "The value of taking breaks after losses",
    "Cognitive fatigue and decision quality",
    "Survivorship bias from social media trading stories",
    "The role of humility in long-term success",
    "Analysis paralysis",
    "Letting winners run vs. fear of giving back gains",
    "Building emotional resilience over time",
]


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


def clean_citations(text):
    return re.sub(r"【[^】]*】", "", text).strip()


def pick_topic(day_of_year):
    return TOPICS[day_of_year % len(TOPICS)]


def ask_groq_for_tip(topic):
    prompt = (
        f"Write a short trading psychology tip for a financial Telegram channel, "
        f"on the topic of: \"{topic}\". "
        "Structure it as: one line naming the concept, then a short paragraph "
        "(3-4 sentences) explaining what it is, why it affects traders, and one "
        "practical way to manage it. Keep the tone calm, educational, and neutral. "
        "Do not give specific financial advice or trade recommendations. "
        "Write only the tip itself, nothing else. Do not include any citation "
        "markers or brackets, only plain text."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.6,
        "max_tokens": 400,
        "reasoning_effort": "low",
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
    print("پیام روانشناسی معامله‌گری با موفقیت ارسال شد.")


def main():
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    day_of_year = int(ny_time.strftime("%j"))
    topic = pick_topic(day_of_year)

    tip = ask_groq_for_tip(topic)
    if not tip:
        print("نکته‌ای دریافت نشد.")
        return

    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")
    lines = ["🧠 <b>Trading Psychology Tip of the Day</b>", "", tip, "", f"🕐 {now_text}"]
    message = "\n".join(lines)
    send_to_telegram(message)


if __name__ == "__main__":
    main()
