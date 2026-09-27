"""
تقویم اقتصادی برای کانال تلگرام (انگلیسی، مخاطب آمریکایی).

دو حالت (با متغیر MODE):
  MODE=weekly : یکشنبه عصر → همه‌ی رویدادهای مهم هفته‌ی پیش رو (تا جمعه)
  MODE=daily  : دوشنبه تا پنجشنبه بعد از بسته شدن بازار → رویدادهای امشب + فردا
               (جمعه اجرا نمی‌شه چون پست یکشنبه، دوشنبه رو پوشش می‌ده)

منبع داده: خروجی رسمی و رایگان تقویم Forex Factory (بدون کلید، بدون هوش مصنوعی).
  - زمان‌ها همراه با اختلاف ساعت نیویورک هستن و به وقت ET نمایش داده می‌شن.
  - Forex Factory حداکثر ۲ درخواست در ۵ دقیقه اجازه می‌ده؛ هر اجرا فقط ۱ درخواست می‌زنه.

فیلتر (برای مخاطب آمریکایی):
  - USD: رویدادهای High و Medium + تعطیلی‌های بازار آمریکا
  - سایر ارزهای اصلی: فقط High
"""

import os
import re
import time
import requests
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
MODE = os.environ.get("MODE", "daily").lower()

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
ET = ZoneInfo("America/New_York")

FEEDS = {
    "thisweek": ["https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                 "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.json"],
    "nextweek": ["https://nfs.faireconomy.media/ff_calendar_nextweek.json"],
}

FLAGS = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵", "CAD": "🇨🇦",
         "AUD": "🇦🇺", "NZD": "🇳🇿", "CHF": "🇨🇭", "CNY": "🇨🇳"}

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
}


# ---------------------------------------------------------------- داده

def fetch(which):
    last = None
    for url in FEEDS[which]:
        try:
            r = requests.get(url, headers=REQUEST_HEADERS, timeout=25)
            if r.status_code == 404:
                last = "404"; continue
            r.raise_for_status()
            if r.text.lstrip().startswith("<"):
                # وقتی سقف درخواست رد بشه، به‌جای JSON یک صفحه‌ی HTML «Request Denied» برمی‌گرده
                raise RuntimeError("Forex Factory returned an HTML page (rate limit?)")
            data = r.json()
            print(f"✓ {which}: {len(data)} events ({url})")
            return data
        except Exception as e:
            last = e
            print(f"✗ {which}: {url} -> {e}")
            time.sleep(2)
    print(f"فید {which} در دسترس نیست: {last}")
    return []


def parse(events):
    out = []
    for e in events:
        try:
            when = datetime.fromisoformat(e["date"]).astimezone(ET)
        except (KeyError, ValueError, TypeError):
            continue
        out.append({
            "title": (e.get("title") or "").strip(),
            "cur": (e.get("country") or "").strip().upper(),
            "impact": (e.get("impact") or "").strip(),
            "forecast": (e.get("forecast") or "").strip(),
            "previous": (e.get("previous") or "").strip(),
            "when": when,
        })
    return out


def important(ev):
    if ev["cur"] not in FLAGS:
        return False
    if ev["impact"] == "Holiday":
        return ev["cur"] == "USD"
    if ev["cur"] == "USD":
        return ev["impact"] in ("High", "Medium")
    return ev["impact"] == "High"


def load_window(start, end):
    """رویدادهای بازه‌ی [start, end] رو برمی‌گردونه و چک می‌کنه داده واقعاً اون بازه رو پوشش بده."""
    events = parse(fetch("thisweek"))
    last_day = max((e["when"].date() for e in events), default=None)
    if last_day is None or last_day < end.date() - timedelta(days=1):
        print(f"فید این هفته تا {last_day} است؛ فید هفته‌ی بعد هم امتحان می‌شه.")
        time.sleep(3)
        events += parse(fetch("nextweek"))
        last_day = max((e["when"].date() for e in events), default=None)

    # داده باید حداقل تا روز کاری آخر بازه برسه، وگرنه تقویم ناقص منتشر نمی‌شه
    need = end.date()
    while need.weekday() >= 5:
        need -= timedelta(days=1)
    if last_day is None or last_day < need:
        raise SystemExit(f"داده‌ی تقویم فقط تا {last_day} است ولی تا {need} لازمه. پستی ارسال نشد.")

    seen, picked = set(), []
    for e in sorted(events, key=lambda x: (x["when"], x["cur"], x["title"])):
        if not (start <= e["when"] <= end) or not important(e):
            continue
        key = (e["when"], e["cur"], e["title"])
        if key in seen:
            continue
        seen.add(key)
        picked.append(e)
    print(f"رویدادهای مهم در بازه: {len(picked)}")
    return picked


