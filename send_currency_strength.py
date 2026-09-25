"""Build a G10 currency post and optionally publish it to a Telegram channel.

Source links appear only in the private GitHub Actions run summary.
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
TELEGRAM_URL = "https://api.telegram.org/bot{token}/{method}"

# Only show candidate links from established reporting and primary institutions
# in the private review section; this does not verify any article's claims.
REVIEW_SOURCE_DOMAINS = (
    "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "wsj.com",
    "cnbc.com", "federalreserve.gov", "ecb.europa.eu", "bls.gov",
    "bea.gov", "treasury.gov", "boj.or.jp", "bankofengland.co.uk",
    "bankofcanada.ca", "rba.gov.au", "snb.ch", "norges-bank.no",
    "riksbank.se", "rbnz.govt.nz", "ismworld.org", "spglobal.com",
)

# Groq selects only these evidence categories. The public sentence is assembled
# here, so it cannot append unsupported claims about sentiment or individual FX.
DRIVER_TAGS = {
    "FED_HIKE_EXPECTATIONS": ("expectations of further Fed rate increases", "strength"),
    "FED_CUT_EXPECTATIONS": ("expectations of Fed rate cuts", "weakness"),
    "US_DATA_STRONG": ("stronger U.S. economic data", "strength"),
    "US_DATA_WEAK": ("weaker U.S. economic data", "weakness"),
    "US_YIELDS_RISE": ("higher U.S. Treasury yields", "strength"),
    "US_YIELDS_FALL": ("lower U.S. Treasury yields", "weakness"),
    "OIL_MIDDLE_EAST_RISE": ("higher oil prices amid Middle East tensions", "strength"),
    "OIL_SUPPLY_FALL": ("lower oil prices as supply concerns eased", "weakness"),
}


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


def parse_analysis(raw, reference_date, usd_move):
    """Render only recognized drivers with eligible earlier-dated source rows."""
    raw = raw.strip()
    if raw.startswith("```") and raw.endswith("```"):
        raw = re.sub(r"^```(?:text)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
    match = re.fullmatch(
        r"\s*DRIVERS_FOR_REVIEW:\s*(.*?)\s*",
        raw, flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None, [], "Groq did not return the requested evidence categories."

    sources = []
    same_day_sources = []
    drivers = []
    for line in match.group(1).splitlines():
        line = re.sub(r"[【〖][^】〗]*[】〗]", "", line).strip()
        row = re.fullmatch(
            r"\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]{2,80})\s*\|\s*(https://[^\s|]+)\s*\|\s*([A-Z_]+)\s*",
            line,
        )
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
        driver = DRIVER_TAGS.get(row.group(4))
        if not driver or (usd_move != "mixed" and driver[1] != usd_move):
            continue
        if row.group(3) not in {source[2] for source in sources}:
            sources.append((row.group(1), row.group(2).strip(), row.group(3)))
        if row.group(4) not in drivers:
            drivers.append(row.group(4))

    if same_day_sources:
        return None, sources[:3] + same_day_sources[:2], (
            "The explanation cited a same-date article without a publication time. "
            "Check its time before the reference-rate fixing; explanation withheld."
        )
    if not drivers:
        return None, [], "No eligible earlier-dated driver with a source URL was supplied; explanation withheld."
    phrases = [DRIVER_TAGS[tag][0] for tag in drivers[:2]]
    if usd_move == "mixed":
        analysis = "Market reports highlighted " + " and ".join(phrases) + " as relevant context for currency trading."
    else:
        analysis = ("Market reports pointed to " + " and ".join(phrases)
                    + f" as possible factors behind the dollar's {usd_move}.")
    return analysis, sources[:4], "Candidate source dates and driver claims still require manual checking."


def ask_groq_for_analysis(strength, previous_date, latest_date):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None, [], "GROQ_API_KEY is missing; no market explanation was generated."

    summary = "\n".join(f"{row['currency']}: {row['percent']:+.2f}%" for row in strength)
    usd_move = ("strength" if all(row["percent"] < 0 for row in strength)
                else "weakness" if all(row["percent"] > 0 for row in strength) else "mixed")
    categories = ", ".join(DRIVER_TAGS)
    prompt = (
        f"Research market reporting relevant to G10 currencies versus USD between "
        f"the reference dates {previous_date} and {latest_date}. "
        "Search Reuters/AP/Bloomberg/FT or original central bank/economic releases. "
        f"Use only reports dated BEFORE {latest_date}, at most three days earlier, "
        "because a same-date article could have been published after the FX reference "
        "rate was fixed. Identify no more than TWO broad-dollar driver categories "
        "that an article explicitly links to the dollar move; do not infer a link "
        "from general economic theory. Avoid invented explanations for individual "
        "currencies, sentiment, or the move 'on both days': there is one comparison. "
        f"The dollar's move across all nine currencies is: {usd_move}. "
        f"Allowed category codes only: {categories}. "
        "Return NO prose, analysis paragraph, title, commentary or citation-only lines. "
        "For EACH category output its real article URL (not search results), actual "
        "publication date and publisher. If you cannot find evidence, output only "
        "the DRIVERS_FOR_REVIEW header. Exact plain-text output format:\n"
        "DRIVERS_FOR_REVIEW:\nYYYY-MM-DD | Publisher | https://article-url | CATEGORY_CODE\n"
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
            return parse_analysis(message.get("content") or "", latest_date, usd_move)
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
            return None, [], f"Groq analysis unavailable ({type(error).__name__}); no explanation was generated."
    return None, [], "Groq rate limit; no explanation was generated."


def add_run_summary(note):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write(note + "\n\n")


def save_preview(text, sources, review_note, previous_date, latest_date):
    print("Currency post preview:\n" + text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
            summary.write("## Currency post\n\n")
            summary.write("### Channel text (no links)\n\n" + text + "\n\n")
            summary.write("### Review only — not sent to Telegram\n\n")
            summary.write(review_note + "\n\n")
            for date, publisher, url in sources:
                summary.write(f"- {date} · {publisher}: {url}\n")
            summary.write(f"- Reference-rate dates: {previous_date} → {latest_date}. "
                          "Frankfurter/ECB daily reference rates; these are not live quotes.\n")


def telegram_api(method, token, payload):
    """Keep the bot token out of exception messages and GitHub Actions logs."""
    try:
        response = requests.post(TELEGRAM_URL.format(token=token, method=method),
                                 json=payload, timeout=20)
        if response.status_code != 200:
            raise RuntimeError(f"Telegram {method} failed (HTTP {response.status_code}).")
        data = response.json()
    except requests.RequestException as error:
        raise RuntimeError(f"Telegram {method} request failed ({type(error).__name__}).") from None
    except ValueError:
        raise RuntimeError(f"Telegram {method} returned invalid JSON.") from None
    if data.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} rejected the request (code {data.get('error_code')}).")
    return data.get("result", {})


def publish_to_telegram(text):
    token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = (os.environ.get("CURRENCY_CHAT_ID") or os.environ.get("CHAT_ID") or "").strip()
    if not token or not chat_id:
        raise RuntimeError("BOT_TOKEN and CURRENCY_CHAT_ID (or CHAT_ID) must be set in Actions secrets.")
    if not text or len(text) > 4096 or re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
        raise RuntimeError("Channel text must be at most 4096 characters and contain no links.")

    chat = telegram_api("getChat", token, {"chat_id": chat_id})
    if chat.get("type") != "channel":
        raise RuntimeError("Configured Telegram destination is not a channel; nothing was sent.")
    result = telegram_api("sendMessage", token, {
        "chat_id": chat_id,
        "text": text,
        "link_preview_options": {"is_disabled": True},
    })
    return result.get("message_id")


def main():
    strength, previous_date, latest_date = compute_strength()
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and latest_date != ny_time.date():
        note = (f"No new reference rate for {ny_time.date()}; latest published "
                f"reference date is {latest_date}. Nothing was sent to Telegram.")
        print(note)
        add_run_summary(f"**Status:** {note}")
        return
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
    lines.append(f"🕐 Updated: {now_text}")

    message = "\n".join(lines)
    save_preview(message, sources, review_note, previous_date, latest_date)

    if os.environ.get("PUBLISH_TO_TELEGRAM") != "true":
        add_run_summary("**Status:** Preview only; no Telegram message was sent.")
        return
    if not analysis or not sources:
        add_run_summary("**Status:** No Telegram message was sent: a sourced market explanation was unavailable.")
        raise RuntimeError("No eligible sourced market explanation; nothing was sent to Telegram.")
    try:
        message_id = publish_to_telegram(message)
    except RuntimeError as error:
        add_run_summary(f"**Status:** Telegram publication failed: {error}")
        raise
    print(f"Sent currency post to Telegram (message id: {message_id}).")
    add_run_summary(f"**Status:** Sent to Telegram channel (message id: {message_id}).")


if __name__ == "__main__":
    main()
