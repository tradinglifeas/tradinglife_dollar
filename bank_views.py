"""
«دیدگاه بانک‌ها» — گزارش خصوصی فارسی (فقط برای چت خصوصی شما، نه کانال).

دو حالت (متغیر MODE):
  MODE=alerts : هر ۳۰ دقیقه در روزهای کاری. اگه بانکی پیش‌بینی جدید منتشر کنه یا خبرها
                تغییر هدف قیمت یک بانک رو گزارش کنن، فوراً یک هشدار فارسی ارسال می‌شه.
                تحلیل‌های روزانه‌ی معمولی هشدار نمی‌گیرن؛ برای خلاصه‌ی هفتگی ذخیره می‌شن.
  MODE=weekly : جمعه عصر. خلاصه‌ی فارسی دیدگاه بانک‌ها در اون هفته، در سه بخش:
                دلار و ارزها · طلا و نقره · بازار سهام آمریکا

منابع (همه در تست دسترسی از GitHub تأیید شدن):
  ING (RSS مستقیم) · Danske (از طریق ActionForex) · FXStreet (تحلیل بانک‌ها)
  · CNBC، MarketWatch، Bloomberg (گزارش خبری پیش‌بینی بانک‌های آمریکایی)

ایمنی:
  - Groq فقط از متن خود گزارش‌ها خلاصه می‌نویسه. هر عددی که در خلاصه بیاد باید در متن
    منبع باشه، وگرنه خلاصه رد می‌شه و به‌جاش عنوان اصلی گزارش با لینک فرستاده می‌شه.
  - اولین اجرا هیچ هشداری نمی‌فرسته؛ فقط گزارش‌های موجود رو «دیده‌شده» ثبت می‌کنه.
  - حافظه در فایل bank_views_state.json ذخیره می‌شه تا هیچ گزارشی دو بار ارسال نشه.
"""

import os
import re
import json
import html
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
MY_CHAT_ID = os.environ["MY_CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MODE = os.environ.get("MODE", "alerts").lower()

STATE_FILE = "bank_views_state.json"
ET_TZ = ZoneInfo("America/New_York")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]
KEEP_DAYS = 10

# (اسم منبع، آدرس، بانک پیش‌فرض اگه متن اسم بانک نداشت)
FEEDS = [
    ("ING THINK", "https://think.ing.com/rss/", "ING"),
    ("ActionForex (Danske)", "https://www.actionforex.com/author/danske/feed/", "Danske Bank"),
    ("FXStreet", "https://www.fxstreet.com/rss/analysis", None),
    ("FXStreet", "https://www.fxstreet.com/rss/news", None),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html", None),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", None),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", None),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss", None),
]

BANKS = [
    (r"Goldman", "Goldman Sachs"), (r"J\.?P\.? ?Morgan|JPMorgan", "JPMorgan"),
    (r"Morgan Stanley", "Morgan Stanley"), (r"Bank of America|BofA", "Bank of America"),
    (r"\bCiti(group|bank)?\b", "Citi"), (r"Wells Fargo", "Wells Fargo"), (r"\bUBS\b", "UBS"),
    (r"\bING\b", "ING"), (r"Danske", "Danske Bank"), (r"Commerzbank", "Commerzbank"),
    (r"\bMUFG\b", "MUFG"), (r"Scotiabank", "Scotiabank"), (r"\bBBH\b|Brown Brothers", "BBH"),
    (r"Soci[eé]t[eé] G[eé]n[eé]rale|\bSocGen\b", "Société Générale"), (r"Rabobank", "Rabobank"),
    (r"\bUOB\b", "UOB"), (r"Deutsche Bank", "Deutsche Bank"), (r"Barclays", "Barclays"),
    (r"\bHSBC\b", "HSBC"), (r"\bBNP\b", "BNP Paribas"),
]

