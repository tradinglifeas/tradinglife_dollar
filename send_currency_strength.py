"""
اسکریپت ساخت و ارسال گزارش «قدرت ارزهای G10» به کانال تلگرام (انگلیسی).

داده‌ی عددی: Frankfurter API (رایگان، بدون کلید) = نرخ‌های مرجع رسمی بانک مرکزی اروپا (ECB).
  - ECB هر روز کاری حدود ساعت 14:15 به وقت اروپای مرکزی نرخ‌ها رو تثبیت می‌کنه.
  - آخر هفته و تعطیلات نرخ جدید منتشر نمی‌شه.
  - برای همین همیشه «دو تاریخ آخری که واقعاً منتشر شدن» مقایسه می‌شن و روی پست نوشته می‌شن.

تحلیل خبری: Groq (با عنوان‌های Bloomberg یا browser_search).

ارزها: ۹ ارز G10 در برابر دلار آمریکا (USD دهمین ارز G10 است).
"""

import os
import re
import time
import html
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
FRANKFURTER_URLS = ["https://api.frankfurter.app", "https://api.frankfurter.dev/v1"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
BLOOMBERG_RSS_URL = "https://feeds.bloomberg.com/economics/news.rss"

# ۹ ارز G10 به‌جز خود دلار
G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "NOK", "SEK"]

# اگه آخرین نرخ منتشرشده قدیمی‌تر از این باشه، پست نمی‌ره (داده‌ی کهنه)
MAX_DATA_AGE_DAYS = 4

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


# ---------------------------------------------------------------- داده‌ی نرخ‌ها

