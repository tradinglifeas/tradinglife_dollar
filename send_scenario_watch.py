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
     "match": r"Non-Farm Employment Change|Unemployment Rate|Average Hourly Earnings",
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


def send_photo(path):
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          data={"chat_id": CHAT_ID}, files={"photo": f}, timeout=60)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال: {res}")
        raise SystemExit(1)
    print("کارت Scenario Watch ارسال شد.")


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
    send_photo(render(scenarios, now))


if __name__ == "__main__":
    main()
