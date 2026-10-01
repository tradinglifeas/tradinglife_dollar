"""
تست دسترسی منابع «دیدگاه بانک‌ها» از سرورهای GitHub — مرحله‌ی اول پروژه‌ی Bank Views.

هیچ چیزی در کانال ارسال نمی‌شه. نتیجه:
  - در لاگ GitHub Actions (جدول کامل)
  - و اگه Secret به اسم MY_CHAT_ID وجود داشته باشه، یک خلاصه‌ی فارسی به چت خصوصی شما

برای هر منبع بررسی می‌شه: جواب داد؟ RSS هست یا صفحه‌ی HTML؟ چند مطلب داره؟ جدیدترین مطلب مال کِیه؟
"""

import os
import re
import time
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
MY_CHAT_ID = os.environ.get("MY_CHAT_ID", "")

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "text/html,application/rss+xml,application/xml,application/json,*/*",
           "Accept-Language": "en-US,en;q=0.9"}

# (گروه، اسم منبع، آدرس، کلمات کلیدی برای شمارش در صفحه‌های HTML)
SOURCES = [
    # --- ING
    ("ING", "ING THINK — latest", "https://think.ing.com/latest/", r"FX Daily|Commodities Feed|FX Talking"),
    ("ING", "ING THINK — FX", "https://think.ing.com/market/fx/", r"FX Daily|FX Talking|dollar"),
    ("ING", "ING THINK — commodities", "https://think.ing.com/market/commodities/", r"Commodities Feed|gold|silver"),
    ("ING", "ING THINK — RSS (guess)", "https://think.ing.com/rss/", None),
    ("ING", "FXStreet — ING author page", "https://www.fxstreet.com/company/ing-direct", r"FX Daily|Commodities Feed"),
    # --- Danske
    ("Danske", "Danske research site", "https://research.danskebank.com/research/", r"FX Forecast|Weekly Focus"),
    ("Danske", "ActionForex — Danske feed", "https://www.actionforex.com/author/danske/feed/", None),
    ("Danske", "ActionForex — Danske page", "https://www.actionforex.com/author/danske/", r"FX Forecast Update|Weekly Focus"),
    # --- Republishing hubs (ING, MUFG, Commerzbank, Scotiabank, BBH... appear here)
    ("Hubs", "FXStreet — analysis RSS", "https://www.fxstreet.com/rss/analysis", None),
    ("Hubs", "FXStreet — news RSS", "https://www.fxstreet.com/rss/news", None),
    ("Hubs", "ActionForex — main feed", "https://www.actionforex.com/feed/", None),
    # --- US banks (public pages)
    ("US banks", "Goldman Sachs Insights", "https://www.goldmansachs.com/insights", r"gold|S&P|outlook|forecast"),
    ("US banks", "Morgan Stanley Ideas", "https://www.morganstanley.com/ideas", r"outlook|S&P|stocks|market"),
    ("US banks", "JPMorgan Guide to the Markets",
     "https://am.jpmorgan.com/us/en/asset-management/adv/insights/market-insights/guide-to-the-markets/",
     r"Guide to the Markets"),
    ("US banks", "Wells Fargo Economics", "https://www.wellsfargo.com/cib/insights/economics/", r"outlook|economic"),
    # --- Others
    ("Others", "UBS insights", "https://www.ubs.com/global/en/wealthmanagement/insights.html", r"gold|outlook"),
    ("Others", "World Gold Council — Goldhub research", "https://www.gold.org/goldhub/research", r"ETF|central bank|demand"),
    ("Others", "World Gold Council — news", "https://www.gold.org/news-and-events/press-releases", r"gold"),
    # --- News feeds already proven from GitHub (for U.S. bank target changes)
    ("News", "CNBC Top News RSS", "https://www.cnbc.com/id/100003114/device/rss/rss.html", None),
    ("News", "MarketWatch Top Stories RSS", "https://feeds.content.dowjones.io/public/rss/mw_topstories", None),
    ("News", "Bloomberg Markets RSS", "https://feeds.bloomberg.com/markets/news.rss", None),
]

BANK_WORDS = r"Goldman|JPMorgan|J\.P\. Morgan|Morgan Stanley|Bank of America|BofA|Citi(group)?|Wells Fargo|UBS|ING|Danske|Commerzbank|MUFG|Scotiabank"