# ---------------------------------------------------------------- ساخت تصویر
# همون سبک تیره‌ی کارت‌های Market Intelligence

W = 1080
BG = (11, 14, 20); PANEL = (24, 29, 39); PANEL2 = (18, 22, 31); LINE = (38, 44, 56)
TEXT = (240, 244, 248); MUTED = (155, 165, 178); RED = (239, 83, 80); AMBER = (245, 166, 35)
BLUE = (91, 155, 255); GREEN = (44, 200, 134)
BADGE = {"USD": (52, 104, 214), "EUR": (70, 84, 110), "GBP": (70, 84, 110), "JPY": (70, 84, 110),
         "CAD": (70, 84, 110), "AUD": (70, 84, 110), "NZD": (70, 84, 110), "CHF": (70, 84, 110),
         "CNY": (70, 84, 110)}
MAX_ROWS_PER_IMAGE = 26


def font(size, bold=False):
    path = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


F_BRAND = font(38, True); F_SUB = font(21); F_TAG = font(28, True); F_RANGE = font(24)
F_COLH = font(17, True); F_DAY = font(23, True); F_TIME = font(24, True); F_EV = font(24)
F_NUM = font(23); F_BADGE = font(18, True); F_FOOT = font(18); F_EMPTY = font(28)

X_TIME, X_DOT, X_BADGE, X_EV, EV_W = 62, 172, 196, 292, 440
X_FC_R, X_PR_R = 900, 1030
ROW_H, ROW_H2, DAY_H = 52, 80, 58


