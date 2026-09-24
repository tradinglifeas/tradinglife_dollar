"""Create a source-linked, numeric-only G10 reference-rate review draft.

The draft is displayed in GitHub Actions. It never sends a Telegram message.
News context needs separate source verification before it can be added.
"""

import os
from pathlib import Path
from math import isfinite
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FRANKFURTER_URL = "https://api.frankfurter.dev/v1"


G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "NOK", "SEK"]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def get_rates(date_str):
    """Return the date and complete USD-based reference-rate snapshot."""
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


def save_draft(text):
    print("DRAFT ONLY. No Telegram message was sent.\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write("## Currency draft — manual review required\n\n" + text + "\n")


def main():
    strength, previous_date, latest_date = compute_strength()
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")

    lines = ["💱 G10 Currency Strength vs USD — Reference Rate Change",
             f"Reference dates: {previous_date} → {latest_date}", ""]
    for i, r in enumerate(strength, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        lines.append(f"{i}. {emoji} {r['currency']}: {r['percent']:+.2f}%")

    down_count = sum(r["percent"] < 0 for r in strength)
    lines.append("")
    lines.append(f"{down_count} of {len(strength)} currencies declined vs USD over these reference dates.")
    lines.append("Source: Frankfurter daily reference rates; not live trading quotes.")
    lines.append(f"Current rates: {FRANKFURTER_URL}/{latest_date}?base=USD")
    lines.append(f"Previous rates: {FRANKFURTER_URL}/{previous_date}?base=USD")
    lines.append(f"🕐 Draft generated: {now_text}")

    message = "\n".join(lines)
    save_draft(message)


if __name__ == "__main__":
    main()
