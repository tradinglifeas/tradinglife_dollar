"""
اسکریپت ساخت و ارسال گزارش «قدرت ارزهای G10» به کانال تلگرام (انگلیسی).

داده‌ی عددی: از Frankfurter API (رایگان، بدون نیاز به کلید) - نرخ رسمی بانک مرکزی اروپا.
تحلیل خبری: از Groq با قابلیت browser_search - مدل خودش وب رو جستجو می‌کنه.

پیام فقط به‌صورت متن (نه عکس) ارسال می‌شه.
"""

import os
import re
import time
from pathlib import Path
from math import isfinite
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

GROQ_API_KEY = os.environ["GROQ_API_KEY"]

FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
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


G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "NOK", "SEK"]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def clean_citations(text):
    """Keep references visible in a review draft; an editor must check real URLs."""
    return text.strip()


def get_rates(date_str):
    """Return the date and complete USD-based ECB reference-rate snapshot."""
    url = f"{FRANKFURTER_URL}/{date_str}"
    params = {"base": "USD", "symbols": ",".join(G10_CURRENCIES)}
    response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("base") != "USD":
        raise ValueError(f"Frankfurter returned base {data.get('base')!r}, not USD")
    data_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    if date_str != "latest" and data_date > datetime.strptime(date_str, "%Y-%m-%d").date():
        raise ValueError("Frankfurter returned a date after the requested date")
    rates = data.get("rates", {})
    if set(rates) != set(G10_CURRENCIES):
        raise ValueError(f"Incomplete currency list: {sorted(rates)}")
    if not all(isfinite(float(rate)) and float(rate) > 0 for rate in rates.values()):
        raise ValueError("Invalid exchange rate")
    return data_date, rates


def compute_strength():
    """Compare the last two *published* dates, not two arbitrary calendar days."""
    latest_date, latest_rates = get_rates("latest")
    age_days = (datetime.now(ZoneInfo("America/New_York")).date() - latest_date).days
    if age_days < 0 or age_days > 5:
        raise ValueError("Latest reference rate is too old for a current draft")
    earlier_date, earlier_rates = get_rates((latest_date - timedelta(days=1)).isoformat())
    if earlier_date >= latest_date or (latest_date - earlier_date).days > 5:
        raise ValueError("No reasonably recent previous reference date was returned")

    results = []
    for currency in G10_CURRENCIES:
        # USD -> currency quote must be inverted to measure currency -> USD.
        percent_change = (float(earlier_rates[currency]) / float(latest_rates[currency]) - 1) * 100
        results.append({"currency": currency, "percent": percent_change})

    results.sort(key=lambda x: x["percent"], reverse=True)
    return results, earlier_date, latest_date


def ask_groq_for_analysis(strength_summary, today_str, date_range):
    """
    از قابلیت browser_search خود Groq استفاده می‌کنیم تا مدل مستقیماً
    وب رو جستجو کنه و اخبار تازه‌ی فارکس رو ببینه.
    """
    prompt = (
        f"Today's date is {today_str}. Search the web for the most important "
        f"forex/currency market news from the last 24 to 48 hours (do not use "
        f"older news). If your search results are not clearly from around "
        f"{today_str}, search again with a more specific recent query. "
        f"The figures below compare official daily reference rates for {date_range}, "
        "not live or intraday prices. Write 2-3 neutral sentences about potentially "
        "relevant currency-market developments. Do not assert causation without evidence. "
        "For every factual claim, include the event date and a full original source URL; "
        "omit a claim if you cannot provide both. "
        "Do not quote sources verbatim; paraphrase in your own words. "
        "Do not give financial advice.\n\n"
        f"Today's G10 currency strength ranking (vs USD):\n{strength_summary}\n\n"
        "Write only the analysis paragraph; preserve source links."
    )

    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 1200,
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

    print("Groq draft response received.")
    try:
        message = data["choices"][0]["message"]
        content = (message.get("content") or "").strip()
        return clean_citations(content) if content else None
    except (KeyError, IndexError):
        return None


def save_draft(text):
    print("DRAFT ONLY. No Telegram message was sent.\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write("## Currency draft — manual review required\n\n" + text + "\n")


def main():
    strength, previous_date, latest_date = compute_strength()
    if not strength:
        print("داده‌ی قدرت ارزها دریافت نشد.")
        return

    strength_summary_lines = [f"{r['currency']}: {r['percent']:+.2f}%" for r in strength]
    strength_summary = "\n".join(strength_summary_lines)

    ny_time = datetime.now(ZoneInfo("America/New_York"))
    today_str = ny_time.strftime("%B %d, %Y")
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    analysis = ask_groq_for_analysis(strength_summary, today_str, f"{previous_date} to {latest_date}")

    lines = ["💱 G10 reference-rate change (vs USD)", f"Reference dates: {previous_date} → {latest_date}", ""]
    for i, r in enumerate(strength, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        lines.append(f"{i}. {emoji} {r['currency']}: {r['percent']:+.2f}%")

    if analysis:
        lines.append("")
        lines.append("📰 Possible context (verify source links before posting):")
        lines.append(analysis)

    lines.append("")
    lines.append("Source: Frankfurter daily reference rates; not live trading quotes.")
    lines.append(f"🕐 Draft generated: {now_text}")

    message = "\n".join(lines)
    save_draft(message)


if __name__ == "__main__":
    main()
