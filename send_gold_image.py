"""
کارت تصویری «قیمت طلا و سکه» (بازار ایران + اونس جهانی) برای کانال.

منابع:
  - طلا و سکه‌ی ایران: nerkh.jahankhahan.shop (رایگان، بدون کلید و سهمیه)،
    کنترل‌شده با آخرین قیمت نوسان (navasan_cache.json که گزارش قیمت می‌سازه)؛
    اگه nerkh در دسترس نبود → همون فایل نوسان. هیچ درخواست اضافه‌ای به نوسان زده نمی‌شه.
  - اونس جهانی طلا: gold-api.com و nerkh (دو منبع؛ اختلاف بیش از ۳٪ = رد)
  - تغییر «روز»: از خود منبع؛ «ماه» و «سال»: از تاریخچه‌ی gold_history_series.json

ایمنی:
  - اگه هیچ منبع معتبری نباشه → عکسی نمی‌ره و اجرا قرمز می‌شه
  - جمعه (بازار ایران تعطیل) → بی‌صدا رد می‌شه
  - قیمت نامعتبر هیچ‌وقت نمایش داده یا در تاریخچه ذخیره نمی‌شه
"""

import json
from datetime import datetime

import price_card_common as pc

HISTORY_FILE = "gold_history_series.json"
CACHE_FILE = "navasan_cache.json"
MAX_CACHE_AGE_HOURS = 6
GOLD_COLOR = (255, 205, 86)

# (کلید تاریخچه، کلید فایل نوسان، برچسب)
ROWS = [
    ("gold_18", "gold_18", "طلای ۱۸ عیار (هر گرم)"),
    ("bahar_azadi", "bahar", "سکه بهار آزادی"),
    ("emami", "emami", "سکه امامی"),
    ("half_coin", "nim", "نیم سکه"),
    ("quarter_coin", "rob", "ربع سکه"),
]
COINS = ["bahar_azadi", "emami", "half_coin", "quarter_coin"]


# کلیدهای nerkh برای هر ردیف
NERKH_KEYS = {"gold_18": "gold", "bahar_azadi": "bahar", "emami": "coin",
              "half_coin": "half", "quarter_coin": "quarter"}


def read_navasan_cache(now, max_age_h):
    """قیمت‌های نوسان از فایلی که گزارش قیمت می‌سازه (اگه به اندازه‌ی کافی تازه باشه)."""
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        fetched = datetime.fromisoformat(cache["fetched_at"]).astimezone(pc.TEHRAN)
    except Exception:
        print("فایل navasan_cache.json پیدا نشد یا خراب است.")
        return None
    age_h = (now - fetched).total_seconds() / 3600
    if age_h > max_age_h or age_h < -0.2:
        print(f"فایل نوسان {age_h:.1f} ساعت قدیمیه (حداکثر {max_age_h}).")
        return None
    out = {}
    items = cache.get("items", {})
    for key, ckey, _ in ROWS:
        it = items.get(ckey) or {}
        price = pc.num(it.get("price"))
        change = pc.signed(it.get("change"))
        ch = None
        if price and change is not None and price - change > 0:
            ch = pc.change_from_pct(price, change / (price - change) * 100)
        if price:
            out[key] = (price, ch)
    return out


def from_nerkh():
    n = pc.nerkh_live()
    if not n:
        return None
    out = {}
    for key, nk in NERKH_KEYS.items():
        price = n["rates"].get(nk)
        if price:
            out[key] = (price, pc.change_from_pct(price, n["change"].get(nk)))
    return out


def check(out):
    """بررسی منطقی؛ اگه طلای ۱۸ نامعتبر باشه None، اگه سکه‌ها به‌هم‌ریخته باشن حذفشون می‌کنه."""
    if not out:
        return None
    g18 = out.get("gold_18", (None,))[0]
    if not g18 or not 1_000_000 <= g18 <= 1_000_000_000:
        print(f"قیمت طلای ۱۸ عیار نامعتبر است ({g18}).")
        return None
    full = (out.get("emami") or out.get("bahar_azadi") or (None,))[0]
    half, quarter = out.get("half_coin", (None,))[0], out.get("quarter_coin", (None,))[0]
    if (full and half and quarter and not full > half > quarter) or (quarter and quarter < g18):
        print("ترتیب قیمت سکه‌ها منطقی نیست؛ سکه‌ها نمایش داده نمی‌شن.")
        for k in COINS:
            out.pop(k, None)
    return out


def load_iran(now):
    """
    منبع اصلی: nerkh (رایگان، بدون سهمیه، لحظه‌ای).
    کنترل: اگه قیمت نوسانِ ۲۴ ساعت اخیر هست، طلای ۱۸ دو منبع نباید بیش از ۳٪ فرق داشته باشه.
    پشتیبان: اگه nerkh در دسترس/معتبر نبود → فایل نوسان (حداکثر ۶ ساعت).
    """
    nerkh = check(from_nerkh())
    navasan = check(read_navasan_cache(now, 24))
    if nerkh:
        if navasan and not pc.agree(nerkh["gold_18"][0], navasan["gold_18"][0]):
            print(f"اختلاف زیاد nerkh و نوسان: {nerkh['gold_18'][0]} در برابر {navasan['gold_18'][0]} — nerkh رد شد.")
        else:
            print("قیمت ایران از nerkh" + (" (تأییدشده با نوسان)" if navasan else ""))
            return nerkh
    fresh = check(read_navasan_cache(now, MAX_CACHE_AGE_HOURS))
    if fresh:
        print("قیمت ایران از فایل نوسان (پشتیبان)")
        return fresh
    raise SystemExit("هیچ منبع معتبری برای قیمت طلا و سکه‌ی ایران نبود. عکسی ارسال نشد.")