def wrap(draw, text, fnt, width):
    """متن رو حداکثر در دو خط جا می‌ده؛ اگه باز جا نشد، با … کوتاه می‌کنه."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=fnt) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    lines.append(cur)
    lines = [l for l in lines if l]
    if len(lines) > 2:
        lines = lines[:2]
        while draw.textlength(lines[1] + "…", font=fnt) > width and lines[1]:
            lines[1] = lines[1][:-1]
        lines[1] += "…"
    return lines


def rrect(d, xy, fill, r=18, outline=None, w=1):
    d.rounded_rectangle(xy, radius=r, fill=fill, outline=outline, width=w)


def right(d, x, y, text, fnt, fill):
    d.text((x - d.textlength(text, font=fnt), y), text, font=fnt, fill=fill)


def paginate(events):
    """رویدادها رو بر اساس روز گروه می‌کنه و اگه زیاد بودن، در چند تصویر تقسیم می‌کنه."""
    days, cur = [], None
    for e in events:
        if e["when"].date() != cur:
            days.append([]); cur = e["when"].date()
        days[-1].append(e)
    pages, page, count = [], [], 0
    for day in days:
        if page and count + len(day) > MAX_ROWS_PER_IMAGE:
            pages.append(page); page, count = [], 0
        page.append(day); count += len(day)
    if page:
        pages.append(page)
    return pages or [[]]


def render(pages_idx, pages_total, days, tag, date_range, today, empty_text, now):
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    # ارتفاع رو اول حساب می‌کنیم
    h = 250 + 60
    for day in days:
        h += DAY_H + 8
        for e in day:
            h += ROW_H2 if len(wrap(probe, e["title"], F_EV, EV_W)) > 1 else ROW_H
        h += 14
    if not days:
        h += 170
    h += 190   # فوتر + فضای خالی پایین برای حباب ساعت تلگرام

    im = Image.new("RGB", (W, h), BG)
    d = ImageDraw.Draw(im)

    # سربرگ
    d.text((55, 42), "TRADINGLIFE", font=F_BRAND, fill=TEXT)
    d.text((55, 91), "Economic Calendar", font=F_SUB, fill=MUTED)
    right(d, 1025, 48, tag, F_TAG, BLUE)
    rng = date_range + (f"   ({pages_idx}/{pages_total})" if pages_total > 1 else "")
    right(d, 1025, 92, rng, F_RANGE, TEXT)

    # سرستون‌ها
    y = 150
    rrect(d, (50, y, 1030, y + 46), PANEL2, r=12)
    for x, t in ((X_TIME, "TIME ET"), (X_BADGE + 4, "CUR"), (X_EV, "EVENT")):
        d.text((x, y + 13), t, font=F_COLH, fill=MUTED)
    right(d, X_FC_R, y + 13, "FORECAST", F_COLH, MUTED)
    right(d, X_PR_R, y + 13, "PREVIOUS", F_COLH, MUTED)
    y += 70

    if not days:
        rrect(d, (50, y, 1030, y + 140), PANEL)
        tw = d.textlength(empty_text, font=F_EMPTY)
        d.text(((W - tw) / 2, y + 52), empty_text, font=F_EMPTY, fill=MUTED)
        y += 170

    for day in days:
        dt = day[0]["when"].date()
        label = (f"TONIGHT · {dt.strftime('%a').upper()} {dt.strftime('%b %d').upper()}"
                 if dt == today else dt.strftime("%A · %b %d").upper())
        rrect(d, (50, y, 1030, y + DAY_H), PANEL, r=14)
        d.rectangle((50, y + 12, 56, y + DAY_H - 12), fill=BLUE)
        d.text((74, y + 16), label, font=F_DAY, fill=TEXT)
        y += DAY_H + 8
        for i, e in enumerate(day):
            lines = wrap(d, e["title"], F_EV, EV_W)
            rh = ROW_H2 if len(lines) > 1 else ROW_H
            if i:
                d.line((70, y, 1010, y), fill=LINE, width=1)
            ty = y + 13
            if e["impact"] == "Holiday":
                d.text((X_TIME, ty), "ALL DAY", font=F_COLH, fill=AMBER)
                d.ellipse((X_DOT, ty + 5, X_DOT + 14, ty + 19), fill=AMBER)
            else:
                d.text((X_TIME, ty), e["when"].strftime("%H:%M"), font=F_TIME, fill=TEXT)
                c = RED if e["impact"] == "High" else AMBER
                d.ellipse((X_DOT, ty + 5, X_DOT + 14, ty + 19), fill=c)
            rrect(d, (X_BADGE, ty - 2, X_BADGE + 76, ty + 28), BADGE.get(e["cur"], PANEL), r=8)
            bw = d.textlength(e["cur"], font=F_BADGE)
            d.text((X_BADGE + (76 - bw) / 2, ty + 3), e["cur"], font=F_BADGE, fill=TEXT)
            for j, line in enumerate(lines):
                d.text((X_EV, ty + j * 30), line, font=F_EV,
                       fill=AMBER if e["impact"] == "Holiday" else TEXT)
            if e["impact"] == "Holiday":
                right(d, X_PR_R, ty + 2, "U.S. markets closed", F_NUM, AMBER)
            else:
                right(d, X_FC_R, ty + 1, e["forecast"] or "—", F_NUM, TEXT if e["forecast"] else MUTED)
                right(d, X_PR_R, ty + 1, e["previous"] or "—", F_NUM, MUTED)
            y += rh
        y += 14

    # فوتر
    y += 6
    rrect(d, (50, y, 1030, y + 128), PANEL2)
    lx = 75
    d.ellipse((lx, y + 26, lx + 14, y + 40), fill=RED); d.text((lx + 22, y + 22), "High impact", font=F_FOOT, fill=TEXT)
    lx = 250
    d.ellipse((lx, y + 26, lx + 14, y + 40), fill=AMBER); d.text((lx + 22, y + 22), "Medium impact", font=F_FOOT, fill=TEXT)
    right(d, 1005, y + 22, f"Updated {now.strftime('%Y-%m-%d %H:%M')} ET", F_FOOT, MUTED)
    d.text((75, y + 60), "Times in ET (New York) • Source: Forex Factory calendar", font=F_FOOT, fill=MUTED)
    d.text((75, y + 88), "Schedules and forecasts can change • Not investment advice", font=F_FOOT, fill=MUTED)

    os.makedirs("calendar_cards", exist_ok=True)
    path = f"calendar_cards/calendar_{MODE}_{pages_idx}.png"
    im.save(path, "PNG", optimize=True)
    return path


# ---------------------------------------------------------------- ارسال

# ---------------------------------------------------------------- متن زیر عکس

TAG_RULES = [(r"(^|\| )(Non-Farm Employment Change|Unemployment Rate|Average Hourly)", "#NFP"), (r"\bCPI\b", "#CPI"),
             (r"\bPCE\b", "#PCE"), (r"FOMC|Federal Funds|Fed Chair", "#FOMC"), (r"\bGDP\b", "#GDP"),
             (r"\bISM\b", "#ISM"), (r"Retail Sales", "#RetailSales"), (r"JOLTS", "#JOLTS"),
             (r"\bPPI\b", "#PPI")]


def short_time(e):
    return "All day" if e["impact"] == "Holiday" else e["when"].strftime("%a %H:%M" if MODE == "weekly" else "%H:%M") + " ET"


def build_caption(events, date_label, now):
    """متن زیر عکس — فقط از روی داده‌های تقویم (بدون هوش مصنوعی)."""
    usd_high = [e for e in events if e["cur"] == "USD" and e["impact"] == "High"]
    other_high = [e for e in events if e["cur"] != "USD" and e["impact"] == "High"]
    holidays = [e for e in events if e["impact"] == "Holiday"]

    if MODE == "weekly":
        lines = [f"🗓 Economic Calendar — Week Ahead ({date_label})", ""]
        intro = "Key U.S. releases this week:"
    else:
        lines = [f"🗓 Economic Calendar — Tomorrow ({date_label})", ""]
        intro = "Key U.S. releases:"

    if not events:
        lines.append("No high-impact events are scheduled — a quiet day on the calendar."
                     if MODE == "daily" else "No high-impact events are scheduled this week.")
    else:
        if usd_high:
            lines.append(intro)
            # مهم‌ترین‌ها اول انتخاب می‌شن (گزارش اشتغال، تورم، فدرال رزرو...)، بعد به ترتیب زمان نمایش
            prio = [r"^Non-Farm Employment Change$", r"^(Core )?CPI m/m", r"Federal Funds|FOMC Statement",
                    r"^Core PCE", r"GDP q/q", r"Retail Sales m/m", r"^ISM Manufacturing PMI", r"^ISM Services PMI",
                    r"Unemployment Rate", r"^(Core )?PPI m/m", r"JOLTS", r"Fed Chair"]
            rank = lambda e: next((i for i, pt in enumerate(prio) if re.search(pt, e["title"], re.I)), len(prio))
            top = sorted(sorted(usd_high, key=rank)[:6], key=lambda e: e["when"])
            for e in top:
                fc = f" (forecast {e['forecast']})" if e["forecast"] else ""
                lines.append(f"• {short_time(e)} — {e['title']}{fc}")
            if len(usd_high) > 6:
                lines.append(f"• …and {len(usd_high) - 6} more on the card")
        else:
            lines.append("No high-impact U.S. data is scheduled; see the card for other releases.")
        if other_high:
            lines += ["", "Also on watch:"]
            for e in other_high[:3]:
                lines.append(f"• {FLAGS.get(e['cur'], '')} {short_time(e)} — {e['title']}")
        for h in holidays[:1]:
            lines += ["", f"🏖 {h['title']} ({h['when'].strftime('%a')}) — U.S. markets closed or shortened."]

    lines += ["", "Times in ET. Forecasts from the Forex Factory calendar; schedules can change."]

    tags = ["#EconomicCalendar", "#USD"]
    titles = " | ".join(e["title"] for e in events if e["cur"] == "USD")
    for pat, tag in TAG_RULES:
        if re.search(pat, titles, re.I) and tag not in tags:
            tags.append(tag)
    lines += ["", " ".join(tags[:7])]
    txt = "\n".join(lines)
    if len(txt) > 1024:                                   # سقف کپشن تلگرام
        txt = txt[:1000].rsplit("\n", 1)[0] + "\n\n" + " ".join(tags[:7])
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


def main():
    now = datetime.now(ET)
    today = now.date()

    if MODE == "weekly":
        # از همین الان (یکشنبه عصر، شامل رویدادهای آسیا در شب یکشنبه) تا پایان جمعه
        wd = today.weekday()                     # دوشنبه=0 ... یکشنبه=6
        friday = today + timedelta(days=(4 - wd + 7) if wd >= 5 else (4 - wd))
        monday = friday - timedelta(days=4)
        end = datetime.combine(friday, datetime.max.time(), ET)
        events = load_window(now, end)
        tag = "WEEK AHEAD"
        date_range = f"{monday.strftime('%b %d')} – {friday.strftime('%b %d, %Y')}"
        empty = "No high-impact events scheduled this week."
    else:
        # از الان (بعد از بسته شدن بازار) تا پایان فردا — شامل رویدادهای آسیا/اروپا در شب
        tomorrow = today + timedelta(days=1)
        end = datetime.combine(tomorrow, datetime.max.time(), ET)
        events = load_window(now, end)
        tag = "TOMORROW"
        date_range = tomorrow.strftime("%A, %b %d, %Y")
        empty = "No high-impact events scheduled for tomorrow."

    pages = paginate(events)
    for i, days in enumerate(pages, 1):
        path = render(i, len(pages), days, tag, date_range, today, empty, now)
        # متن فقط زیر عکس اول (اگه کارت چند صفحه باشه)
        send_photo(path, build_caption(events, date_range, now) if i == 1 else None)
        print(f"ارسال شد: {path}")
        time.sleep(1)
    print(f"تمام ({MODE}): {len(events)} رویداد در {len(pages)} تصویر.")


if __name__ == "__main__":
    main()