def get_rate_history(days_back=12):
    """
    همه‌ی نرخ‌های منتشرشده‌ی چند روز اخیر رو یکجا می‌گیره.
    خروجی: لیست (تاریخ، نرخ‌ها) از قدیم به جدید، فقط روزهایی که هر ۹ ارز رو دارن.
    نرخ‌ها به شکل «واحد ارز به ازای ۱ دلار» هستن.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = today - timedelta(days=days_back)
    params = {"from": "USD", "to": ",".join(G10_CURRENCIES)}
    last_error = None
    for base in FRANKFURTER_URLS:
        try:
            url = f"{base}/{start.isoformat()}..{today.isoformat()}"
            response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=20)
            response.raise_for_status()
            data = response.json()
            rows = []
            for day, rates in sorted(data.get("rates", {}).items()):
                if all(c in rates and float(rates[c]) > 0 for c in G10_CURRENCIES):
                    rows.append((datetime.strptime(day, "%Y-%m-%d").date(), rates))
            print(f"Frankfurter ({base}): {len(rows)} published dates -> {[d.isoformat() for d, _ in rows]}")
            if len(rows) >= 2:
                return rows
            last_error = f"only {len(rows)} complete dates"
        except (requests.RequestException, ValueError, KeyError) as e:
            last_error = e
            print(f"خطا در Frankfurter ({base}): {e}")
    raise RuntimeError(f"داده‌ی نرخ ارز دریافت نشد: {last_error}")


def compute_strength():
    """
    دو تاریخ آخری که ECB واقعاً نرخ منتشر کرده رو مقایسه می‌کنه (نه روزهای تقویمی).
    فرمول دقیق: نرخ به شکل «ارز برای هر دلار» است، پس ارزش ارز در برابر دلار عکس آن است:
        تغییر٪ = (نرخ قبلی ÷ نرخ جدید − 1) × 100
    """
    rows = get_rate_history()
    (prev_date, prev_rates), (last_date, last_rates) = rows[-2], rows[-1]

    today = datetime.now(ZoneInfo("America/New_York")).date()
    age = (today - last_date).days
    if age > MAX_DATA_AGE_DAYS:
        raise RuntimeError(f"آخرین نرخ ECB مال {last_date} است ({age} روز پیش) - داده کهنه است.")

    results = []
    for c in G10_CURRENCIES:
        percent = (float(prev_rates[c]) / float(last_rates[c]) - 1) * 100
        results.append({"currency": c, "percent": percent})
    results.sort(key=lambda x: x["percent"], reverse=True)

    print(f"مقایسه: {prev_date} -> {last_date}")
    for r in results:
        print(f"  {r['currency']}: {r['percent']:+.3f}%")
    return results, prev_date, last_date


def dollar_direction(strength):
    """جهت کلی دلار: چند ارز از ۹ تا در برابر دلار قوی‌تر/ضعیف‌تر شدن."""
    up = sum(1 for r in strength if r["percent"] > 0)
    down = sum(1 for r in strength if r["percent"] < 0)
    if up >= 7:
        return f"the U.S. dollar WEAKENED against {up} of 9 G10 currencies"
    if down >= 7:
        return f"the U.S. dollar STRENGTHENED against {down} of 9 G10 currencies"
    return f"the U.S. dollar was MIXED ({up} currencies gained vs USD, {down} fell)"


# ---------------------------------------------------------------- تحلیل خبری

def clean_citations(text):
    """علامت‌های استناد داخلی مدل (مثل 【3†L163-L170】) رو پاک می‌کنه"""
    text = re.sub(r"[【〖][^】〗]*[】〗]", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def get_bloomberg_headlines(limit=8):
    try:
        response = requests.get(BLOOMBERG_RSS_URL, headers=REQUEST_HEADERS, timeout=15)
        root = ET.fromstring(response.content)
    except (requests.RequestException, ET.ParseError) as e:
        print(f"خطا در خوندن فید Bloomberg: {e}")
        return []
    headlines = []
    for item in root.findall(".//item")[:limit]:
        title = item.findtext("title", default="").strip()
        desc = item.findtext("description", default="").strip()
        pub = item.findtext("pubDate", default="").strip()
        if title:
            line = f"[{pub}] {title}" if pub else title
            headlines.append(f"{line} — {desc}" if desc else line)
    print(f"عنوان‌های Bloomberg: {len(headlines)}")
    return headlines


def call_groq_with_retry(payload, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=60)
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"خطا در Groq: {e}")
            return None
        error = data.get("error") if isinstance(data, dict) else None
        if error and error.get("code") == "rate_limit_exceeded":
            match = re.search(r"try again in ([\d.]+)s", error.get("message", ""))
            wait = float(match.group(1)) + 2 if match else 15
            print(f"محدودیت نرخ Groq - {wait:.1f} ثانیه صبر (تلاش {attempt + 1}/{max_retries})")
            time.sleep(min(wait, 30))
            continue
        if error:
            print(f"خطای Groq: {error}")
            return None
        return data
    return None


def ask_groq_for_analysis(strength, prev_date, last_date, headlines):
    if not GROQ_API_KEY:
        print("GROQ_API_KEY موجود نیست - تحلیل حذف شد.")
        return None

    summary = "\n".join(f"{r['currency']}: {r['percent']:+.2f}%" for r in strength)
    direction = dollar_direction(strength)
    period = f"{prev_date.strftime('%B %d')} to {last_date.strftime('%B %d, %Y')}"

    rules = (
        f"These are ECB reference-rate changes from {period} (one fixing to the next). "
        f"Overall, {direction}. Positive % = the currency gained against USD; negative = it lost.\n"
        f"Ranking:\n{summary}\n\n"
        "Write a short, neutral 2-3 sentence explanation in English of what drove the dollar "
        f"during {period}. STRICT RULES: "
        "(1) Your description of the dollar's direction MUST match the data above — never "
        "say the dollar rallied if it weakened, or vice versa. "
        f"(2) Only use news published between {prev_date.isoformat()} and {last_date.isoformat()}. "
        "(3) Do not invent statements by officials, central banks, or specific numbers that are "
        "not in your sources. If you are unsure why a currency moved, do not mention it. "
        "(4) No financial advice, no quotes, no citations, no brackets, no links. "
        "Write only the paragraph."
    )
    if headlines:
        prompt = ("Recent Bloomberg Economics headlines (with publish times):\n"
                  + "\n".join(f"- {h}" for h in headlines)
                  + "\n\nUse these as your primary source; you may search the web for more "
                  "context from the same dates.\n\n" + rules)
        tool_choice = "auto"
    else:
        prompt = ("Search the web for forex/dollar news from the dates below, then answer.\n\n" + rules)
        tool_choice = "required"

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 700,
        "reasoning_effort": "low",
        "tools": [{"type": "browser_search"}],
        "tool_choice": tool_choice,
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = call_groq_with_retry(payload, headers)
    if not data:
        return None
    try:
        content = (data["choices"][0]["message"].get("content") or "").strip()
    except (KeyError, IndexError, TypeError):
        return None
    # متن reasoning مدل رو هیچ‌وقت به‌عنوان تحلیل منتشر نمی‌کنیم
    if not content:
        print("Groq متن نهایی برنگردوند - تحلیل حذف شد.")
        return None
    content = clean_citations(content)
    print(f"تحلیل Groq: {content}")
    return content if len(content) > 40 else None


# ---------------------------------------------------------------- ارسال

def send_to_telegram(text):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    response = requests.post(TELEGRAM_API_URL, data=payload, timeout=20)
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print("پیام قدرت ارزها با موفقیت ارسال شد.")


def main():
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    # نکته: آخر هفته ECB نرخ جدید منتشر نمی‌کنه. زمان‌بندی این گزارش در cron-job.org
    # باید فقط دوشنبه تا جمعه باشه تا پست تکراری نره.

    strength, prev_date, last_date = compute_strength()
    analysis = ask_groq_for_analysis(strength, prev_date, last_date, get_bloomberg_headlines())

    lines = [
        "💱 <b>G10 Currency Strength (vs USD)</b>",
        f"📅 {prev_date.strftime('%b %d')} → {last_date.strftime('%b %d, %Y')}",
        "",
    ]
    for i, r in enumerate(strength, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        lines.append(f"{i}. {emoji} {r['currency']}: {r['percent']:+.2f}%")

    if analysis:
        lines += ["", "📰 <b>What's driving it:</b>", html.escape(analysis)]

    lines += [
        "",
        "<i>ECB daily reference rates (14:15 CET fixing), not live quotes.</i>",
        f"🕐 Updated: {ny_now.strftime('%Y-%m-%d %H:%M %Z')}",
    ]
    send_to_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
