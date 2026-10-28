"""
کارت «Scenario Watch» — سناریوهای قابل پیگیری برای رویدادهای پراهمیت آمریکا.

این سیگنال معاملاتی نیست. برای هر رویداد مهم امروز:
  زمان    ← از تقویم Forex Factory
  شرط     ← عدد واقعی بالاتر/پایین‌تر از پیش‌بینی (یا عدد قبلی اگه پیش‌بینی نباشه)
  دلیل    ← سازوکار شناخته‌شده‌ی اقتصادی، از جدول ثابت RULES
  بازبینی ← اگه بازار در یک ساعت اول خلاف سناریو حرکت کرد، فرض بازبینی می‌شه

هیچ هوش مصنوعی‌ای استفاده نمی‌شه. رویدادی که در جدول نیست، سناریو نمی‌گیره.
اگه امروز رویداد پراهمیتی نباشه، پستی ارسال نمی‌شه.
زمان اجرا: دوشنبه تا جمعه، ساعت ۸:۰۰ صبح به وقت نیویورک.
"""

import os
import re
import time
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
ET = ZoneInfo("America/New_York")

FEEDS = ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
         "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "application/json"}

# ---------------------------------------------------------------- جدول واکنش‌ها
# «hawkish» = سناریویی که انتظار سیاست پولی سخت‌گیرانه‌تر رو بالا می‌بره
UP, DOWN, MIX = "↑", "↓", "mixed"
REACTIONS = {
    "hawkish_inflation": [("10Y yield", UP), ("U.S. dollar", UP), ("Gold", DOWN), ("Stocks", DOWN), ("Bitcoin", DOWN)],
    "hawkish_growth":    [("10Y yield", UP), ("U.S. dollar", UP), ("Gold", DOWN), ("Stocks", MIX), ("Bitcoin", MIX)],
    "hawkish_fed":       [("10Y yield", UP), ("U.S. dollar", UP), ("Gold", DOWN), ("Stocks", DOWN), ("Bitcoin", DOWN)],
}


def flip(reactions):
    return [(k, DOWN if v == UP else UP if v == DOWN else v) for k, v in reactions]


