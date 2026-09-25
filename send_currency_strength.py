"""Draft a G10 currency post with a market explanation and private review notes.

The copyable post contains no links. Nothing is sent to Telegram automatically.
"""

import os
import re
import time
from pathlib import Path
from math import isfinite
from urllib.parse import urlsplit
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Only show candidate links from established reporting and primary institutions
# in the private review section; this does not verify any article's claims.
REVIEW_SOURCE_DOMAINS = (
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "cnbc.com", "federalreserve.gov", "ecb.europa.eu", "bls.gov",
    "bea.gov", "treasury.gov", "boj.or.jp", "bankofengland.co.uk",
    "bankofcanada.ca", "rba.gov.au", "snb.ch", "norges-bank.no",
    "riksbank.se", "rbnz.govt.nz", "ismworld.org", "spglobal.com",
)


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


def parse_analysis(raw, reference_date):
    """Require earlier-dated sources and avoid invented pair-specific drivers."""
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = re.sub(r"^```(?:text)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    match = re.fullmatch(
        r"\s*POST_ANALYSIS:\s*(.*?)\s*SOURCES_FOR_REVIEW:\s*(.*?)\s*",
        raw, flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, [], "Groq did not return the requested review format."

    analysis = re.sub(r"[【〖][^】〗]*[】〗]", "", match.group(1))
    analysis = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", analysis)
    analysis = re.sub(r"https?://\S+|www\.\S+", "", analysis)
    analysis = " ".join(analysis.split()).strip(" -*")
    analysis = re.sub(r"\s+([.,;:!?])", r"\1", analysis)
    if not 35 <= len(analysis) <= 900:
        return None, [], "Groq returned an empty or unusually long explanation."
    if re.search(r"\b(both days|each day|on each day)\b", analysis, re.IGNORECASE):
        return None, [], "The explanation describes two daily moves, but the data contain only one dated comparison."
    if re.search(
        r"\b(yen|franc|loonie|krona|krone|sterling|pound|euro|"
        r"canadian dollar|australian dollar|new zealand dollar|"
        r"CHF|CAD|JPY|EUR|GBP|AUD|NZD|NOK|SEK)\b",
        analysis, re.IGNORECASE,
    ):
        return None, [], "Groq attributed relative currency moves to unverified pair-specific drivers."

    sources = []
    same_day_sources = []
    for line in match.group(2).splitlines():
        line = re.sub(r"[【〖][^】〗]*[】〗]", "", line).strip()
        row = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]{2,80})\s*\|\s*(https://\S+)\s*", line)
        if not row:
            continue
        try:
            published = datetime.strptime(row.group(1), "%Y-%m-%d").date()
            parsed = urlsplit(row.group(3))
            host = (parsed.hostname or "").lower()
            allowed = any(host == domain or host.endswith("." + domain)
                          for domain in REVIEW_SOURCE_DOMAINS)
            if not allowed or parsed.username or parsed.password or parsed.port not in (None, 443):
                continue
            if published == reference_date:
                # A day-only timestamp cannot prove publication preceded the
                # ECB reference-rate fixing on that same date.
                same_day_sources.append((row.group(1), row.group(2).strip(), row.group(3)))
                continue
            if not reference_date - timedelta(days=3) <= published < reference_date:
                continue
        except (ValueError, TypeError):
            continue
        if row.group(3) not in [source[2] for source in sources]:
            sources.append((row.group(1), row.group(2).strip(), row.group(3)))

    if same_day_sources:
        return None, sources[:3] + same_day_sources[:2], (
            "The explanation cited a same-date article without a publication time. "
            "Check its time before the reference-rate fixing; explanation withheld."
        )
    if not sources:
        return None, [], "No eligible earlier-dated source URL was supplied; explanation withheld."
    return analysis, sources[:4], "Candidate source dates and claims still require manual checking."


def ask_groq_for_analysis(strength, previous_date, latest_date):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None, [], "GROQ_API_KEY is missing; no market explanation was generated."

    summary = "\n".join(f"{row['currency']}: {row['percent']:+.2f}%" for row in strength)
    prompt = (
        f"Write a short English market explanation for changes in G10 currencies versus USD "
        f"between the published reference dates {previous_date} and {latest_date}. "
        "Search for contemporaneous reporting from Reuters/AP/Bloomberg/FT or primary "
        "central bank/economic sources. Because only article dates can be checked by this "
        f"program, use only reports dated BEFORE {latest_date}, no more than three days "
        "earlier. An article published on the reference date might have appeared after "
        "the reference-rate fixing. Describe one plausible, broad USD market backdrop "
        "from those reports; do not speculate about reasons why individual currencies "
        "differed. The input contains one change between two snapshots, NOT two daily "
        "moves; never write 'on both days'. "
        "Separate observations from plausible explanations; avoid unsupported claims "
        "of causation, precise index levels, future rate decisions, or interventions. "
        "If no timely evidence exists, say so plainly rather than inventing a reason. "
        "Use 1-2 neutral sentences without advice, URLs, or citation markers in the analysis. "
        "After the analysis list each actual article URL (not a search URL) with its "
        "publication date and publisher, only if you found it while searching. "
        "Return exactly this plain-text format, without Markdown fences:\n"
        "POST_ANALYSIS:\n<1-2 sentences about broad USD drivers only>\nSOURCES_FOR_REVIEW:\n"
        "YYYY-MM-DD | Publisher | https://article-url\n"
        f"Verified reference-rate changes (not intraday trading moves):\n{summary}"
    )
    payload = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": 1200,
        "reasoning_effort": "low",
        "tools": [{"type": "browser_search"}],
        "tool_choice": "required",
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    for attempt in range(2):
        try:
            response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=65)
            if response.status_code == 429 and attempt == 0:
                retry_after = response.headers.get("Retry-After", "15")
                try:
                    wait = min(max(float(retry_after), 1), 25)
                except ValueError:
                    wait = 15
                time.sleep(wait)
                continue
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            return parse_analysis(message.get("content") or "", latest_date)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
            return None, [], f"Groq analysis unavailable ({type(error).__name__}); no explanation was generated."
    return None, [], "Groq rate limit; no explanation was generated."


