"""
اسکریپت ساخت و ارسال «Key Market News» به کانال تلگرام (انگلیسی).

طراحی قابل‌اعتماد:
  ۱. خبرها فقط از فیدهای RSS واقعی با تاریخ انتشار مشخص گرفته می‌شن
     (CNBC، MarketWatch، Bloomberg، فدرال رزرو، ECB). هیچ جستجوی آزاد وبی انجام نمی‌شه.
  ۲. کد خبرهای قدیمی رو خودش حذف می‌کنه (۲۴ ساعت؛ دوشنبه‌ها ۷۲ ساعت برای پوشش آخر هفته).
  ۳. Groq فقط از بین همین خبرهای واقعی، با «شماره»، مهم‌ترین‌ها رو انتخاب می‌کنه
     و یک خلاصه‌ی کوتاه می‌نویسه. کد این‌ها رو چک می‌کنه:
        - شماره‌ی خبر باید واقعاً در لیست باشه
        - هر عددی که در خلاصه اومده باید در خود خبر اصلی هم باشه (جلوی عدد ساختگی)
        - خلاصه نباید کپی عین عنوان خبر باشه
  ۴. اسم منبع کنار هر خبر نوشته می‌شه (منبع رو کد می‌نویسه، نه مدل).
  ۵. اگه خبر معتبری پیدا نشه، پستی نمی‌ره و اجرا با خطا (قرمز) تموم می‌شه تا متوجه بشید.
"""

import os
import re
import json
import html
import time
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

# (اسم منبع، آدرس فید) — اگه یکی از کار بیفته، بقیه کار می‌کنن
FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),      # Top News
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),       # Economy
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),       # Finance
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("ECB", "https://www.ecb.europa.eu/rss/press.html"),
]

MIN_ITEMS_TO_POST = 3
MAX_ITEMS_TO_POST = 5
MAX_CANDIDATES = 45

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# ---------------------------------------------------------------- جمع‌آوری خبرها

def strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(text):
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text.strip())          # RSS: "Fri, 25 Sep 2026 14:03:00 GMT"
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))   # Atom
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def fetch_feed(source, url):
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except (requests.RequestException, ET.ParseError) as e:
        print(f"  ✗ {source}: {url} -> {e}")
        return []
    items = []
    # RSS <item> و Atom <entry> هر دو پشتیبانی می‌شن
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        fields = {c.tag.split("}")[-1]: (c.text or "") for c in node}
        title = strip_tags(fields.get("title", ""))
        desc = strip_tags(fields.get("description") or fields.get("summary") or "")[:400]
        when = parse_date(fields.get("pubDate") or fields.get("published")
                          or fields.get("updated") or fields.get("date"))
        link = (fields.get("link") or "").strip()
        if not link:
            for c in node:
                if c.tag.split("}")[-1] == "link" and c.get("href"):
                    link = c.get("href"); break
        low = (link + " " + title).lower()
        if any(k in low for k in ("/video", "/videos/", "/opinion/", "podcast", "/audio/", "/live-blog")):
            continue   # ویدیو، مصاحبه، ستون نظر و پادکست خبر نیستن
        if title and when:
            items.append({"source": source, "title": title, "desc": desc, "time": when, "link": link})
    print(f"  ✓ {source}: {len(items)} items  ({url})")
    return items


def collect_news():
    now = datetime.now(timezone.utc)
    ny = datetime.now(ZoneInfo("America/New_York"))
    hours = 72 if ny.weekday() == 0 else 24           # دوشنبه: خبرهای آخر هفته هم حساب می‌شن
    cutoff = now - timedelta(hours=hours)

    print("دریافت فیدها:")
    all_items = []
    for source, url in FEEDS:
        all_items += fetch_feed(source, url)

    fresh, seen = [], set()
    for it in sorted(all_items, key=lambda x: x["time"], reverse=True):
        if not (cutoff <= it["time"] <= now + timedelta(minutes=10)):
            continue
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        fresh.append(it)
    print(f"خبرهای {hours} ساعت اخیر (بدون تکرار): {len(fresh)} از {len(all_items)}")
    return fresh[:MAX_CANDIDATES], hours


# ---------------------------------------------------------------- انتخاب و خلاصه با Groq

def numbers_in(text):
    """عددهای مهم یک متن (درصد، مبلغ، سال و ...) به شکل نرمال‌شده"""
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", text)}


def longest_common_run(a, b):
    """طولانی‌ترین تعداد کلمات پشت‌سرهم مشترک بین دو متن"""
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    best = 0
    for i in range(len(wa)):
        for j in range(len(wb)):
            k = 0
            while i + k < len(wa) and j + k < len(wb) and wa[i + k] == wb[j + k]:
                k += 1
            best = max(best, k)
    return best


