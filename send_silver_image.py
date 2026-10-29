"""
کارت تصویری «قیمت نقره (بازار جهانی)» برای کانال.

منبع قیمت: gold-api.com (رایگان، بدون کلید)؛ پشتیبان: Alpha Vantage فقط اگه کلیدش در workflow باشه.
تغییر روز/ماه/سال: از تاریخچه‌ی silver_history_series.json

ایمنی: قیمت نامعتبر یا پرش غیرعادی (بیش از ۱۵٪ نسبت به آخرین رکورد هفته‌ی اخیر)
→ عکسی نمی‌ره، چیزی در تاریخچه ذخیره نمی‌شه و اجرا قرمز می‌شه.
"""

import price_card_common as pc

HISTORY_FILE = "silver_history_series.json"
SILVER_COLOR = (200, 200, 210)


def build_image(price, day_ch, records, day, now):
    W, H = 900, 900
    img = pc.glow_background(W, H, [(210, 215, 225), (190, 195, 205), (210, 215, 225)], 0.18)
    d = pc.ImageDraw.Draw(img)
    F = pc.standard_fonts()
    m = 55
    y = 45
    d.text((W / 2, y), "قیمت نقره (بازار جهانی)", font=F["title"], fill=SILVER_COLOR,
           direction="rtl", anchor="ma")
    y += 100
    pairs = [("روز:", pc.change_text(day_ch, "$", 2)),
             ("ماه:", pc.change_text(pc.period_change(records, "price", price, day, 30), "$", 2)),
             ("سال:", pc.change_text(pc.period_change(records, "price", price, day, 365), "$", 2))]
    y = pc.price_card(d, W, m, y, "هر اونس نقره", "$" + pc.fmt(price, 2), pairs, F)
    y += 80
    pc.chart(d, m, y, W - 2 * m, 220, records, "price", SILVER_COLOR, F, lambda v: f"${v:,.1f}")
    y += 290
    d.text((W / 2, y), "بروزرسانی: " + pc.date_text(now) + " به وقت ایران",
           font=F["footer"], fill=pc.MUTED, direction="rtl", anchor="ma")
    img = img.crop((0, 0, W, min(y + 60, H)))
    img.save("silver_price.png")
    return "silver_price.png"


def main():
    now = pc.now_tehran()
    day = now.strftime("%Y-%m-%d")
    records = pc.load_history(HISTORY_FILE, multi=False)

    price = pc.world_metal("XAG", 5, 500)
    if not price or not pc.jump_ok(records, "price", price, day, 15):
        raise SystemExit("قیمت معتبر نقره دریافت نشد. عکسی ارسال نشد.")

    prev = pc.last_before(records, "price", day, 4)
    day_ch = (price - prev, (price / prev - 1) * 100) if prev else None
    records = pc.upsert(records, day, {"price": round(price, 2)})

    path = build_image(price, day_ch, records, day, now)
    pc.save_history(HISTORY_FILE, records, multi=False)

    cap = ["⚪ قیمت نقره (اونس جهانی)", "", f"هر اونس: ${pc.fmt(price, 2)}"]
    if pc.pct_caption(day_ch):
        cap.append(f"تغییر روزانه: {pc.pct_caption(day_ch)}")
    cap += ["", "#نقره #انس_نقره #فلزات_گرانبها"]
    pc.send_photo(path, "\n".join(cap))


if __name__ == "__main__":
    main()
