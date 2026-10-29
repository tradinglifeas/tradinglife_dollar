"""
ابزار مشترک کارت‌های تصویری «طلا و سکه»، «نقره» و «بیت‌کوین».

قانون‌های ایمنی (بعد از پست «نامشخص / ‎-100%»):
  - هیچ قیمت صفر، منفی، خالی یا غیرعادی در تاریخچه ذخیره نمی‌شه
  - موقع خوندن تاریخچه، رکوردهای خراب قدیمی خودکار حذف می‌شن
  - اگه قیمت اصلی کارت معتبر نباشه، هیچ عکسی به کانال نمی‌ره و اجرا قرمز می‌شه
  - پرش ناگهانی قیمت نسبت به آخرین رکورد (چند روز اخیر) = داده‌ی خراب → منتشر نمی‌شه
"""

import os
import re
import json
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont, ImageFilter, features

TEHRAN = ZoneInfo("Asia/Tehran")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "application/json"}

UP = (80, 200, 120)
DOWN = (230, 90, 90)
NEUTRAL = (170, 170, 180)
BG = (18, 18, 24)
CARD = (30, 30, 40)
TEXT = (240, 240, 240)
MUTED = (170, 170, 180)
GRID = (55, 55, 68)

MAX_HISTORY_DAYS = 400
FONT_URL = "https://raw.githubusercontent.com/rastikerdar/vazirmatn/master/fonts/ttf/Vazirmatn-{w}.ttf"


# ============================================================ اعداد

def num(v):
    """عدد مثبت یا None (رشته، کاما و رقم فارسی رو هم می‌فهمه)."""
    if isinstance(v, dict):
        v = v.get("value", v.get("price"))
    if v is None or isinstance(v, bool):
        return None
    s = str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "").strip()
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 0 and x == x and x != float("inf") else None


def signed(v):
    """عدد با علامت (برای مقدار تغییر) یا None."""
    if v is None or isinstance(v, bool):
        return None
    s = str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "").strip()
    s = s.replace("−", "-")
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


def fmt(v, decimals=0):
    return f"{v:,.{decimals}f}"


def now_tehran():
    return datetime.now(TEHRAN)


def date_text(dt):
    """تاریخ شمسی اگه jdatetime نصب باشه، وگرنه میلادی."""
    try:
        import jdatetime
        return jdatetime.datetime.fromgregorian(datetime=dt).strftime("%Y/%m/%d - %H:%M")
    except Exception:
        return dt.strftime("%Y/%m/%d - %H:%M")


# ============================================================ تاریخچه
# فرمت فایل‌ها همون فرمت قبلیه:
#   طلا:   [{"date": "...", "prices": {"gold_18": ..., ...}}]
#   نقره/بیت‌کوین: [{"date": "...", "price": ...}]
# داخل برنامه همه‌چیز به شکل {"date": ..., "v": {key: value}} نگه داشته می‌شه.