def validate(selection, candidates):
    ok = []
    for sel in selection:
        try:
            idx = int(sel.get("id"))
            label = str(sel.get("label", "")).strip().rstrip(":")
            summary = re.sub(r"[【〖][^】〗]*[】〗]", "", str(sel.get("summary", ""))).strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= idx <= len(candidates):
            print(f"  رد شد: شماره‌ی نامعتبر {idx}"); continue
        item = candidates[idx - 1]
        if idx in [o["id"] for o in ok]:
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 &/.\-]{0,17}", label):
            label = "Markets"
        if not 30 <= len(summary) <= 260:
            print(f"  رد شد #{idx}: طول خلاصه نامناسب"); continue
        source_text = item["title"] + " " + item["desc"]
        extra = numbers_in(summary) - numbers_in(source_text)
        if extra:
            print(f"  رد شد #{idx}: عدد(های) ساختگی {extra} در خلاصه"); continue
        if max(longest_common_run(summary, item["title"]),
               longest_common_run(summary, item["desc"])) >= 9:
            print(f"  رد شد #{idx}: خلاصه تقریباً کپی متن منبع است"); continue
        # اگه خبر اصلی «گزارش» یا «نقل‌قول» است، خلاصه هم باید همین رو بگه
        # (نه اینکه یک ادعای گزارش‌شده رو به‌عنوان واقعیت قطعی بیان کنه)
        if re.search(r"\b(reports?|reported|according to|citing|sources say|WSJ|people familiar)\b",
                     source_text, re.I) and not re.search(
                r"\b(report|reported|reports|according|citing|said|says|told)\b", summary, re.I):
            print(f"  رد شد #{idx}: منبعِ گزارش‌شده در خلاصه حذف شده"); continue
        ok.append({"id": idx, "label": label, "summary": summary, "source": item["source"]})
        print(f"  قبول شد #{idx} [{item['source']}] {label}: {summary}")
    return ok[:MAX_ITEMS_TO_POST]


def call_groq(payload, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            r = requests.post(GROQ_URL, json=payload, headers=headers, timeout=90)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"خطا در Groq: {e}")
            return None
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "rate_limit_exceeded":
            m = re.search(r"try again in ([\d.]+)s", err.get("message", ""))
            wait = float(m.group(1)) + 2 if m else 15
            print(f"محدودیت نرخ Groq - {wait:.1f} ثانیه صبر")
            time.sleep(min(wait, 30))
            continue
        if err:
            print(f"خطای Groq: {err}")
            return None
        return data
    return None


def select_with_groq(candidates):
    listing = "\n".join(
        f"[{i}] ({c['source']}, {c['time'].strftime('%b %d %H:%M UTC')}) {c['title']}"
        + (f" — {c['desc']}" if c["desc"] else "")
        for i, c in enumerate(candidates, 1)
    )
    prompt = (
        "Below is a numbered list of real, recent news items from financial news feeds.\n"
        f"Select the {MIN_ITEMS_TO_POST} to {MAX_ITEMS_TO_POST} items that matter MOST to global "
        "financial markets (central banks, inflation/jobs/GDP data, bond yields, oil and energy, "
        "major geopolitical events, big moves in major stock indexes or the dollar). "
        "Prefer concrete events and data (decisions, releases, market moves) over interviews, commentary "
        "or analysis. Skip personal-finance tips, opinion pieces, single-company gossip, and duplicates "
        "of the same story.\n"
        "For each selected item write ONE neutral sentence (max 35 words) summarizing it.\n"
        "STRICT RULES:\n"
        "- Use ONLY information stated in that item's title/description. Add no facts, causes, "
        "numbers, names, or predictions that are not there.\n"
        "- Paraphrase in your own words; do not copy the headline or description.\n"
        "- If the item says something was REPORTED by a news outlet or anonymous officials "
        "(e.g. 'WSJ reports', 'according to sources'), keep that attribution in your sentence "
        "(e.g. 'The Wall Street Journal reported that...'). Never turn a report into a confirmed fact.\n"
        "- No financial advice.\n"
        "- label = 1-2 words topic, e.g. Fed, ECB, Inflation, Jobs, Oil, Stocks, Bonds, Dollar, Geopolitics.\n"
        'Return ONLY JSON: {"items":[{"id":<number>,"label":"<topic>","summary":"<sentence>"}]}\n\n'
        f"NEWS ITEMS:\n{listing}"
    )
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 1500,
            "reasoning_effort": "medium",
            "response_format": {"type": "json_object"},
        }
        data = call_groq(payload, headers)
        if not data:
            continue
        try:
            raw = (data["choices"][0]["message"].get("content") or "").strip()
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            selection = json.loads(m.group(0))["items"] if m else []
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as e:
            print(f"خروجی Groq ({model}) قابل خواندن نبود: {e}")
            continue
        print(f"Groq ({model}) {len(selection)} خبر انتخاب کرد. اعتبارسنجی:")
        valid = validate(selection, candidates)
        if len(valid) >= MIN_ITEMS_TO_POST:
            return valid
        print(f"فقط {len(valid)} خبر معتبر - تلاش با مدل بعدی")
    return []


# ---------------------------------------------------------------- ارسال

def send_to_telegram(text):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    r = requests.post(TELEGRAM_API_URL, data=payload, timeout=20)
    result = r.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print("پیام اخبار با موفقیت ارسال شد.")


def main():
    ny = datetime.now(ZoneInfo("America/New_York"))
    candidates, hours = collect_news()
    if len(candidates) < MIN_ITEMS_TO_POST:
        raise SystemExit(f"خبر تازه‌ی کافی از فیدها دریافت نشد ({len(candidates)}). پستی ارسال نشد.")

    items = select_with_groq(candidates)
    if len(items) < MIN_ITEMS_TO_POST:
        raise SystemExit("خبر معتبر کافی انتخاب نشد. پستی ارسال نشد.")

    lines = ["📰 <b>Key Market News</b>", f"📅 {ny.strftime('%b %d, %Y')}", ""]
    for it in items:
        lines.append(f"• <b>{html.escape(it['label'])}:</b> {html.escape(it['summary'])} "
                     f"<i>({html.escape(it['source'])})</i>")
        lines.append("")
    window = "past 72 hours" if hours == 72 else "past 24 hours"
    lines += [f"<i>Summaries of reports from the named sources, {window}. Not investment advice.</i>",
              f"🕐 Updated: {ny.strftime('%Y-%m-%d %H:%M %Z')}"]
    send_to_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
