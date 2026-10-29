"""
کارت تصویری «قیمت بیت‌کوین (بازار جهانی)» برای کانال.

منبع قیمت: OKX (BTC-USDT، رایگان، بدون کلید)؛ پشتیبان: CoinGecko.
تغییر «۲۴ ساعت»: از خود منبع؛ «ماه» و «سال»: از تاریخچه‌ی btc_history_series.json

ایمنی: قیمت نامعتبر یا پرش غیرعادی (بیش از ۳۰٪ نسبت به آخرین رکورد هفته‌ی اخیر)
→ عکسی نمی‌ره، چیزی در تاریخچه ذخیره نمی‌شه و اجرا قرمز می‌شه.
"""

import requests
import price_card_common as pc

HISTORY_FILE = "btc_history_series.json"
BTC_COLOR = (247, 147, 26)


def get_btc():
    last, open24 = pc.okx_ticker("BTC-USDT")
    if last:
        print(f"OKX BTC: {last} (24h قبل: {open24})")
        return last, ((last - open24, (last / open24 - 1) * 100) if open24 else None)
    try:
        d = requests.get("https://api.coingecko.com/api/v3/simple/price",
                         params={"ids": "bitcoin", "vs_currencies": "usd", "include_24hr_change": "true"},
                         headers=pc.UA, timeout=20).json()["bitcoin"]
        last = pc.num(d.get("usd"))
        p = pc.signed(d.get("usd_24h_change"))
        print(f"CoinGecko BTC: {last} ({p}%)")
        if last and p is not None:
            return last, (last - last / (1 + p / 100), p)
        return last, None
    except Exception as e:
        print("CoinGecko BTC error:", e)
        return None, None


def build_image(price, day_ch, records, day, now):
    W, H = 900, 900
    img = pc.glow_background(W, H, [(247, 147, 26), (255, 190, 60), (247, 147, 26)], 0.20)
    d = pc.ImageDraw.Draw(img)
    F = pc.standard_fonts()
    m = 55
    y = 45
    d.text((W / 2, y), "قیمت بیت‌کوین (بازار جهانی)", font=F["title"], fill=BTC_COLOR,
           direction="rtl", anchor="ma")
    y += 100
    pairs = [("۲۴ ساعت:", pc.change_text(day_ch, "$")),
             ("ماه:", pc.change_text(pc.period_change(records, "price", price, day, 30), "$")),
             ("سال:", pc.change_text(pc.period_change(records, "price", price, day, 365), "$"))]
    y = pc.price_card(d, W, m, y, "هر بیت‌کوین", "$" + pc.fmt(price), pairs, F)
    y += 80
    pc.chart(d, m, y, W - 2 * m, 220, records, "price", BTC_COLOR, F, lambda v: f"${v / 1000:,.1f}K")
    y += 290
    d.text((W / 2, y), "بروزرسانی: " + pc.date_text(now) + " به وقت ایران",
           font=F["footer"], fill=pc.MUTED, direction="rtl", anchor="ma")
    img = img.crop((0, 0, W, min(y + 60, H)))
    img.save("btc_price.png")
    return "btc_price.png"


def main():
    now = pc.now_tehran()
    day = now.strftime("%Y-%m-%d")
    records = pc.load_history(HISTORY_FILE, multi=False)

    price, day_ch = get_btc()
    if not price or not 1_000 <= price <= 10_000_000 or not pc.jump_ok(records, "price", price, day, 30):
        raise SystemExit(f"قیمت معتبر بیت‌کوین دریافت نشد ({price}). عکسی ارسال نشد.")
    if day_ch and abs(day_ch[1]) > 40:
        day_ch = None
    records = pc.upsert(records, day, {"price": round(price)})

    path = build_image(price, day_ch, records, day, now)
    pc.save_history(HISTORY_FILE, records, multi=False)

    cap = ["₿ قیمت بیت‌کوین", "", f"هر بیت‌کوین: ${pc.fmt(price)}"]
    if pc.pct_caption(day_ch):
        cap.append(f"تغییر ۲۴ ساعته: {pc.pct_caption(day_ch)}")
    cap += ["", "#بیت_کوین #ارز_دیجیتال #BTC"]
    pc.send_photo(path, "\n".join(cap))


if __name__ == "__main__":
    main()