def load_history(path, multi):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out, dropped = {}, 0
    for rec in raw if isinstance(raw, list) else []:
        d = str(rec.get("date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            dropped += 1
            continue
        src = rec.get("prices", {}) if multi else {"price": rec.get("price")}
        vals = {k: num(v) for k, v in (src or {}).items()}
        bad = [k for k, v in vals.items() if v is None]
        dropped += len(bad)
        vals = {k: v for k, v in vals.items() if v is not None}
        if vals:
            out[d] = vals
    if dropped:
        print(f"تاریخچه {path}: {dropped} مقدار خراب (صفر/خالی) حذف شد.")
    return [{"date": d, "v": out[d]} for d in sorted(out)]


def save_history(path, records, multi):
    recs = [{"date": r["date"], "v": {k: int(v) if float(v).is_integer() else v for k, v in r["v"].items()}}
            for r in records[-MAX_HISTORY_DAYS:]]
    if multi:
        data = [{"date": r["date"], "prices": r["v"]} for r in recs]
    else:
        data = [{"date": r["date"], "price": r["v"]["price"]} for r in recs if "price" in r["v"]]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def upsert(records, day, values):
    """فقط مقدارهای معتبر ثبت می‌شن؛ مقدار قبلی همون روز برای کلیدهای غایب حفظ می‌شه."""
    values = {k: v for k, v in values.items() if num(v) is not None}
    if not values:
        return records
    for r in records:
        if r["date"] == day:
            r["v"].update(values)
            return records
    records.append({"date": day, "v": dict(values)})
    records.sort(key=lambda r: r["date"])
    return records


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def last_before(records, key, day, max_days):
    """آخرین مقدار معتبر قبل از امروز (حداکثر max_days روز قبل)."""
    today = _d(day)
    for r in reversed(records):
        dd = _d(r["date"])
        if dd < today and key in r["v"]:
            return r["v"][key] if (today - dd).days <= max_days else None
    return None


def value_near(records, key, target, tolerance=5):
    best, best_gap = None, None
    for r in records:
        if key not in r["v"]:
            continue
        gap = abs((_d(r["date"]) - target).days)
        if gap <= tolerance and (best_gap is None or gap < best_gap):
            best, best_gap = r["v"][key], gap
    return best


def period_change(records, key, price, day, days_ago):
    if not price:
        return None
    old = value_near(records, key, _d(day) - timedelta(days=days_ago))
    if not old:
        return None
    return price - old, (price / old - 1) * 100


def jump_ok(records, key, price, day, limit_pct, within_days=7):
    prev = last_before(records, key, day, within_days)
    if prev and abs(price / prev - 1) * 100 > limit_pct:
        print(f"{key}: پرش غیرعادی {price} در برابر {prev} (بیش از {limit_pct}%) — داده رد شد.")
        return False
    return True


# ============================================================ منابع جهانی (بدون کلید)

def gold_api(symbol):
    """قیمت لحظه‌ای فلز از gold-api.com (رایگان، بدون کلید). XAU یا XAG."""
    try:
        r = requests.get(f"https://api.gold-api.com/price/{symbol}", headers=UA, timeout=20)
        d = r.json()
        price = num(d.get("price"))
        upd = d.get("updatedAt")
        if price and upd:
            t = datetime.fromisoformat(str(upd).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - t > timedelta(days=4):
                print(f"gold-api {symbol}: قیمت قدیمیه ({upd}) — رد شد.")
                return None
        print(f"gold-api {symbol}: {price} ({upd})")
        return price
    except Exception as e:
        print(f"gold-api {symbol} error: {e}")
        return None


def alpha_vantage_spot(symbol):
    """پشتیبان: Alpha Vantage (فقط اگه کلیدش در workflow تعریف شده باشه). GOLD یا SILVER."""
    key = os.environ.get("ALPHA_VANTAGE_METALS_KEY") or os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return None
    try:
        d = requests.get("https://www.alphavantage.co/query",
                         params={"function": "GOLD_SILVER_SPOT", "symbol": symbol, "apikey": key},
                         headers=UA, timeout=30).json()
        for k in ("price", "Price", "spot_price", "Spot Price"):
            if num(d.get(k)):
                return num(d[k])
        for v in d.values():
            if isinstance(v, dict):
                for k, x in v.items():
                    if "price" in k.lower() and num(x):
                        return num(x)
        print(f"Alpha Vantage {symbol}: قیمت پیدا نشد.")
    except Exception as e:
        print(f"Alpha Vantage {symbol} error: {str(e).replace(key, '***')}")
    return None


def okx_ticker(inst):
    """(قیمت فعلی، قیمت ۲۴ ساعت قبل) از OKX."""
    try:
        d = requests.get("https://www.okx.com/api/v5/market/ticker", params={"instId": inst},
                         headers=UA, timeout=20).json()
        t = d["data"][0]
        return num(t.get("last")), num(t.get("open24h"))
    except Exception as e:
        print(f"OKX {inst} error: {e}")
        return None, None


def world_metal(symbol, lo, hi):
    """قیمت اونس طلا (XAU) یا نقره (XAG) با بررسی صحت."""
    price = gold_api(symbol) or alpha_vantage_spot("GOLD" if symbol == "XAU" else "SILVER")
    if price and not lo <= price <= hi:
        print(f"{symbol}: قیمت {price} بیرون از بازه‌ی منطقی {lo}–{hi} — رد شد.")
        price = None
    if price and symbol == "XAU":
        # کنترل متقابل با توکن طلای XAUT در OKX (هر توکن = یک اونس طلا)
        xaut, _ = okx_ticker("XAUT-USDT")
        if xaut and abs(price / xaut - 1) > 0.03:
            print(f"XAU: اختلاف زیاد با XAUT ({price} در برابر {xaut}) — رد شد.")
            price = None
    return price


# ============================================================ فونت و رسم

def font(size, bold=False):
    if not features.check("raqm"):
        raise SystemExit("موتور raqm در Pillow نیست؛ متن فارسی درست رسم نمی‌شه. عکسی ارسال نشد.")
    w = "Bold" if bold else "Regular"
    for p in (f"Vazirmatn-{w}.ttf", f"/tmp/Vazirmatn-{w}.ttf"):
        if os.path.exists(p) and os.path.getsize(p) > 50000:
            return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)
    r = requests.get(FONT_URL.format(w=w), timeout=30)
    r.raise_for_status()
    p = f"/tmp/Vazirmatn-{w}.ttf"
    with open(p, "wb") as f:
        f.write(r.content)
    return ImageFont.truetype(p, size, layout_engine=ImageFont.Layout.RAQM)


def glow_background(w, h, colors, alpha):
    layer = Image.new("RGB", (w, h), BG)
    g = ImageDraw.Draw(layer)
    for (fx, fy, r), c in zip(((0.15, 0.04, 260), (0.92, 0.12, 220), (0.08, 0.55, 200)), colors):
        cx, cy = w * fx, h * fy
        g.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    layer = layer.filter(ImageFilter.GaussianBlur(120))
    return Image.blend(Image.new("RGB", (w, h), BG), layer, alpha=alpha)


def width_of(draw, text, f, rtl=False):
    if not rtl:
        text = "\u202a" + text + "\u202c"
    b = draw.textbbox((0, 0), text, font=f, direction="rtl" if rtl else "ltr")
    return b[2] - b[0]


def rtl(draw, x_right, y, text, f, fill):
    draw.text((x_right, y), text, font=f, fill=fill, direction="rtl", anchor="ra")


def ltr(draw, x_left, y, text, f, fill):
    # LRE ... PDF: ترتیب «+$750» و «%» رو در موتور raqm ثابت نگه می‌داره
    draw.text((x_left, y), "\u202a" + text + "\u202c", font=f, fill=fill, direction="ltr", anchor="la")


def change_text(ch, prefix="", decimals=0):
    """(متن، رنگ) برای یک تغییر؛ اگه داده نباشه «—»."""
    if not ch:
        return "—", NEUTRAL
    diff, p = ch
    if abs(p) < 0.05:
        return "0.0%", NEUTRAL
    sign = "+" if p > 0 else "-"
    color = UP if p > 0 else DOWN
    return f"{sign}{abs(p):.1f}% ({sign}{prefix}{fmt(abs(diff), decimals)})", color


def changes_row(draw, x_right, y, pairs, f):
    """ردیف «روز / ماه / سال» از راست به چپ: برچسب فارسی، بعد عدد (چپ‌به‌راست)."""
    x = x_right
    for label, (text, color) in pairs:
        lw = width_of(draw, label, f, rtl=True)
        rtl(draw, x, y, label, f, MUTED)
        x -= lw + 8
        tw = width_of(draw, text, f)
        ltr(draw, x - tw, y, text, f, color)
        x -= tw + 30


def price_card(draw, W, margin, y, label, value, pairs, fonts, h=120):
    draw.rounded_rectangle([margin, y, W - margin, y + h], radius=16, fill=CARD)
    rtl(draw, W - margin - 30, y + 16, label, fonts["label"], MUTED)
    ltr(draw, margin + 30, y + 14, value, fonts["value"], TEXT)
    changes_row(draw, W - margin - 30, y + 68, pairs, fonts["small"])
    return y + h + 14


def chart(draw, x, y, w, h, records, key, color, fonts, value_fmt):
    """نمودار روند؛ محور افقی بر اساس تاریخ (فاصله‌ی روزهای بدون داده واقعی نشون داده می‌شه)."""
    pts = [(_d(r["date"]), r["v"][key]) for r in records if key in r["v"]][-120:]
    rtl(draw, x + w, y - 42, "روند قیمت (روزهای ثبت‌شده)", fonts["small"], MUTED)
    if len(pts) < 2:
        draw.text((x + w / 2, y + h / 2), "هنوز داده‌ی کافی برای نمودار نداریم",
                  font=fonts["small"], fill=MUTED, direction="rtl", anchor="mm")
        return
    d0, d1 = pts[0][0], pts[-1][0]
    span = max((d1 - d0).days, 1)
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    if hi == lo:
        hi = lo * 1.001 + 1e-9
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    plot_x, plot_w = x + 110, w - 110
    for i in range(3):
        gy = y + h * i / 2
        draw.line([(plot_x, gy), (plot_x + plot_w, gy)], fill=GRID, width=1)
        val = hi - (hi - lo) * i / 2
        tw = width_of(draw, value_fmt(val), fonts["tiny"])
        ltr(draw, plot_x - 12 - tw, gy - 11, value_fmt(val), fonts["tiny"], MUTED)
    coords = [(plot_x + (d - d0).days / span * plot_w, y + h - (p - lo) / (hi - lo) * h) for d, p in pts]
    # روزهای پشت‌سرهم: خط پر؛ فاصله‌ی بیش از ۵ روز بدون داده: خط‌چین کم‌رنگ
    seg = [coords[0]]
    for i in range(1, len(coords)):
        if (pts[i][0] - pts[i - 1][0]).days > 5:
            if len(seg) > 1:
                draw.line(seg, fill=color, width=3, joint="curve")
            (ax, ay), (bx, by) = coords[i - 1], coords[i]
            n = max(int(((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 // 12), 1)
            for k in range(0, n, 2):
                t0, t1 = k / n, min((k + 1) / n, 1)
                draw.line([(ax + (bx - ax) * t0, ay + (by - ay) * t0),
                           (ax + (bx - ax) * t1, ay + (by - ay) * t1)], fill=MUTED, width=2)
            seg = [coords[i]]
        else:
            seg.append(coords[i])
    if len(seg) > 1:
        draw.line(seg, fill=color, width=3, joint="curve")
    for cx, cy in (coords[0], coords[-1]):
        draw.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=color)
    ltr(draw, plot_x, y + h + 8, d0.strftime("%Y-%m-%d"), fonts["tiny"], MUTED)
    s = d1.strftime("%Y-%m-%d")
    ltr(draw, plot_x + plot_w - width_of(draw, s, fonts["tiny"]), y + h + 8, s, fonts["tiny"], MUTED)


def standard_fonts():
    return {"title": font(46, True), "section": font(30, True), "label": font(28),
            "value": font(32, True), "small": font(21), "tiny": font(18), "footer": font(22)}


# ============================================================ تلگرام

def send_photo(path, caption):
    token, chat = os.environ["BOT_TOKEN"], os.environ["CHAT_ID"]
    with open(path, "rb") as ph:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                          data={"chat_id": chat, "caption": caption}, files={"photo": ph}, timeout=60)
    res = r.json()
    if not res.get("ok"):
        print("خطا در ارسال عکس:", res)
        raise SystemExit(1)
    print("عکس با موفقیت ارسال شد.")


def pct_caption(ch):
    """درصد برای کپشن با نشانه‌ی جهت چپ‌به‌راست تا در متن فارسی جابه‌جا نشه."""
    if not ch:
        return None
    p = ch[1]
    return "‎" + (f"+{p:.1f}%" if p > 0 else f"{p:.1f}%")