def parse_date(t):
    if not t:
        return None
    try:
        d = parsedate_to_datetime(t.strip())
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def probe(group, name, url, pattern):
    r0 = {"group": group, "name": name, "url": url, "ok": False, "kind": "", "items": 0,
          "newest": None, "hits": 0, "bank_items": 0, "note": ""}
    try:
        r = requests.get(url, headers=HEADERS, timeout=25, allow_redirects=True)
        r0["status"] = r.status_code
        body = r.text or ""
        r0["size"] = len(body)
        if r.status_code != 200:
            r0["note"] = f"HTTP {r.status_code}"
            return r0
        head = body.lstrip()[:400].lower()
        if "<rss" in head or "<feed" in head or "<?xml" in head:
            r0["kind"] = "RSS"
            root = ET.fromstring(r.content)
            items = [n for n in root.iter() if n.tag.split("}")[-1] in ("item", "entry")]
            r0["items"] = len(items)
            dates, bank = [], 0
            for n in items:
                f = {c.tag.split("}")[-1]: (c.text or "") for c in n}
                d = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
                if d:
                    dates.append(d)
                if re.search(BANK_WORDS, f.get("title", "") + " " + f.get("description", "")):
                    bank += 1
            r0["newest"] = max(dates) if dates else None
            r0["bank_items"] = bank
            r0["ok"] = r0["items"] > 0
        else:
            r0["kind"] = "HTML"
            # صفحه‌هایی که با جاوااسکریپت ساخته می‌شن، متن کمی در HTML اولیه دارن
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", body, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            r0["hits"] = len(re.findall(pattern, text, re.I)) if pattern else 0
            r0["ok"] = r0["hits"] > 0
            if not r0["ok"]:
                r0["note"] = "page loads but content not visible (likely JavaScript-only or blocked)"
            if re.search(r"captcha|access denied|are you a robot|cloudflare", body[:5000], re.I):
                r0["ok"] = False
                r0["note"] = "blocked by bot protection"
    except Exception as e:
        r0["note"] = f"{type(e).__name__}: {str(e)[:120]}"
    return r0


def age_text(d):
    if not d:
        return "—"
    h = (datetime.now(timezone.utc) - d).total_seconds() / 3600
    return f"{h:.0f}h ago" if h < 48 else f"{h / 24:.0f}d ago"


def persian_report(results):
    ok = [r for r in results if r["ok"]]
    bad = [r for r in results if not r["ok"]]
    lines = ["🔎 گزارش تست منابع «دیدگاه بانک‌ها»",
             f"{len(ok)} از {len(results)} منبع از سرور GitHub قابل استفاده‌ست.", ""]
    lines.append("✅ در دسترس:")
    for r in ok:
        if r["kind"] == "RSS":
            extra = f"RSS، {r['items']} مطلب، جدیدترین: {age_text(r['newest'])}"
            if r["bank_items"]:
                extra += f"، {r['bank_items']} مطلب با اسم بانک"
        else:
            extra = f"صفحه‌ی وب، {r['hits']} مورد مرتبط"
        lines.append(f"• {r['name']} — {extra}")
    lines += ["", "❌ در دسترس نیست:"]
    fa = {"page loads but content not visible (likely JavaScript-only or blocked)":
              "صفحه باز می‌شه ولی محتوا دیده نمی‌شه (احتمالاً با جاوااسکریپت ساخته شده)",
          "blocked by bot protection": "سایت جلوی ربات‌ها رو گرفته"}
    for r in bad:
        n = r["note"]
        if n in fa:
            why = fa[n]
        elif n.startswith("HTTP 403"):
            why = "دسترسی رد شد (403)"
        elif n.startswith("HTTP 404"):
            why = "آدرس وجود نداره (404)"
        elif n.startswith("HTTP"):
            why = f"خطای سرور ({n})"
        elif n:
            why = "اتصال برقرار نشد"
        else:
            why = f"HTTP {r.get('status', '?')}"
        lines.append(f"• {r['name']} — {why}")
    lines += ["", "جزئیات کامل در لاگ GitHub Actions هست. این پیام فقط برای شماست و در کانال ارسال نشده."]
    return "\n".join(lines)


def main():
    results = []
    for g, n, u, p in SOURCES:
        res = probe(g, n, u, p)
        results.append(res)
        flag = "✓" if res["ok"] else "✗"
        print(f"{flag} [{g:8}] {n:42} status={res.get('status', '-')} kind={res['kind'] or '-':4} "
              f"items={res['items']} hits={res['hits']} bank_items={res['bank_items']} "
              f"newest={res['newest'].isoformat() if res['newest'] else '-'} {res['note']}")
        time.sleep(1)

    msg = persian_report(results)
    print("\n" + msg)
    if BOT_TOKEN and MY_CHAT_ID:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": MY_CHAT_ID, "text": msg[:4000],
                                "disable_web_page_preview": True}, timeout=20)
        print("Private message:", "sent" if r.json().get("ok") else r.text[:300])
    else:
        print("MY_CHAT_ID not set — report is only in this log.")


if __name__ == "__main__":
    main()