def build_image(iran, ounce, ounce_ch, records, day, now):
    W, H = 950, 1700
    img = pc.glow_background(W, H, [(255, 205, 86), (255, 180, 60), (255, 205, 86)], 0.22)
    d = pc.ImageDraw.Draw(img)
    F = pc.standard_fonts()
    m = 55
    y = 40
    d.text((W / 2, y), "قیمت طلا و سکه", font=F["title"], fill=GOLD_COLOR, direction="rtl", anchor="ma")
    y += 80
    pc.rtl(d, W - m, y, "بازار ایران (تومان)", F["section"], pc.MUTED)
    y += 50
    for key, _, label in ROWS:
        if key not in iran:
            continue
        price, day_ch = iran[key]
        pairs = [("روز:", pc.change_text(day_ch)),
                 ("ماه:", pc.change_text(pc.period_change(records, key, price, day, 30))),
                 ("سال:", pc.change_text(pc.period_change(records, key, price, day, 365)))]
        y = pc.price_card(d, W, m, y, label, pc.fmt(price), pairs, F)

    if ounce:
        y += 12
        pc.rtl(d, W - m, y, "بازار جهانی (دلار)", F["section"], pc.MUTED)
        y += 50
        pairs = [("روز:", pc.change_text(ounce_ch, "$")),
                 ("ماه:", pc.change_text(pc.period_change(records, "global_gold_ounce", ounce, day, 30), "$")),
                 ("سال:", pc.change_text(pc.period_change(records, "global_gold_ounce", ounce, day, 365), "$"))]
        y = pc.price_card(d, W, m, y, "هر اونس طلا", "$" + pc.fmt(ounce), pairs, F)

    y += 70
    pc.chart(d, m, y, W - 2 * m, 200, records, "gold_18", GOLD_COLOR, F,
             lambda v: f"{v / 1_000_000:,.1f}M")
    pc.rtl(d, W - m, y + 236, "نمودار: طلای ۱۸ عیار (تومان، M = میلیون)", F["tiny"], pc.MUTED)
    y += 290
    d.text((W / 2, y), "بروزرسانی: " + pc.date_text(now) + " به وقت ایران",
           font=F["footer"], fill=pc.MUTED, direction="rtl", anchor="ma")
    img = img.crop((0, 0, W, min(y + 60, H)))
    img.save("gold_price.png")
    return "gold_price.png"


def caption(iran, ounce, ounce_ch):
    g18, ch = iran["gold_18"]
    lines = ["🟡 قیمت طلا و سکه", ""]
    t = f"طلای ۱۸ عیار: {pc.fmt(g18)} تومان"
    if pc.pct_caption(ch):
        t += f" ({pc.pct_caption(ch)} امروز)"
    lines.append(t)
    coin = iran.get("emami")
    if coin:
        t = f"سکه امامی: {pc.fmt(coin[0])} تومان"
        if pc.pct_caption(coin[1]):
            t += f" ({pc.pct_caption(coin[1])} امروز)"
        lines.append(t)
    if ounce:
        t = f"اونس جهانی طلا: ${pc.fmt(ounce)}"
        if pc.pct_caption(ounce_ch):
            t += f" ({pc.pct_caption(ounce_ch)})"
        lines.append(t)
    lines += ["", "#طلا #سکه #طلای_۱۸_عیار #انس_طلا"]
    return "\n".join(lines)


def main():
    now = pc.now_tehran()
    if now.weekday() == 4:
        print("جمعه — بازار طلا و سکه‌ی ایران تعطیله؛ کارتی ارسال نمی‌شه.")
        return
    day = now.strftime("%Y-%m-%d")

    records = pc.load_history(HISTORY_FILE, multi=True)
    iran = load_iran(now)

    ounce = pc.world_metal("XAU", 500, 20000)
    if ounce and not pc.jump_ok(records, "global_gold_ounce", ounce, day, 15):
        ounce = None
    if not ounce:
        print("اونس جهانی طلا در دسترس نبود؛ کارت بدون بخش جهانی ساخته می‌شه.")
    prev = pc.last_before(records, "global_gold_ounce", day, 5) if ounce else None
    ounce_ch = (ounce - prev, (ounce / prev - 1) * 100) if ounce and prev else None

    values = {k: v[0] for k, v in iran.items()}
    if ounce:
        values["global_gold_ounce"] = ounce
    records = pc.upsert(records, day, values)

    path = build_image(iran, ounce, ounce_ch, records, day, now)
    pc.save_history(HISTORY_FILE, records, multi=True)
    pc.send_photo(path, caption(iran, ounce, ounce_ch))


if __name__ == "__main__":
    main()
