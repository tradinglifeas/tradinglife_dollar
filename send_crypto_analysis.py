"""
کارت «Crypto Market Update» برای کانال تلگرام (انگلیسی، مخاطب آمریکایی).

همه‌ی عددها از منابع داده میان، نه از هوش مصنوعی:
  - قیمت و تغییر ۲۴ ساعته و ۷ روزه‌ی ۶ کوین: OKX (رایگان، بدون کلید، از GitHub تست‌شده)
  - ارزش کل بازار، دامیننس بیت‌کوین، بیشترین رشد/افت بین ۵۰ کوین برتر: CoinGecko
  - شاخص ترس و طمع: alternative.me
  - جمله‌ی خلاصه‌ی بازار رو خود کد از روی عددها می‌سازه (جهت‌ها هیچ‌وقت اشتباه نمی‌شن)

اخبار: از RSS رسمی CoinDesk، Cointelegraph، Decrypt، The Block و Bloomberg.
  Groq فقط از بین خبرهای واقعی انتخاب و خلاصه می‌کنه و کد چک می‌کنه:
  عدد ساختگی، کپی متن، حذف منبعِ گزارش، حذف بازه‌ی زمانی عدد.
  اگه Groq یا فیدها کار نکنن، کارت بدون بخش اخبار ارسال می‌شه.

اگه قیمت‌های OKX دریافت نشه، هیچ پستی نمی‌ره و اجرا قرمز می‌شه.
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
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
COINGECKO_KEY = os.environ.get("COINGECKO_API_KEY", "")   # اختیاری (کلید Demo رایگان)

ET_TZ = ZoneInfo("America/New_York")
OKX = "https://www.okx.com"
COINGECKO = "https://api.coingecko.com/api/v3"
FNG_URL = "https://api.alternative.me/fng/"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

COINS = [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"),
         ("XRP", "XRP"), ("ADA", "Cardano"), ("DOGE", "Dogecoin")]

NEWS_FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("The Block", "https://www.theblock.co/rss.xml"),
    ("Bloomberg", "https://feeds.bloomberg.com/Crypto/news.rss"),
]
NEWS_MAX = 3
NEWS_CANDIDATES = 30

# استیبل‌کوین‌ها و توکن‌های wrapped/staked در لیست «بیشترین رشد و افت» معنی ندارن
EXCLUDE_WORDS = ("usd", "wrapped", "staked", "bridged", "restaked", "liquid staking", "tether", "dai")
EXCLUDE_SYMBOLS = {"usdt", "usdc", "dai", "usde", "fdusd", "tusd", "pyusd", "usds", "usd1", "busd",
                   "susde", "usdd", "usdtb", "rlusd", "wbtc", "weth", "steth", "wsteth", "weeth",
                   "cbbtc", "lbtc", "reth", "meth", "bsc-usd", "bfusd", "usdf", "gho", "frax",
                   "xaut", "paxg"}

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json, application/rss+xml, application/xml, */*"}


def sf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def pct(a, b):
    return (a / b - 1) * 100 if a is not None and b not in (None, 0) else None


def get_json(url, params=None, headers=None, timeout=20):
    h = dict(HEADERS)
    if headers:
        h.update(headers)
    r = requests.get(url, params=params, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ================================================================ داده‌های بازار

def okx_coin(sym):
    inst = f"{sym}-USDT"
    t = get_json(f"{OKX}/api/v5/market/ticker", {"instId": inst})["data"][0]
    last, open24 = sf(t.get("last")), sf(t.get("open24h"))
    candles = get_json(f"{OKX}/api/v5/market/candles",
                       {"instId": inst, "bar": "1Dutc", "limit": 9})["data"]
    closes = [sf(c[4]) for c in reversed(candles)]          # قدیم ← جدید
    closes = [c for c in closes if c is not None]
    week_ago = closes[-8] if len(closes) >= 8 else None
    if last is None:
        raise ValueError("no last price")
    return {"price": last, "ch24": pct(last, open24), "ch7": pct(last, week_ago),
            "spark": closes[-8:-1] + [last]}


def get_coins():
    out = {}
    for sym, _ in COINS:
        try:
            out[sym] = okx_coin(sym)
            print(f"✓ OKX {sym}: {out[sym]['price']}  24h {out[sym]['ch24']}")
        except Exception as e:
            print(f"✗ OKX {sym}: {e}")
        time.sleep(0.25)
    if len(out) < 4:
        raise SystemExit(f"قیمت فقط برای {len(out)} کوین دریافت شد. پستی ارسال نشد.")
    return out


def cg_headers():
    return {"x-cg-demo-api-key": COINGECKO_KEY} if COINGECKO_KEY else None


def get_global():
    try:
        d = get_json(f"{COINGECKO}/global", headers=cg_headers())["data"]
        g = {"mcap": sf(d["total_market_cap"]["usd"]),
             "mcap_ch": sf(d.get("market_cap_change_percentage_24h_usd")),
             "btc_dom": sf(d["market_cap_percentage"]["btc"])}
        print(f"✓ CoinGecko global: {g}")
        return g
    except Exception as e:
        print(f"✗ CoinGecko global: {e}")
        return None


def get_movers():
    try:
        rows = get_json(f"{COINGECKO}/coins/markets",
                        {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 100,
                         "page": 1, "price_change_percentage": "24h"}, headers=cg_headers())
    except Exception as e:
        print(f"✗ CoinGecko markets: {e}")
        return None
    # تغییر ۲۴ ساعته‌ی ارزش کل بازار رو خودمون از ۱۰۰ کوین برتر حساب می‌کنیم
    # (عدد آماده‌ی CoinGecko گاهی با حرکت واقعی بازار نمی‌خونه)
    tot = sum(sf(r.get("market_cap")) or 0 for r in rows)
    chg = sum(sf(r.get("market_cap_change_24h")) or 0 for r in rows)
    agg = chg / (tot - chg) * 100 if tot and tot != chg else None
    clean = []
    for r in rows:
        sym, name = (r.get("symbol") or "").lower(), (r.get("name") or "").lower()
        ch = sf(r.get("price_change_percentage_24h"))
        if ch is None or sym in EXCLUDE_SYMBOLS or any(w in name for w in EXCLUDE_WORDS):
            continue
        clean.append({"sym": sym.upper(), "ch": ch})
        if len(clean) == 50:
            break
    if len(clean) < 20:
        print(f"✗ CoinGecko markets: only {len(clean)} usable coins")
        return None
    s = sorted(clean, key=lambda x: x["ch"], reverse=True)
    print(f"✓ CoinGecko movers from top {len(clean)}")
    return {"up": s[:3], "down": s[-3:][::-1], "n": len(clean), "mcap_ch_top100": agg}


def get_fng():
    try:
        d = get_json(FNG_URL, {"limit": 2})["data"]
        f = {"value": int(d[0]["value"]), "label": d[0]["value_classification"],
             "prev": int(d[1]["value"]) if len(d) > 1 else None}
        print(f"✓ Fear & Greed: {f}")
        return f
    except Exception as e:
        print(f"✗ Fear & Greed: {e}")
        return None


def summary_sentence(coins, glob):
    """جمله‌ی خلاصه فقط از روی عددها — بدون هوش مصنوعی."""
    parts = []
    btc = coins.get("BTC")
    if btc and btc["ch24"] is not None:
        verb = "rose" if btc["ch24"] > 0.05 else "fell" if btc["ch24"] < -0.05 else "was little changed"
        parts.append(f"Bitcoin {verb}" + (f" {abs(btc['ch24']):.1f}%" if verb != "was little changed" else "")
                     + " over the past 24 hours")
    tracked = [c for c in coins.values() if c["ch24"] is not None]
    up = sum(1 for c in tracked if c["ch24"] > 0)
    down = sum(1 for c in tracked if c["ch24"] < 0)
    parts.append(f"{up} of {len(tracked)} tracked coins were higher and {down} lower")
    s = "; ".join(parts) + "."
    if glob and glob["mcap_ch"] is not None:
        d = "up" if glob["mcap_ch"] >= 0 else "down"
        s += f" Total crypto market value was {d} {abs(glob['mcap_ch']):.1f}% on the day."
    return s


# ================================================================ اخبار (همون روش Key Market News)

def strip_tags(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


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


def fetch_feed(source, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"  ✗ {source}: {e}")
        return []
    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: (c.text or "") for c in node}
        link = (f.get("link") or "").strip()
        title = strip_tags(f.get("title", ""))
        low = (link + " " + title).lower()
        if any(k in low for k in ("/video", "/videos/", "/opinion/", "podcast", "sponsored",
                                  "press-release", "/learn/", "price-prediction", "price prediction")):
            continue
        when = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        desc = strip_tags(f.get("description") or f.get("summary") or "")[:300]
        if title and when:
            items.append({"source": source, "title": title, "desc": desc, "time": when})
    print(f"  ✓ {source}: {len(items)} items")
    return items


def collect_news():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    print("دریافت فیدهای خبری:")
    allit = []
    for s, u in NEWS_FEEDS:
        allit += fetch_feed(s, u)
    fresh, seen = [], set()
    for it in sorted(allit, key=lambda x: x["time"], reverse=True):
        if not (cutoff <= it["time"] <= now + timedelta(minutes=10)):
            continue
        k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        fresh.append(it)
    print(f"خبرهای ۲۴ ساعت اخیر: {len(fresh)}")
    return fresh[:NEWS_CANDIDATES]


def numbers_in(t):
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", t)}


def common_run(a, b):
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    best = 0
    for i in range(len(wa)):
        for j in range(len(wb)):
            k = 0
            while i + k < len(wa) and j + k < len(wb) and wa[i + k] == wb[j + k]:
                k += 1
            best = max(best, k)
    return best


def validate(selection, cands):
    ok = []
    for s in selection:
        try:
            idx = int(s.get("id"))
            label = str(s.get("label", "")).strip().rstrip(":")
            summ = re.sub(r"[【〖][^】〗]*[】〗]", "", str(s.get("summary", ""))).strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= idx <= len(cands) or idx in [o["id"] for o in ok]:
            continue
        it = cands[idx - 1]
        src = it["title"] + " " + it["desc"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 &/.\-]{0,17}", label):
            label = "Crypto"
        if not 30 <= len(summ) <= 230:
            print(f"  رد شد #{idx}: طول"); continue
        extra = numbers_in(summ) - numbers_in(src)
        if extra:
            print(f"  رد شد #{idx}: عدد ساختگی {extra}"); continue
        if max(common_run(summ, it["title"]), common_run(summ, it["desc"])) >= 9:
            print(f"  رد شد #{idx}: کپی متن"); continue
        if re.search(r"\b(week|weekly|month|monthly|quarter|year[- ]to[- ]date|ytd)\b", src, re.I) \
                and re.search(r"\d\s*%|percent", summ, re.I) \
                and not re.search(r"\b(week|weekly|month|monthly|quarter|year|ytd)\b", summ, re.I):
            print(f"  رد شد #{idx}: بازه‌ی زمانی حذف شده"); continue
        if re.search(r"\b(reports?|reported|according to|citing|sources say|people familiar)\b", src, re.I) \
                and not re.search(r"\b(report|reported|reports|according|citing|said|says|told)\b", summ, re.I):
            print(f"  رد شد #{idx}: منبعِ گزارش حذف شده"); continue
        ok.append({"id": idx, "label": label, "summary": summ, "source": it["source"]})
        print(f"  قبول شد #{idx} [{it['source']}] {label}: {summ}")
    return ok[:NEWS_MAX]


def call_groq(payload):
    h = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for _ in range(3):
        try:
            data = requests.post(GROQ_URL, json=payload, headers=h, timeout=90).json()
        except Exception as e:
            print(f"خطا در Groq: {e}")
            return None
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "rate_limit_exceeded":
            m = re.search(r"try again in ([\d.]+)s", err.get("message", ""))
            time.sleep(min(float(m.group(1)) + 2 if m else 15, 30))
            continue
        if err:
            print(f"خطای Groq: {err}")
            return None
        return data
    return None


def select_news(cands):
    if not GROQ_API_KEY or len(cands) < 2:
        return []
    listing = "\n".join(f"[{i}] ({c['source']}, {c['time'].strftime('%b %d %H:%M UTC')}) {c['title']}"
                        + (f" — {c['desc'][:200]}" if c["desc"] else "")
                        for i, c in enumerate(cands, 1))
    prompt = (
        "Below is a numbered list of real, recent crypto news items.\n"
        f"Select the {NEWS_MAX} items that matter MOST to crypto markets overall (ETF flows, regulation "
        "and SEC actions, major exchange or protocol events, large liquidations, institutional adoption, "
        "macro events affecting crypto). Prefer concrete events over opinion, price predictions, "
        "or single small-token stories. Skip duplicates.\n"
        "For each, write ONE neutral sentence (max 30 words).\n"
        "STRICT RULES:\n"
        "- Use ONLY information stated in that item's title/description; add no facts, causes, numbers "
        "or predictions.\n"
        "- Paraphrase; do not copy the headline or description.\n"
        "- If the item is a report or attributed claim, keep the attribution. Never attribute price "
        "moves to a person.\n"
        "- Keep time qualifiers attached to numbers (e.g. 'for the week').\n"
        "- label = 1-2 word topic, e.g. ETFs, Regulation, Bitcoin, Ethereum, Exchanges, DeFi, Macro.\n"
        'Return ONLY JSON: {"items":[{"id":<number>,"label":"<topic>","summary":"<sentence>"}]}\n\n'
        f"NEWS ITEMS:\n{listing}")
    for model in GROQ_MODELS:
        for effort in ("low", "medium"):
            data = call_groq({"model": model, "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0.2, "max_tokens": 2500, "reasoning_effort": effort})
            if not data:
                continue
            try:
                raw = data["choices"][0]["message"].get("content") or ""
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                sel = json.loads(m.group(0))["items"] if m else []
            except Exception as e:
                print(f"خروجی Groq قابل خواندن نبود: {e}")
                continue
            if not sel:
                continue
            print(f"Groq ({model}/{effort}) انتخاب کرد. اعتبارسنجی:")
            v = validate(sel, cands)
            if len(v) >= 2:
                return v
    return []


# ================================================================ کارت تصویری

W = 1080
BG = (11, 14, 20); PANEL = (24, 29, 39); PANEL2 = (18, 22, 31); LINE = (38, 44, 56)
TEXT = (240, 244, 248); MUTED = (155, 165, 178); GREEN = (44, 200, 134); RED = (239, 83, 80)
AMBER = (245, 166, 35); BLUE = (91, 155, 255); ORANGE = (247, 147, 26)


def font(n, b=False):
    p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if b else ''}.ttf"
    return ImageFont.truetype(p, n) if os.path.exists(p) else ImageFont.load_default(size=n)


F38B, F30B, F26B, F24B, F22B, F18B = font(38, 1), font(30, 1), font(26, 1), font(24, 1), font(22, 1), font(18, 1)
F24, F22, F21, F19, F18 = font(24), font(22), font(21), font(19), font(18)


def col(v):
    return MUTED if v is None else GREEN if v > 0 else RED if v < 0 else MUTED


def fmt_pct(v):
    return "—" if v is None else f"{v:+.2f}%"


def fmt_price(v):
    return f"${v:,.2f}" if v >= 100 else f"${v:,.4f}" if v >= 1 else f"${v:,.5f}"


def fmt_big(v):
    return f"${v / 1e12:.2f}T" if v >= 1e12 else f"${v / 1e9:.0f}B"


def right(d, x, y, t, f, fill):
    d.text((x - d.textlength(t, font=f), y), t, font=f, fill=fill)


def rr(d, xy, fill, r=18, outline=None, w=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    return lines + [cur] if cur else lines


def spark(d, box, vals, color):
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return
    x1, y1, x2, y2 = box
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pts = [(x1 + (x2 - x1) * i / (len(vals) - 1), y2 - (y2 - y1) * (v - lo) / span) for i, v in enumerate(vals)]
    d.line(pts, fill=color, width=3, joint="curve")


def fng_color(v):
    return RED if v <= 25 else AMBER if v <= 45 else MUTED if v <= 55 else (130, 210, 110) if v <= 75 else GREEN


def render(coins, glob, movers, fng, summary, news, now):
    im = Image.new("RGB", (W, 3200), BG)
    d = ImageDraw.Draw(im)

    d.text((55, 42), "TRADINGLIFE", font=F38B, fill=TEXT)
    d.text((55, 91), "Crypto Market Update", font=F21, fill=MUTED)
    right(d, 1025, 48, "CRYPTO", F30B, ORANGE)
    right(d, 1025, 92, now.strftime("%a, %b %d, %Y"), F22, TEXT)
    y = 145

    # --- نمای کلی بازار
    rr(d, (50, y, 1030, y + 150), PANEL)
    cells = [(80, "TOTAL MARKET CAP"), (410, "BTC DOMINANCE"), (720, "FEAR & GREED")]
    for x, t in cells:
        d.text((x, y + 24), t, font=F18B, fill=MUTED)
    if glob:
        d.text((80, y + 56), fmt_big(glob["mcap"]), font=F30B, fill=TEXT)
        d.text((80, y + 100), f"{fmt_pct(glob['mcap_ch'])} 24h", font=F21, fill=col(glob["mcap_ch"]))
        d.text((410, y + 56), f"{glob['btc_dom']:.1f}%", font=F30B, fill=TEXT)
    else:
        d.text((80, y + 60), "N/A", font=F30B, fill=MUTED)
        d.text((410, y + 60), "N/A", font=F30B, fill=MUTED)
    if fng:
        c = fng_color(fng["value"])
        d.text((720, y + 56), f"{fng['value']}", font=F30B, fill=c)
        d.text((720 + d.textlength(f"{fng['value']} ", font=F30B), y + 63), fng["label"], font=F22B, fill=c)
        if fng["prev"] is not None:
            d.text((720, y + 100), f"Yesterday: {fng['prev']}", font=F21, fill=MUTED)
    else:
        d.text((720, y + 60), "N/A", font=F30B, fill=MUTED)
    y += 175

    # --- جدول کوین‌ها
    rr(d, (50, y, 1030, y + 46), PANEL2, r=12)
    d.text((78, y + 13), "COIN", font=F18B, fill=MUTED)
    right(d, 560, y + 13, "PRICE", F18B, MUTED)
    right(d, 700, y + 13, "24H", F18B, MUTED)
    right(d, 830, y + 13, "7D", F18B, MUTED)
    right(d, 1005, y + 13, "7-DAY TREND", F18B, MUTED)
    y += 58
    rr(d, (50, y, 1030, y + 12 + 64 * len(COINS)), PANEL)
    y += 6
    for i, (sym, name) in enumerate(COINS):
        c = coins.get(sym)
        if i:
            d.line((75, y, 1005, y), fill=LINE)
        d.text((78, y + 10), sym, font=F24B, fill=TEXT)
        d.text((78 + d.textlength(sym + "  ", font=F24B), y + 14), name, font=F19, fill=MUTED)
        if c:
            right(d, 560, y + 12, fmt_price(c["price"]), F24, TEXT)
            right(d, 700, y + 14, fmt_pct(c["ch24"]), F22B, col(c["ch24"]))
            right(d, 830, y + 14, fmt_pct(c["ch7"]), F22, col(c["ch7"]))
            spark(d, (870, y + 14, 1005, y + 46), c["spark"], col(c["ch7"]))
        else:
            right(d, 560, y + 12, "N/A", F24, MUTED)
        y += 64
    y += 30

    # --- بیشترین رشد و افت
    if movers:
        rr(d, (50, y, 1030, y + 175), PANEL)
        d.text((78, y + 20), f"TOP GAINERS · 24H", font=F18B, fill=GREEN)
        d.text((560, y + 20), f"TOP LOSERS · 24H", font=F18B, fill=RED)
        for j in range(3):
            yy = y + 58 + j * 36
            if j < len(movers["up"]):
                m = movers["up"][j]
                d.text((78, yy), m["sym"], font=F22B, fill=TEXT)
                right(d, 470, yy, fmt_pct(m["ch"]), F22, GREEN if m["ch"] > 0 else RED)
            if j < len(movers["down"]):
                m = movers["down"][j]
                d.text((560, yy), m["sym"], font=F22B, fill=TEXT)
                right(d, 1000, yy, fmt_pct(m["ch"]), F22, GREEN if m["ch"] > 0 else RED)
        d.line((530, y + 20, 530, y + 155), fill=LINE, width=1)
        y += 175 + 8
        d.text((60, y), f"Among the top {movers['n']} coins by market cap (excl. stablecoins & wrapped tokens)",
               font=F18, fill=MUTED)
        y += 40

    # --- خلاصه
    lines = wrap(d, summary, F22, 910)
    h = 70 + 32 * len(lines)
    rr(d, (50, y, 1030, y + h), PANEL2)
    d.text((78, y + 20), "MARKET SUMMARY", font=F18B, fill=BLUE)
    for k, ln in enumerate(lines):
        d.text((78, y + 52 + k * 32), ln, font=F22, fill=TEXT)
    y += h + 22

    # --- اخبار
    if news:
        blocks = []
        for n in news:
            head = f"{n['label']}: "
            body = wrap(d, head + n["summary"] + f"  ({n['source']})", F21, 900)
            blocks.append((head, body))
        h = 64 + sum(30 * len(b) + 18 for _, b in blocks)
        rr(d, (50, y, 1030, y + h), PANEL)
        d.text((78, y + 20), "KEY CRYPTO NEWS", font=F18B, fill=BLUE)
        yy = y + 56
        for head, body in blocks:
            d.ellipse((66, yy + 9, 74, yy + 17), fill=ORANGE)
            for k, ln in enumerate(body):
                if k == 0 and ln.startswith(head):
                    d.text((82, yy), head, font=F22B, fill=TEXT)
                    d.text((82 + d.textlength(head, font=F22B), yy + 1), ln[len(head):], font=F21, fill=TEXT)
                else:
                    d.text((82, yy + 1), ln, font=F21, fill=TEXT)
                yy += 30
            yy += 18
        y += h + 22

    # --- فوتر
    rr(d, (50, y, 1030, y + 120), PANEL2)
    d.text((75, y + 20), f"TradingLife  •  Updated {now.strftime('%Y-%m-%d %H:%M')} ET", font=F18, fill=TEXT)
    d.text((75, y + 52), "Prices: OKX (USDT pairs) • Market data: CoinGecko • Sentiment: alternative.me",
           font=F18, fill=MUTED)
    d.text((75, y + 80), "24H = rolling 24 hours • News summarized from named sources • Not investment advice",
           font=F18, fill=MUTED)
    y += 120 + 90   # فضای خالی پایین برای حباب ساعت تلگرام

    im = im.crop((0, 0, W, y))
    os.makedirs("crypto_cards", exist_ok=True)
    p = "crypto_cards/crypto_update.png"
    im.save(p, "PNG", optimize=True)
    return p


def send_photo(path):
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          data={"chat_id": CHAT_ID}, files={"photo": f}, timeout=60)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال: {res}")
        raise SystemExit(1)
    print("کارت کریپتو ارسال شد.")


def main():
    now = datetime.now(ET_TZ)
    coins = get_coins()
    glob = get_global()
    movers = get_movers()
    fng = get_fng()
    if glob and movers and movers.get("mcap_ch_top100") is not None:
        print(f"تغییر ارزش بازار: CoinGecko {glob['mcap_ch']} → محاسبه‌ی ۱۰۰ کوین برتر {movers['mcap_ch_top100']:.2f}")
        glob["mcap_ch"] = movers["mcap_ch_top100"]
    elif glob:
        glob["mcap_ch"] = None   # عدد تأییدنشده نمایش داده نمی‌شه
    summary = summary_sentence(coins, glob)
    print(f"خلاصه: {summary}")
    news = select_news(collect_news())
    if not news:
        print("بخش اخبار حذف شد (خبر معتبر کافی نبود).")
    send_photo(render(coins, glob, movers, fng, summary, news, now))


if __name__ == "__main__":
    main()