ASSETS = [
    ("metals", r"\bgold\b|\bsilver\b|bullion|precious metal|XAU|XAG"),
    ("fx", r"dollar|\bUSD\b|EUR/USD|USD/JPY|\bFX\b|currenc|\byen\b|\beuro\b|sterling|\bDXY\b"),
    ("stocks", r"S&P|\bstocks?\b|equit|Nasdaq|\bDow\b|earnings"),
]
ASSET_FA = {"fx": "💵 دلار و ارزها", "metals": "🥇 طلا و نقره", "stocks": "📈 بازار سهام آمریکا"}

# چه چیزی «هشدار فوری» می‌گیره: پیش‌بینی/هدف قیمت جدید یا تغییرش
FORECAST_RE = re.compile(
    r"forecast update|FX Talking|\bforecasts?\b|price target|year-end target|\btarget\b|"
    r"raises?|lifts?|cuts?|lowers?|upgrades?|downgrades?|revis|outlook for 20\d\d|sees .{0,30}\$?\d", re.I)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, */*"}

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫", "01234567890123456789.")


# ================================================================ حافظه

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def save_state(st):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat()
    st["seen"] = {k: v for k, v in st["seen"].items() if v >= cutoff}
    st["week"] = [w for w in st["week"] if w["time"] >= cutoff]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


# ================================================================ جمع‌آوری

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


def detect_bank(text, default):
    for pat, name in BANKS:
        if re.search(pat, text):
            return name
    return default


def detect_assets(text):
    return [k for k, pat in ASSETS if re.search(pat, text, re.I)]


def fetch_feed(source, url, default_bank):
    try:
        r = requests.get(url, headers=UA, timeout=25)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"  ✗ {source}: {url} -> {e}")
        return []
    out = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: (c.text or "") for c in node}
        link = (f.get("link") or "").strip()
        if not link:
            for c in node:
                if c.tag.split("}")[-1] == "link" and c.get("href"):
                    link = c.get("href"); break
        title = strip_tags(f.get("title", ""))
        desc = strip_tags(f.get("description") or f.get("summary") or f.get("encoded") or "")[:700]
        author = strip_tags(f.get("creator") or f.get("author") or "")
        when = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        if not title or not when:
            continue
        blob = f"{title} {desc} {author}"
        bank = detect_bank(blob, default_bank)
        if not bank:
            continue                                   # فقط مطالبی که به یک بانک مربوطن
        assets = detect_assets(f"{title} {desc}")
        if not assets:
            continue                                   # فقط دلار/ارز، طلا/نقره، سهام
        uid = hashlib.sha1((link or title).encode()).hexdigest()[:16]
        out.append({"id": uid, "source": source, "bank": bank, "title": title, "desc": desc,
                    "link": link, "time": when.isoformat(), "assets": assets,
                    "forecast": bool(FORECAST_RE.search(title + " " + desc[:300]))})
    print(f"  ✓ {source}: {len(out)} bank items ({url})")
    return out


def collect():
    print("دریافت منابع:")
    items, seen = [], set()
    for s, u, b in FEEDS:
        for it in fetch_feed(s, u, b):
            if it["id"] not in seen:
                seen.add(it["id"]); items.append(it)
        time.sleep(0.5)
    items.sort(key=lambda x: x["time"])
    return items


# ================================================================ خلاصه‌ی فارسی با Groq (با چک عددها)

def numbers_in(t):
    t = (t or "").translate(FA_DIGITS)
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", t)}


def wrong_bank(text, allowed):
    """اگه خلاصه اسم بانکی رو بیاره که جزو منابع ارجاع‌شده نیست، نسبت دادن اشتباهه."""
    for pat, name in BANKS:
        if re.search(pat, text) and name not in allowed:
            return name
    return None


def call_groq(prompt, max_tokens=2500):
    if not GROQ_API_KEY:
        return None
    h = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        for _ in range(2):
            try:
                d = requests.post(GROQ_URL, headers=h, timeout=90, json={
                    "model": model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2, "max_tokens": max_tokens, "reasoning_effort": "medium"}).json()
            except Exception as e:
                print("Groq error:", e); break
            err = d.get("error") if isinstance(d, dict) else None
            if err and err.get("code") == "rate_limit_exceeded":
                time.sleep(20); continue
            if err:
                print("Groq error:", err); break
            raw = (d["choices"][0]["message"].get("content") or "").strip()
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    pass
            print(f"Groq ({model}) returned no JSON"); break
    return None


RULES_FA = (
    "قوانین سخت‌گیرانه:\n"
    "- فقط از اطلاعاتی استفاده کن که در عنوان/توضیح همون مطلب آمده؛ هیچ عدد، دلیل یا پیش‌بینی‌ای اضافه نکن.\n"
    "- همه‌ی عددها رو دقیقاً همون‌طور که در متن هست و با ارقام لاتین بنویس (مثلاً 1.12 یا 4,500).\n"
    "- اسم بانک‌ها و نمادها رو به انگلیسی بنویس (مثلاً Goldman Sachs، EUR/USD، S&P 500).\n"
    "- اگه مطلب گزارش رسانه‌ای درباره‌ی نظر یک بانکه، با «به گزارش …» یا «به گفته‌ی …» بیان کن.\n"
    "- لحن بی‌طرف؛ هیچ توصیه‌ی خرید یا فروش نده.\n"
    "- اگه جهت دیدگاه روشنه، به زبان ساده توضیح بده یعنی چی (مثلاً «یعنی انتظار تقویت دلار»)، "
    "فقط اگه از خود متن برمیاد.\n")


def summarize_alerts(items):
    listing = "\n".join(f"[{i}] ({it['bank']} · {it['source']}) {it['title']} — {it['desc'][:500]}"
                        for i, it in enumerate(items, 1))
    prompt = ("برای هر مطلب زیر یک خلاصه‌ی فارسی ۱ تا ۲ جمله‌ای (حداکثر ۵۵ کلمه) بنویس که بگه "
              "این بانک چه پیش‌بینی یا دیدگاهی داده.\n" + RULES_FA +
              'فقط JSON برگردون: {"items":[{"id":<شماره>,"summary_fa":"<خلاصه>"}]}\n\nمطالب:\n' + listing)
    res = call_groq(prompt) or {}
    out = {}
    for x in res.get("items", []):
        try:
            i = int(x["id"]); s = str(x["summary_fa"]).strip()
        except (KeyError, ValueError, TypeError):
            continue
        if not 1 <= i <= len(items) or len(s) < 15:
            continue
        src = items[i - 1]["title"] + " " + items[i - 1]["desc"]
        extra = numbers_in(s) - numbers_in(src)
        if extra:
            print(f"  رد شد #{i}: عدد ساختگی {extra}"); continue
        wb = wrong_bank(s, {items[i - 1]["bank"]} | {n for p_, n in BANKS if re.search(p_, src)})
        if wb:
            print(f"  رد شد #{i}: نسبت دادن به بانک اشتباه ({wb})"); continue
        out[i] = s
    return out


def summarize_week(items):
    groups = {}
    for k in ("fx", "metals", "stocks"):
        g = [it for it in items if k in it["assets"]]
        if g:
            groups[k] = g[-25:]
    flat, idx = [], {}
    for k, g in groups.items():
        for it in g:
            if it["id"] not in idx:
                flat.append(it); idx[it["id"]] = len(flat)
    if not flat:
        return {}
    listing = "\n".join(f"[{i}] ({it['bank']} · {datetime.fromisoformat(it['time']).strftime('%b %d')}) "
                        f"{it['title']} — {it['desc'][:350]}" for i, it in enumerate(flat, 1))
    prompt = ("این مطالب دیدگاه بانک‌ها در هفته‌ی گذشته‌ست. برای هر بخش (fx = دلار و ارزها، "
              "metals = طلا و نقره، stocks = سهام آمریکا) ۲ تا ۴ نکته‌ی فارسی بنویس که مهم‌ترین "
              "دیدگاه‌ها رو خلاصه کنه. هر نکته حداکثر ۴۵ کلمه، و شماره‌ی مطالبی که ازشون استفاده کردی رو بده.\n"
              + RULES_FA +
              'فقط JSON: {"groups":[{"asset":"fx|metals|stocks","bullets":[{"text_fa":"...","ids":[1,2]}]}]}\n\n'
              "مطالب:\n" + listing)
    res = call_groq(prompt, max_tokens=3500) or {}
    out = {}
    for g in res.get("groups", []):
        k = g.get("asset")
        if k not in groups:
            continue
        for b in g.get("bullets", []):
            try:
                ids = [int(i) for i in b.get("ids", []) if 1 <= int(i) <= len(flat)]
                t = str(b["text_fa"]).strip()
            except (KeyError, ValueError, TypeError):
                continue
            if not ids or len(t) < 15:
                continue
            src = " ".join(flat[i - 1]["title"] + " " + flat[i - 1]["desc"] for i in ids)
            if numbers_in(t) - numbers_in(src):
                print(f"  رد شد (عدد ساختگی): {t[:60]}"); continue
            banks = sorted({flat[i - 1]["bank"] for i in ids})
            wb = wrong_bank(t, set(banks))
            if wb:
                print(f"  رد شد (بانک {wb} جزو منابع این نکته نیست): {t[:60]}"); continue
            out.setdefault(k, []).append((t, banks))
    return out


# ================================================================ کارت تصویری فارسی (خلاصه‌ی هفتگی)
# روش تأییدشده‌ی پروژه: موتور raqm خود Pillow + فونت Vazirmatn (نه arabic_reshaper که بعضی اتصال‌ها رو خراب می‌کنه)

FONT_URL = "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/Vazirmatn-{w}.ttf"
SECTION_COL = {"fx": (44, 200, 134), "metals": (232, 185, 60), "stocks": (91, 155, 255)}
SECTION_TITLE = {"fx": "دلار و ارزها", "metals": "طلا و نقره", "stocks": "بازار سهام آمریکا"}


def load_fa_fonts():
    from PIL import ImageFont
    paths = {}
    for w in ("Regular", "Bold"):
        path = f"/tmp/Vazirmatn-{w}.ttf"
        if not os.path.exists(path):
            r = requests.get(FONT_URL.format(w=w), timeout=30)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        paths[w] = path
    L = ImageFont.Layout.RAQM
    mk = lambda w, n: ImageFont.truetype(paths[w], n, layout_engine=L)
    return {"title": mk("Bold", 46), "sub": mk("Regular", 24), "sec": mk("Bold", 30),
            "body": mk("Regular", 25), "small": mk("Regular", 20), "brand": mk("Bold", 26)}


def card_weekly(out, pool, today):
    """کارت فارسی راست‌به‌چپ؛ اگه فونت/موتور در دسترس نبود، None برمی‌گردونه (و نسخه‌ی متنی می‌ره)."""
    try:
        from PIL import Image, ImageDraw, features
        if not features.check("raqm"):
            print("raqm not available - text fallback"); return None
        F = load_fa_fonts()
    except Exception as e:
        print("Persian card unavailable:", e); return None

    W, R, Lx = 1080, 1025, 55                  # عرض کارت، لبه‌ی راست متن، لبه‌ی چپ
    BG, PANEL, TEXT, MUTED = (11, 14, 20), (24, 29, 39), (240, 244, 248), (155, 165, 178)
    im = Image.new("RGB", (W, 4000), BG)
    d = ImageDraw.Draw(im)

    def rtl(x, y, t, f, fill):
        d.text((x, y), t, font=f, fill=fill, direction="rtl", anchor="ra")

    def wrap_fa(t, f, width):
        lines, cur = [], ""
        for w in t.split():
            c = (cur + " " + w).strip()
            if d.textlength(c, font=f, direction="rtl") <= width:
                cur = c
            else:
                lines.append(cur); cur = w
        return lines + ([cur] if cur else [])

    d.text((Lx, 48), "TRADINGLIFE", font=F["brand"], fill=TEXT)
    rtl(R, 38, "دیدگاه بانک‌ها", F["title"], TEXT)
    rtl(R, 104, f"خلاصه‌ی هفتگی · هفته‌ی منتهی به {today.strftime('%b %d, %Y')}", F["sub"], MUTED)
    y = 165

    for k in ("fx", "metals", "stocks"):
        g = [it for it in pool if k in it["assets"]]
        if not g:
            continue
        # بدون خلاصه‌ی معتبر: عنوان اصلی انگلیسی (بدون گیومه‌ی فارسی که در متن چپ‌به‌راست به‌هم می‌ریزه)
        bullets = out.get(k) or [(f"عنوان گزارش: {it['title']}", [it["bank"]]) for it in g[-4:]]
        blocks = []
        for t, banks in bullets[:4]:
            blocks.append((wrap_fa(t, F["body"], 870), "، ".join(banks)))
        h = 80 + sum(40 * len(b) + 44 for b, _ in blocks)
        d.rounded_rectangle((Lx - 5, y, R + 5, y + h), radius=18, fill=PANEL)
        d.rectangle((R - 1, y + 16, R + 5, y + 58), fill=SECTION_COL[k])
        rtl(R - 22, y + 20, SECTION_TITLE[k], F["sec"], SECTION_COL[k])
        yy = y + 78
        for lines, banks in blocks:
            d.ellipse((R - 34, yy + 14, R - 24, yy + 24), fill=SECTION_COL[k])
            for ln in lines:
                rtl(R - 46, yy, ln, F["body"], TEXT)
                yy += 40
            rtl(R - 46, yy + 2, f"منبع: {banks}", F["small"], MUTED)
            yy += 44
        y += h + 22

    banks = "، ".join(sorted({it["bank"] for it in pool}))
    d.rounded_rectangle((Lx - 5, y, R + 5, y + 112), radius=18, fill=(18, 22, 31))
    rtl(R - 22, y + 20, f"بانک‌های این هفته: {banks}", F["small"], TEXT)
    rtl(R - 22, y + 58, "خلاصه از متن گزارش‌ها و خبرهای منتشرشده · توصیه‌ی سرمایه‌گذاری نیست", F["small"], MUTED)
    y += 112 + 30
    im = im.crop((0, 0, W, y))
    path = "/tmp/bank_views_weekly.png"
    im.save(path, "PNG", optimize=True)
    return path


def send_photo(path, caption):
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          data={"chat_id": MY_CHAT_ID, "caption": caption[:1000]}, files={"photo": f}, timeout=60)
    if not r.json().get("ok"):
        print("Telegram photo error:", r.text[:300])
        return False
    return True


# ================================================================ ارسال

def send(text):
    for chunk in [text[i:i + 3900] for i in range(0, len(text), 3900)]:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                          data={"chat_id": MY_CHAT_ID, "text": chunk, "disable_web_page_preview": True},
                          timeout=20)
        if not r.json().get("ok"):
            print("Telegram error:", r.text[:300])
            raise SystemExit(1)
        time.sleep(1)


def fa_date(iso):
    d = datetime.fromisoformat(iso).astimezone(ET_TZ)
    return d.strftime("%b %d, %H:%M ET")


def run_alerts(st, items):
    new = [it for it in items if it["id"] not in st["seen"]]
    print(f"مطالب جدید: {len(new)}")
    now_iso = datetime.now(timezone.utc).isoformat()
    for it in new:
        st["seen"][it["id"]] = now_iso
        st["week"].append({k: it[k] for k in ("id", "source", "bank", "title", "desc", "link", "time", "assets")})
    alerts = [it for it in new if it["forecast"]]
    if not alerts:
        print("هشدار فوری لازم نیست.")
        return
    alerts = alerts[:6]                                # هیچ‌وقت بیشتر از ۶ هشدار در یک اجرا
    sums = summarize_alerts(alerts)
    for i, it in enumerate(alerts, 1):
        assets = " · ".join(ASSET_FA[a] for a in it["assets"])
        lines = [f"🏦 {it['bank']} — دیدگاه جدید", assets, ""]
        if i in sums:
            lines.append(sums[i])
        else:                                          # بدون خلاصه‌ی معتبر: عنوان اصلی
            lines.append(f"عنوان گزارش: «{it['title']}»")
        lines += ["", f"منبع: {it['source']} · {fa_date(it['time'])}"]
        if it["link"]:
            lines.append(it["link"])
        send("\n".join(lines))
        print("هشدار ارسال شد:", it["bank"], it["title"][:70])


def run_weekly(st, items):
    # مطالبی که در اجراهای هشدار ذخیره شدن + هر چیزی که الان در فیدهاست
    seen_ids = {w["id"] for w in st["week"]}
    week_cut = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    pool = [w for w in st["week"] if w["time"] >= week_cut]
    pool += [it for it in items if it["id"] not in seen_ids and it["time"] >= week_cut]
    pool.sort(key=lambda x: x["time"])
    print(f"مطالب هفته: {len(pool)}")
    today = datetime.now(ET_TZ)
    head = [f"🗓 خلاصه‌ی هفتگی دیدگاه بانک‌ها — هفته‌ی منتهی به {today.strftime('%b %d, %Y')}", ""]
    if not pool:
        send("\n".join(head + ["این هفته مطلب تازه‌ای از بانک‌ها درباره‌ی دلار، طلا و نقره یا سهام پیدا نشد."]))
        return
    out = summarize_week(pool)
    lines = list(head)
    for k in ("fx", "metals", "stocks"):
        g = [it for it in pool if k in it["assets"]]
        if not g:
            continue
        lines.append(ASSET_FA[k])
        bullets = out.get(k)
        if bullets:
            for t, banks in bullets:
                lines.append(f"• {t} ({'، '.join(banks)})")
        else:                                          # بدون خلاصه‌ی معتبر: عنوان‌های اصلی
            for it in g[-4:]:
                lines.append(f"• {it['bank']}: «{it['title']}»")
        lines.append("")
    banks = sorted({it["bank"] for it in pool})
    lines += [f"بانک‌های این هفته: {'، '.join(banks)}",
              "خلاصه از متن گزارش‌ها و خبرهای منتشرشده؛ توصیه‌ی سرمایه‌گذاری نیست."]
    card = card_weekly(out, pool, today)
    if card and send_photo(card, f"🗓 خلاصه‌ی هفتگی دیدگاه بانک‌ها — {today.strftime('%b %d, %Y')}"):
        # لینک منابع زیر کارت، به‌صورت متن (قابل کلیک)
        links = ["🔗 منابع این هفته:"]
        for it in pool[-15:]:
            if it.get("link"):
                links.append(f"• {it['bank']}: {it['title'][:80]}\n{it['link']}")
        send("\n".join(links))
    else:
        send("\n".join(lines))
    st["last_weekly"] = datetime.now(timezone.utc).isoformat()


def main():
    st = load_state()
    items = collect()
    if st is None:
        # اولین اجرا: همه‌ی مطالب موجود «دیده‌شده» ثبت می‌شن تا یک‌باره ده‌ها هشدار نیاد
        st = {"seen": {}, "week": [], "last_weekly": None}
        now_iso = datetime.now(timezone.utc).isoformat()
        for it in items:
            st["seen"][it["id"]] = now_iso
            st["week"].append({k: it[k] for k in ("id", "source", "bank", "title", "desc", "link", "time", "assets")})
        save_state(st)
        print(f"اولین اجرا: {len(items)} مطلب ثبت شد. از اجرای بعدی فقط مطالب جدید هشدار می‌گیرن.")
        send("✅ سیستم «دیدگاه بانک‌ها» فعال شد.\n"
             f"{len(items)} مطلب موجود ثبت شد. از این به بعد، وقتی بانکی پیش‌بینی جدید منتشر کنه، "
             "این‌جا هشدار فارسی می‌گیرید؛ و جمعه‌ها خلاصه‌ی هفتگی.")
        return
    if MODE == "weekly":
        run_weekly(st, items)
    else:
        run_alerts(st, items)
    save_state(st)


if __name__ == "__main__":
    main()