# هر گروه: الگوی عنوان‌ها، اولویت شاخص اصلی، جهت (معکوس یا نه)، دلیل‌ها
RULES = [
    {"key": "cpi", "name": "Consumer Price Index (CPI)",
     "match": r"\bCPI\b", "priority": [r"^Core CPI m/m", r"^CPI m/m", r"^CPI y/y", r"^Core CPI y/y"],
     "react": "hawkish_inflation", "inverse": False,
     "why_up": "Hotter inflation tends to raise expectations of tighter Fed policy.",
     "why_down": "Cooler inflation tends to ease expectations of further Fed tightening."},
    {"key": "pce", "name": "PCE Inflation (the Fed's preferred gauge)",
     "match": r"\bPCE\b", "priority": [r"^Core PCE Price Index m/m", r"^PCE Price Index m/m"],
     "react": "hawkish_inflation", "inverse": False,
     "why_up": "Faster PCE inflation, the Fed's preferred measure, tends to support tighter policy expectations.",
     "why_down": "Slower PCE inflation tends to ease pressure on the Fed to tighten."},
    {"key": "ppi", "name": "Producer Price Index (PPI)",
     "match": r"\bPPI\b", "priority": [r"^Core PPI m/m", r"^PPI m/m"],
     "react": "hawkish_inflation", "inverse": False,
     "why_up": "Higher producer prices can signal inflation pressure ahead, supporting tighter policy expectations.",
     "why_down": "Softer producer prices can signal easing inflation pressure ahead."},
    {"key": "nfp", "name": "Jobs Report (Non-Farm Payrolls)",
     # «^» مهمه: ADP Non-Farm Employment Change گزارش خصوصیه و قانون جدای خودش رو داره
     "match": r"^Non-Farm Employment Change|^Unemployment Rate|^Average Hourly Earnings",
     "priority": [r"^Non-Farm Employment Change"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Stronger hiring tends to support expectations that rates stay higher for longer.",
     "why_down": "Weaker hiring tends to raise expectations of an easier Fed path."},
    {"key": "unemp", "name": "Unemployment Rate",
     "match": r"^Unemployment Rate", "priority": [r"^Unemployment Rate"],
     "react": "hawkish_growth", "inverse": True,
     "why_up": "A lower jobless rate signals a tight labor market, supporting higher-rate expectations.",
     "why_down": "A higher jobless rate signals a cooling labor market, supporting easier-policy expectations."},
    {"key": "claims", "name": "Weekly Jobless Claims",
     "match": r"^Unemployment Claims", "priority": [r"^Unemployment Claims"],
     "react": "hawkish_growth", "inverse": True,
     "why_up": "Fewer jobless claims point to a resilient labor market.",
     "why_down": "More jobless claims point to a softening labor market."},
    {"key": "jolts", "name": "JOLTS Job Openings",
     "match": r"JOLTS", "priority": [r"JOLTS"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "More job openings signal strong labor demand.",
     "why_down": "Fewer job openings signal cooling labor demand."},
    {"key": "adp", "name": "ADP Private Payrolls",
     "match": r"^ADP Non-Farm Employment Change", "priority": [r"^ADP Non-Farm"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Stronger private hiring can raise expectations for Friday's jobs report.",
     "why_down": "Weaker private hiring can lower expectations for Friday's jobs report."},
    {"key": "gdp", "name": "GDP",
     "match": r"GDP q/q|GDP Price Index", "priority": [r"GDP q/q"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Faster growth tends to reduce the case for rate cuts.",
     "why_down": "Slower growth tends to strengthen the case for easier policy."},
    {"key": "retail", "name": "Retail Sales",
     "match": r"Retail Sales", "priority": [r"^Retail Sales m/m", r"^Core Retail Sales m/m"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Stronger consumer spending points to a resilient economy.",
     "why_down": "Weaker consumer spending points to a slowing economy."},
    {"key": "ism_m", "name": "ISM Manufacturing",
     "match": r"^ISM Manufacturing", "priority": [r"^ISM Manufacturing PMI", r"^ISM Manufacturing Prices"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Stronger factory activity points to firmer growth (readings above 50 signal expansion).",
     "why_down": "Weaker factory activity points to softer growth (readings below 50 signal contraction)."},
    {"key": "ism_s", "name": "ISM Services",
     "match": r"^ISM Services", "priority": [r"^ISM Services PMI", r"^ISM Services Prices"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Stronger services activity points to firmer growth (readings above 50 signal expansion).",
     "why_down": "Weaker services activity points to softer growth (readings below 50 signal contraction)."},
    {"key": "conf", "name": "Consumer Confidence",
     "match": r"CB Consumer Confidence", "priority": [r"CB Consumer Confidence"],
     "react": "hawkish_growth", "inverse": False,
     "why_up": "Higher confidence can signal stronger future consumer spending.",
     "why_down": "Lower confidence can signal weaker future consumer spending."},
    {"key": "fomc", "name": "Fed Rate Decision (FOMC)",
     "match": r"Federal Funds Rate|FOMC Statement|FOMC Press Conference|FOMC Economic Projections",
     "priority": [r"^Federal Funds Rate"],
     "react": "hawkish_fed", "inverse": False,
     "why_up": "A larger-than-expected hike, or a more hawkish statement, tends to lift rate expectations.",
     "why_down": "A smaller move than expected, or a more dovish statement, tends to lower rate expectations."},
]


# ---------------------------------------------------------------- داده

def fetch_calendar():
    for url in FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=25)
            r.raise_for_status()
            if r.text.lstrip().startswith("<"):
                raise RuntimeError("HTML instead of JSON (rate limit?)")
            data = r.json()
            print(f"✓ Forex Factory: {len(data)} events")
            return data
        except Exception as e:
            print(f"✗ {url}: {e}")
            time.sleep(2)
    raise SystemExit("تقویم Forex Factory دریافت نشد. پستی ارسال نشد.")


def todays_us_events(raw, today):
    evs = []
    for e in raw:
        if (e.get("country") or "").upper() != "USD":
            continue
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(ET)
        except (KeyError, ValueError, TypeError):
            continue
        if when.date() != today:
            continue
        evs.append({"title": (e.get("title") or "").strip(), "impact": e.get("impact", ""),
                    "forecast": (e.get("forecast") or "").strip(),
                    "previous": (e.get("previous") or "").strip(), "when": when})
    return evs


def build_scenarios(evs):
    """رویدادها رو با جدول قوانین تطبیق می‌ده. فقط گروه‌هایی که حداقل یک رویداد High دارن."""
    scenarios, used = [], set()
    for rule in RULES:
        group = [e for e in evs if re.search(rule["match"], e["title"], re.I) and id(e) not in used]
        if not group or not any(e["impact"] == "High" for e in group):
            continue
        # فقط رویدادهای هم‌زمان با اولین رویداد High
        t0 = min(e["when"] for e in group if e["impact"] == "High")
        group = [e for e in group if e["when"] == t0]
        primary = None
        for pat in rule["priority"]:
            primary = next((e for e in group if re.search(pat, e["title"], re.I)), None)
            if primary:
                break
        primary = primary or group[0]
        for e in group:
            used.add(id(e))
        # در روز گزارش اشتغال، نرخ بیکاری جزو همون گروه NFP حساب می‌شه
        if rule["key"] == "unemp" and any(s["rule"]["key"] == "nfp" for s in scenarios):
            continue
        base = primary["forecast"] or primary["previous"]
        if not base:
            print(f"سناریو ساخته نشد (نه پیش‌بینی، نه عدد قبلی): {primary['title']}")
            continue
        scenarios.append({"rule": rule, "time": t0, "primary": primary,
                          "others": [e for e in group if e is not primary],
                          "base": base, "base_kind": "forecast" if primary["forecast"] else "previous"})
    scenarios.sort(key=lambda s: s["time"])
    return scenarios


# ---------------------------------------------------------------- کارت تصویری

W = 1080
BG = (11, 14, 20); PANEL = (24, 29, 39); PANEL2 = (18, 22, 31); LINE = (38, 44, 56)
TEXT = (240, 244, 248); MUTED = (155, 165, 178); BLUE = (91, 155, 255); AMBER = (245, 166, 35)
TEAL = (64, 190, 200); RED = (239, 83, 80)


def font(n, b=False):
    p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if b else ''}.ttf"
    return ImageFont.truetype(p, n) if os.path.exists(p) else ImageFont.load_default(size=n)


F38B, F30B, F26B, F22B, F20B, F18B = font(38, 1), font(30, 1), font(26, 1), font(22, 1), font(20, 1), font(18, 1)
F22, F21, F20, F18 = font(22), font(21), font(20), font(18)


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
    return lines + ([cur] if cur else [])


def draw_watch(d, x, y, reactions):
    """ردیف «Watch»: هر ابزار با فلش"""
    cx = x
    for name, arrow in reactions:
        label = f"{name} "
        d.text((cx, y), label, font=F20, fill=MUTED)
        cx += d.textlength(label, font=F20)
        col = TEXT if arrow == MIX else (TEXT if arrow in (UP, DOWN) else MUTED)
        d.text((cx, y - 1), arrow, font=F20B, fill=col)
        cx += d.textlength(arrow, font=F20B) + 26
    return cx


def branch(d, y, color, title, why, reactions):
    """یک شاخه‌ی سناریو («اگر بالاتر…» یا «اگر پایین‌تر…») و برگردوندن y جدید"""
    lines = wrap(d, "Why: " + why, F21, 880)
    h = 50 + 30 * len(lines) + 44
    rr(d, (70, y, 1010, y + h), PANEL2, r=14)
    d.rectangle((70, y + 12, 75, y + h - 12), fill=color)
    d.text((95, y + 14), title, font=F22B, fill=color)
    yy = y + 50
    for ln in lines:
        d.text((95, yy), ln, font=F21, fill=TEXT)
        yy += 30
    d.text((95, yy + 6), "Watch:", font=F20B, fill=MUTED)
    draw_watch(d, 95 + d.textlength("Watch:  ", font=F20B), yy + 6, reactions)
    return y + h + 12


def draw_scenario(d, s):
    rule, p = s["rule"], s["primary"]
    hawk = REACTIONS[rule["react"]]
    # برای شاخص‌های معکوس (بیکاری، مدعیان)، «بالاتر» یعنی بازار کار ضعیف‌تر
    up_react, down_react = (flip(hawk), hawk) if rule["inverse"] else (hawk, flip(hawk))
    up_why, down_why = (rule["why_down"], rule["why_up"]) if rule["inverse"] else (rule["why_up"], rule["why_down"])
    kind = "forecast" if s["base_kind"] == "forecast" else "previous; no forecast"

    y = 22
    d.text((78, y), s["time"].strftime("%H:%M") + " ET", font=F26B, fill=TEXT)
    d.ellipse((250, y + 9, 264, y + 23), fill=RED)
    d.text((278, y), rule["name"], font=F26B, fill=TEXT)
    y += 42
    fp = f"{p['title']}  —  Forecast: {p['forecast'] or 'n/a'}  ·  Previous: {p['previous'] or 'n/a'}"
    for ln in wrap(d, fp, F20, 920):
        d.text((78, y), ln, font=F20, fill=MUTED); y += 28
    if s["others"]:
        also = "Also released: " + ", ".join(e["title"] for e in s["others"][:4])
        for ln in wrap(d, also, F18, 920):
            d.text((78, y), ln, font=F18, fill=MUTED); y += 26
    y += 12

    y = branch(d, y, AMBER, f"IF above {s['base']} ({kind})", up_why, up_react)
    y = branch(d, y, TEAL, f"IF below {s['base']} ({kind})", down_why, down_react)

    inline = "If in line: the reaction may depend more on revisions and details inside the report."
    if rule["key"] == "fomc":
        inline = "If in line: attention shifts to the statement, projections and the press conference."
    review_t = (s["time"] + timedelta(hours=1)).strftime("%H:%M")
    review = (f"Review: if yields and the dollar move against the matching scenario by {review_t} ET, "
              "the market may be focused on other factors — reassess the assumption.")
    for txt, f, c in ((inline, F20, MUTED), (review, F20B, BLUE)):
        for ln in wrap(d, txt, f, 920):
            d.text((78, y + 4), ln, font=f, fill=c); y += 28
        y += 6
    return y + 16


def render(scenarios, now):
    im = Image.new("RGB", (W, 5000), BG)
    d = ImageDraw.Draw(im)
    d.text((55, 42), "TRADINGLIFE", font=F38B, fill=TEXT)
    d.text((55, 91), "Scenario Watch · U.S. data", font=F21, fill=MUTED)
    right(d, 1025, 48, "SCENARIOS", F30B, BLUE)
    right(d, 1025, 92, now.strftime("%A, %b %d, %Y"), F22, TEXT)
    y = 140

    intro = ("Trackable scenarios, not trading signals: each shows the release time, the condition "
             "to check, the usual reasoning, and when to reassess.")
    for ln in wrap(d, intro, F20, 960):
        d.text((58, y), ln, font=F20, fill=MUTED); y += 28
    y += 18

    for s in scenarios:
        # هر سناریو روی بوم جداگانه با رنگ پنل کشیده می‌شه، بعد با گوشه‌های گرد روی کارت قرار می‌گیره
        tmp = Image.new("RGB", (W, 1600), PANEL)
        h = draw_scenario(ImageDraw.Draw(tmp), s)
        mask = Image.new("L", (W, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((50, 0, 1030, h - 1), radius=18, fill=255)
        im.paste(tmp.crop((0, 0, W, h)), (0, y), mask)
        y += h + 22

    rr(d, (50, y, 1030, y + 120), PANEL2)
    d.text((75, y + 20), f"TradingLife  •  Updated {now.strftime('%Y-%m-%d %H:%M')} ET", font=F18, fill=TEXT)
    d.text((75, y + 52), "Schedule & forecasts: Forex Factory calendar. Reactions describe typical tendencies,",
           font=F18, fill=MUTED)
    d.text((75, y + 80), "not predictions. Educational content — not trading signals or investment advice.",
           font=F18, fill=MUTED)
    y += 120 + 90
    im = im.crop((0, 0, W, y))
    os.makedirs("scenario_cards", exist_ok=True)
    path = "scenario_cards/scenario_watch.png"
    im.save(path, "PNG", optimize=True)
    return path


SC_TAGS = {"cpi": "#CPI", "pce": "#PCE", "ppi": "#PPI", "nfp": "#NFP", "unemp": "#Jobs", "claims": "#Jobs",
           "jolts": "#JOLTS", "adp": "#Jobs", "gdp": "#GDP", "retail": "#RetailSales", "ism_m": "#ISM",
           "ism_s": "#ISM", "conf": "#ConsumerConfidence", "fomc": "#FOMC"}


def short_watch(reactions):
    parts = [f"{n.replace('10Y yield', 'yields').replace('U.S. dollar', 'dollar').lower()} {a}"
             for n, a in reactions if n in ("10Y yield", "U.S. dollar", "Gold")]
    return ", ".join(parts)


def caption_text(scenarios, now):
    """متن زیر عکس — خلاصه‌ی سناریوها، فقط از روی جدول قوانین و تقویم."""
    lines = [f"🎯 Scenario Watch — {now.strftime('%a, %b %d')}", "", "Today's key U.S. data:"]
    for s in scenarios:
        rule, p = s["rule"], s["primary"]
        hawk = REACTIONS[rule["react"]]
        up, down = (flip(hawk), hawk) if rule["inverse"] else (hawk, flip(hawk))
        basis = "forecast" if s["base_kind"] == "forecast" else "previous"
        lines.append(f"• {s['time'].strftime('%H:%M')} ET — {rule['name']} ({basis} {s['base']})")
        lines.append(f"   Above: {short_watch(up)}")
        lines.append(f"   Below: {short_watch(down)}")
    lines += ["", "If markets move against the matching scenario within an hour, reassess.",
              "Typical reactions, not trading signals. Not investment advice."]
    tags = ["#Scenarios", "#USD"]
    for s in scenarios:
        t = SC_TAGS.get(s["rule"]["key"])
        if t and t not in tags:
            tags.append(t)
    lines += ["", " ".join(tags[:7])]
    txt = "\n".join(lines)
    if len(txt) > 1024:
        txt = txt[:990].rsplit("\n•", 1)[0] + "\n…see the card for all scenarios.\n\n" + " ".join(tags[:7])
    return txt


def send_photo(path, caption=None):
    data = {"chat_id": CHAT_ID}
    if caption:
        data["caption"] = caption
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          data=data, files={"photo": f}, timeout=60)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال: {res}")
        raise SystemExit(1)
    print("کارت Scenario Watch ارسال شد.")



# ---------------------------------------------------------------- نسخه‌ی فارسی
from tl_fa import IS_FA, TEHRAN, fa_font, draw_r, fa_date, event_fa, hashtags
from tl_fa import wrap as wrap_fa

RULES_FA = {
    "cpi": ("تورم مصرف‌کننده (CPI)", "تورم داغ‌تر معمولاً انتظار سیاست سخت‌گیرانه‌تر فدرال رزرو رو بالا می‌بره.",
            "تورم آروم‌تر معمولاً انتظار سخت‌گیری بیشتر فدرال رزرو رو کم می‌کنه."),
    "pce": ("تورم PCE (شاخص مورد توجه فدرال رزرو)", "تورم PCE بالاتر، که معیار اصلی فدرال رزروه، معمولاً از انتظار سیاست سخت‌گیرانه‌تر حمایت می‌کنه.",
            "تورم PCE پایین‌تر معمولاً فشار روی فدرال رزرو برای سخت‌گیری رو کم می‌کنه."),
    "ppi": ("تورم تولیدکننده (PPI)", "قیمت‌های بالاتر تولیدکننده می‌تونه نشونه‌ی فشار تورمی پیش رو باشه.",
            "قیمت‌های آروم‌تر تولیدکننده می‌تونه نشونه‌ی کاهش فشار تورمی پیش رو باشه."),
    "nfp": ("گزارش اشتغال (NFP)", "استخدام قوی‌تر معمولاً از انتظار ماندن نرخ‌ها در سطح بالا حمایت می‌کنه.",
            "استخدام ضعیف‌تر معمولاً انتظار سیاست پولی آسون‌تر رو بالا می‌بره."),
    "unemp": ("نرخ بیکاری", "نرخ بیکاری پایین‌تر نشونه‌ی بازار کار داغه و از انتظار نرخ‌های بالاتر حمایت می‌کنه.",
              "نرخ بیکاری بالاتر نشونه‌ی سرد شدن بازار کاره و از انتظار سیاست آسون‌تر حمایت می‌کنه."),
    "claims": ("مدعیان بیکاری هفتگی", "مدعیان کمتر یعنی بازار کار مقاومه.", "مدعیان بیشتر یعنی بازار کار داره ضعیف می‌شه."),
    "jolts": ("فرصت‌های شغلی JOLTS", "فرصت‌های شغلی بیشتر یعنی تقاضای قوی برای نیروی کار.",
              "فرصت‌های شغلی کمتر یعنی تقاضای نیروی کار داره سرد می‌شه."),
    "adp": ("اشتغال بخش خصوصی ADP", "استخدام قوی‌تر بخش خصوصی می‌تونه انتظار از گزارش اشتغال جمعه رو بالا ببره.",
            "استخدام ضعیف‌تر بخش خصوصی می‌تونه انتظار از گزارش اشتغال جمعه رو پایین بیاره."),
    "gdp": ("تولید ناخالص داخلی (GDP)", "رشد سریع‌تر معمولاً دلیل کاهش نرخ بهره رو ضعیف‌تر می‌کنه.",
            "رشد کندتر معمولاً دلیل سیاست آسون‌تر رو قوی‌تر می‌کنه."),
    "retail": ("خرده‌فروشی", "خرج قوی‌تر مصرف‌کننده نشونه‌ی اقتصاد مقاومه.", "خرج ضعیف‌تر مصرف‌کننده نشونه‌ی کند شدن اقتصاده."),
    "ism_m": ("ISM تولیدی", "فعالیت قوی‌تر کارخانه‌ها نشونه‌ی رشد محکم‌تره (بالای 50 یعنی رشد).",
              "فعالیت ضعیف‌تر کارخانه‌ها نشونه‌ی رشد کندتره (زیر 50 یعنی انقباض)."),
    "ism_s": ("ISM خدمات", "فعالیت قوی‌تر بخش خدمات نشونه‌ی رشد محکم‌تره (بالای 50 یعنی رشد).",
              "فعالیت ضعیف‌تر بخش خدمات نشونه‌ی رشد کندتره (زیر 50 یعنی انقباض)."),
    "conf": ("اعتماد مصرف‌کننده", "اعتماد بالاتر می‌تونه نشونه‌ی خرج بیشتر مصرف‌کننده در آینده باشه.",
             "اعتماد پایین‌تر می‌تونه نشونه‌ی خرج کمتر مصرف‌کننده در آینده باشه."),
    "fomc": ("تصمیم نرخ بهره‌ی فدرال رزرو", "افزایش بیشتر از انتظار یا بیانیه‌ی سخت‌گیرانه‌تر معمولاً انتظار نرخ بالاتر رو تقویت می‌کنه.",
             "تغییر کمتر از انتظار یا بیانیه‌ی ملایم‌تر معمولاً انتظار نرخ پایین‌تر رو تقویت می‌کنه."),
}
WATCH_FA = {"10Y yield": "بازده 10 ساله", "U.S. dollar": "دلار", "Gold": "طلا", "Stocks": "سهام", "Bitcoin": "بیت‌کوین"}
SC_TAGS_FA = {"cpi": "تورم", "pce": "تورم_PCE", "ppi": "تورم_تولیدکننده", "nfp": "گزارش_اشتغال",
              "unemp": "اشتغال", "claims": "اشتغال", "jolts": "اشتغال", "adp": "اشتغال", "gdp": "رشد_اقتصادی",
              "retail": "خرده_فروشی", "ism_m": "ISM", "ism_s": "ISM", "conf": "اعتماد_مصرف_کننده",
              "fomc": "فدرال_رزرو"}


def sides(s):
    rule = s["rule"]
    hawk = REACTIONS[rule["react"]]
    up, down = (flip(hawk), hawk) if rule["inverse"] else (hawk, flip(hawk))
    name, w_up, w_down = RULES_FA[rule["key"]]
    up_why, down_why = (w_down, w_up) if rule["inverse"] else (w_up, w_down)
    return name, up, down, up_why, down_why


def watch_text(reactions):
    # فونت فارسی علامت ↑↓ نداره؛ روی کارت از کلمه استفاده می‌شه
    word = {UP: "بالا", DOWN: "پایین", MIX: "نامشخص"}
    return " · ".join(f"{WATCH_FA[n]} {word.get(a, a)}" for n, a in reactions)


def draw_scenario_fa(d, s, F):
    R = 1005
    name, up, down, up_why, down_why = sides(s)
    p = s["primary"]
    t_ir = s["time"].astimezone(TEHRAN).strftime("%H:%M")
    kind = "پیش‌بینی" if s["base_kind"] == "forecast" else "عدد قبلی؛ پیش‌بینی نداره"
    y = 20
    draw_r(d, R, y, t_ir, F["h"], TEXT)
    d.ellipse((R - 105, y + 13, R - 91, y + 27), fill=RED)
    draw_r(d, R - 120, y, name, F["h"], TEXT)
    y += 46
    sub = f"{event_fa(p['title'])} — پیش‌بینی: {p['forecast'] or '—'} · قبلی: {p['previous'] or '—'}"
    for ln in wrap_fa(d, sub, F["s"], 920):
        draw_r(d, R, y, ln, F["s"], MUTED); y += 30
    if s["others"]:
        also = "هم‌زمان منتشر می‌شه: " + "، ".join(event_fa(e["title"]) for e in s["others"][:4])
        for ln in wrap_fa(d, also, F["xs"], 920):
            draw_r(d, R, y, ln, F["xs"], MUTED); y += 28
    y += 12
    for color, title, why, react in ((AMBER, f"اگه بالاتر از {s['base']} بود ({kind})", up_why, up),
                                     (TEAL, f"اگه پایین‌تر از {s['base']} بود ({kind})", down_why, down)):
        lines = wrap_fa(d, "چرا: " + why, F["b"], 880)
        h = 54 + 34 * len(lines) + 48
        d.rounded_rectangle((70, y, 1010, y + h), radius=14, fill=PANEL2)
        d.rectangle((1004, y + 12, 1010, y + h - 12), fill=color)
        draw_r(d, 985, y + 12, title, F["bb"], color)
        yy = y + 52
        for ln in lines:
            draw_r(d, 985, yy, ln, F["b"], TEXT); yy += 34
        draw_r(d, 985, yy + 6, "زیر نظر: " + watch_text(react), F["wb"], MUTED)
        y += h + 12
    inline = ("اگه مطابق انتظار بود: واکنش بیشتر به جزئیات گزارش و اصلاح ارقام قبلی بستگی داره."
              if s["rule"]["key"] != "fomc" else
              "اگه مطابق انتظار بود: توجه به بیانیه، پیش‌بینی‌ها و کنفرانس خبری منتقل می‌شه.")
    review_t = (s["time"] + timedelta(hours=1)).astimezone(TEHRAN).strftime("%H:%M")
    review = (f"بازبینی: اگه تا ساعت {review_t} بازده اوراق و دلار خلاف سناریوی فعال‌شده حرکت کردن، "
              "احتمالاً بازار روی عوامل دیگه تمرکز داره — فرض اولیه رو بازبینی کنید.")
    for txt, f, c in ((inline, F["s"], MUTED), (review, F["sb"], BLUE)):
        for ln in wrap_fa(d, txt, f, 920):
            draw_r(d, R, y + 4, ln, f, c); y += 32
        y += 6
    return y + 16


def render_fa(scenarios, now):
    F = {"title": fa_font(40, True), "brand": fa_font(30, True), "h": fa_font(27, True), "s": fa_font(21),
         "sb": fa_font(21, True), "xs": fa_font(19), "b": fa_font(22), "bb": fa_font(23, True), "wb": fa_font(21, True)}
    im = Image.new("RGB", (W, 6000), BG)
    d = ImageDraw.Draw(im)
    d.text((55, 48), "TRADINGLIFE", font=F["brand"], fill=TEXT)
    draw_r(d, 1025, 34, "سناریوهای امروز", F["title"], TEXT)
    draw_r(d, 1025, 96, f"داده‌های آمریکا · {fa_date(now.astimezone(TEHRAN))}", F["s"], BLUE)
    y = 145
    intro = ("سناریوهای قابل پیگیری، نه سیگنال معاملاتی: هر سناریو زمان انتشار، شرط، "
             "دلیل معمول و زمان بازبینی رو نشون می‌ده.")
    for ln in wrap_fa(d, intro, F["s"], 960):
        draw_r(d, 1025, y, ln, F["s"], MUTED); y += 30
    y += 16
    for s in scenarios:
        tmp = Image.new("RGB", (W, 1800), PANEL)
        h = draw_scenario_fa(ImageDraw.Draw(tmp), s, F)
        mask = Image.new("L", (W, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((50, 0, 1030, h - 1), radius=18, fill=255)
        im.paste(tmp.crop((0, 0, W, h)), (0, y), mask)
        y += h + 22
    rr(d, (50, y, 1030, y + 120), PANEL2)
    draw_r(d, 1005, y + 18, f"TradingLife · به‌روزرسانی {now.astimezone(TEHRAN).strftime('%H:%M')} به وقت تهران",
           F["xs"], TEXT)
    draw_r(d, 1005, y + 50, "زمان‌ها و پیش‌بینی‌ها: تقویم Forex Factory · واکنش‌ها گرایش معمول بازارن، نه پیش‌بینی",
           F["xs"], MUTED)
    draw_r(d, 1005, y + 80, "محتوای آموزشی — سیگنال معاملاتی یا توصیه‌ی سرمایه‌گذاری نیست", F["xs"], MUTED)
    y += 120 + 90
    im = im.crop((0, 0, W, y))
    os.makedirs("scenario_cards", exist_ok=True)
    path = "scenario_cards/scenario_watch_fa.png"
    im.save(path, "PNG", optimize=True)
    return path


def caption_fa(scenarios, now):
    short = lambda r: "، ".join(f"{WATCH_FA[n]} {a}" for n, a in r if n in ("10Y yield", "U.S. dollar", "Gold"))
    lines = [f"🎯 سناریوهای امروز — {fa_date(now.astimezone(TEHRAN), year=False)}", "", "داده‌های مهم آمریکا:"]
    for s in scenarios:
        name, up, down, _, _ = sides(s)
        basis = "پیش‌بینی" if s["base_kind"] == "forecast" else "قبلی"
        lines.append(f"• {s['time'].astimezone(TEHRAN).strftime('%H:%M')} — {name} ({basis} {s['base']})")
        lines.append(f"   بالاتر: {short(up)}")
        lines.append(f"   پایین‌تر: {short(down)}")
    lines += ["", "اگه بازار تا یک ساعت بعد خلاف سناریو حرکت کرد، فرض رو بازبینی کنید.",
              "ساعت‌ها به وقت تهران. گرایش‌های معمول بازار، نه سیگنال معاملاتی."]
    tags = ["سناریو", "دلار"] + [SC_TAGS_FA[s["rule"]["key"]] for s in scenarios]
    lines += ["", hashtags(*tags)]
    txt = "\n".join(lines)
    if len(txt) > 1024:
        txt = txt[:980].rsplit("\n•", 1)[0] + "\n…بقیه‌ی سناریوها روی کارت.\n\n" + hashtags(*tags)
    return txt


def main():
    now = datetime.now(ET)
    evs = todays_us_events(fetch_calendar(), now.date())
    print(f"رویدادهای آمریکای امروز: {len(evs)}")
    scenarios = build_scenarios(evs)
    for s in scenarios:
        print(f"  سناریو: {s['time'].strftime('%H:%M')} {s['rule']['name']} — مبنا {s['base']} ({s['base_kind']})")
    if not scenarios:
        print("امروز رویداد پراهمیت آمریکایی با سناریوی تعریف‌شده نیست — پستی ارسال نشد.")
        return
    if IS_FA:
        send_photo(render_fa(scenarios, now), caption_fa(scenarios, now))
    else:
        send_photo(render(scenarios, now), caption_text(scenarios, now))


if __name__ == "__main__":
    main()