def save_draft(text, sources, review_note, previous_date, latest_date):
    print("DRAFT ONLY. No Telegram message was sent.\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write("## Currency draft — manual review required\n\n")
            summary.write("### Copyable channel post (no links)\n\n" + text + "\n\n")
            summary.write("### Review only — do not copy into the channel\n\n")
            summary.write(review_note + "\n\n")
            for date, publisher, url in sources:
                summary.write(f"- {date} · {publisher}: {url}\n")
            summary.write(f"- Reference-rate dates: {previous_date} → {latest_date}. "
                          "Frankfurter/ECB daily reference rates; these are not live quotes.\n")


def main():
    strength, previous_date, latest_date = compute_strength()
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    now_text = ny_time.strftime("%Y-%m-%d %H:%M %Z")
    analysis, sources, review_note = ask_groq_for_analysis(strength, previous_date, latest_date)

    lines = ["💱 G10 Currency Strength (vs USD)",
             f"Reference dates: {previous_date} → {latest_date}", ""]
    for i, r in enumerate(strength, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        lines.append(f"{i}. {emoji} {r['currency']}: {r['percent']:+.2f}%")

    if analysis:
        strongest = " and ".join(row["currency"] for row in strength[:2])
        weakest = " and ".join(row["currency"] for row in strength[-2:])
        relative_moves = (
            f"In this reference-rate comparison, {strongest} held up best "
            f"against the dollar, while {weakest} weakened the most."
        )
        lines += ["", "📰 What's driving it:", analysis + " " + relative_moves]
    else:
        review_note = review_note + " Draft has no market explanation."
    lines.append("")
    lines.append("Daily reference-rate change; not a live trading quote.")
    lines.append(f"🕐 Draft generated: {now_text}")

    message = "\n".join(lines)
    save_draft(message, sources, review_note, previous_date, latest_date)


if __name__ == "__main__":
    main()
