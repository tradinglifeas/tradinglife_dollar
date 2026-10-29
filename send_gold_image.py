"""
کارت تصویری «قیمت طلا و سکه» (بازار ایران + اونس جهانی) برای کانال.

منابع:
  - طلا و سکه‌ی ایران: فایل navasan_cache.json که گزارش قیمت (send_price.py) هر بار
    از API نوسان می‌سازه → این کارت «هیچ» درخواست اضافه‌ای به نوسان نمی‌زنه (سهمیه‌ی ۱۲۰ در ماه)
  - اونس جهانی طلا: gold-api.com (رایگان، بدون کلید) + کنترل متقابل با XAUT در OKX
  - تغییر «روز» طلا و سکه: از خود نوسان؛ «ماه» و «سال»: از تاریخچه‌ی gold_history_series.json

ایمنی:
  - اگه فایل نوسان قدیمی‌تر از ۶ ساعت یا خراب باشه → عکسی نمی‌ره و اجرا قرمز می‌شه
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


def load_iran(now):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        raise SystemExit("فایل navasan_cache.json پیدا نشد یا خراب است. "
                         "اول گزارش قیمت (send_price) باید اجرا بشه. عکسی ارسال نشد.")
    try:
        fetched = datetime.fromisoformat(cache["fetched_at"]).astimezone(pc.TEHRAN)
    except Exception:
        raise SystemExit("زمان فایل نوسان نامعتبر است. عکسی ارسال نشد.")
    age_h = (now - fetched).total_seconds() / 3600
    print(f"فایل نوسان: {fetched:%Y-%m-%d %H:%M} تهران ({age_h:.1f} ساعت پیش)")
    if age_h > MAX_CACHE_AGE_HOURS or age_h < -0.2:
        raise SystemExit(f"قیمت‌های نوسان قدیمی است ({age_h:.1f} ساعت). "
                         "این کارت باید کمی بعد از گزارش قیمت اجرا بشه. عکسی ارسال نشد.")

    items = cache.get("items", {})
    out = {}
    for key, ckey, _ in ROWS:
        it = items.get(ckey) or {}
        price = pc.num(it.get("price"))
        ch = None
        change = pc.signed(it.get("change"))
        if price and change is not None and price - change > 0:
            p = change / (price - change) * 100
            if abs(p) <= 20:
                ch = (change, p)
        if price:
            out[key] = (price, ch)

    g18 = out.get("gold_18", (None,))[0]
    if not g18 or not 1_000_000 <= g18 <= 1_000_000_000:
        raise SystemExit(f"قیمت طلای ۱۸ عیار نامعتبر است ({g18}). عکسی ارسال نشد.")
    full = (out.get("emami") or out.get("bahar_azadi") or (None,))[0]
    half, quarter = out.get("half_coin", (None,))[0], out.get("quarter_coin", (None,))[0]
    bad = (full and half and quarter and not full > half > quarter) or (quarter and quarter < g18)
    if bad:
        print("ترتیب قیمت سکه‌ها منطقی نیست؛ سکه‌ها نمایش داده نمی‌شن.")
        for k in COINS:
            out.pop(k, None)
    return out


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
