"""
گزارش قیمت دلار، دلار کانادا، طلا، سکه و بیت‌کوین برای کانال تلگرام (فارسی).

منبع:
  - ارز، طلا و سکه: API نوسان (navasan.tech) — هر اجرا فقط «یک» درخواست (سهمیه: ۱۲۰ در ماه)
  - بیت‌کوین: OKX (رایگان، بدون کلید، از سهمیه‌ی نوسان کم نمی‌کنه)

برنامه: روزی ۴ بار با cron-job.org، شنبه تا پنجشنبه (جمعه بازار ایران تعطیله → پستی نمی‌ره).

ایمنی (اصلاح‌شده بعد از پست «0 تومان»):
  - اگه قیمت دلار دریافت نشه یا صفر/غیرعادی باشه، هیچ پستی نمی‌ره و اجرا قرمز می‌شه
  - هر قیمت نامعتبر (صفر، منفی، خالی) حذف می‌شه، نه اینکه «0» نمایش داده بشه
  - ترتیب منطقی قیمت سکه‌ها چک می‌شه (تمام > نیم > ربع)؛ اگه به‌هم ریخته بود، سکه‌ها نمایش داده نمی‌شن
  - جواب کامل API در لاگ چاپ می‌شه (بدون کلید) تا اگه ساختار عوض شد، سریع اصلاح بشه

پشتیبان: اگه نوسان جواب نده یا عدد نامعتبر بده، قیمت‌ها از nerkh.jahankhahan.shop
(رایگان، بدون کلید) گرفته می‌شه؛ اگه اون هم نبود، پستی نمی‌ره.

اشتراک با کارت تصویری طلا و سکه:
  - قیمت‌های تأییدشده در navasan_cache.json ذخیره و در ریپو commit می‌شن
  - send_gold_image.py از همین فایل می‌خونه → هیچ درخواست اضافه‌ای به نوسان زده نمی‌شه
"""

import os
import re
import json
import requests
import jdatetime
from datetime import datetime
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
NAVASAN_API_KEY = os.environ["NAVASAN_API_KEY"]

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
NAVASAN_URL = "http://api.navasan.tech/latest/"
OKX_TICKER = "https://www.okx.com/api/v5/market/ticker"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# (کلید داخلی، برچسب، اسم‌های احتمالی در جواب نوسان به ترتیب اولویت)
ITEMS = [
    ("usd", "💵 دلار آمریکا", ["usd_sell", "usd"]),
    ("cad", "💵 دلار کانادا", ["cad_sell", "cad"]),
    ("gold_18", "🥇 طلای ۱۸ عیار (گرم)", ["18ayar", "18_ayar"]),
    ("bahar", "🟡 سکه بهار آزادی", ["bahar"]),
    ("emami", "🟡 سکه امامی", ["sekkeh", "emami"]),
    ("nim", "🟡 نیم سکه", ["nim"]),
    ("rob", "🟡 ربع سکه", ["rob"]),
]

# نوسان قیمت سکه‌ها رو به «هزار تومان» می‌ده (مثلاً 268000 = ۲۶۸ میلیون تومان)؛ ارز و طلا به تومان‌ان
THOUSAND_TOMAN = {"bahar", "emami", "nim", "rob"}

# بازه‌ی منطقی قیمت دلار به تومان؛ بیرون از این بازه یعنی داده خرابه یا واحدش عوض شده
USD_MIN, USD_MAX = 10_000, 10_000_000


def to_number(v):
    if isinstance(v, dict):
        v = v.get("value")
    if v is None:
        return None
    s = re.sub(r"[^\d.]", "", str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
    try:
        x = float(s)
    except ValueError:
        return None
    return x if x > 0 else None


def get_navasan():
    r = requests.get(NAVASAN_URL, params={"api_key": NAVASAN_API_KEY}, headers=REQUEST_HEADERS, timeout=25)
    print(f"Navasan HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        raise SystemExit(f"جواب نوسان JSON نبود: {r.text[:300]!r}. پستی ارسال نشد.")
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"جواب نوسان خالی یا نامعتبر بود: {str(data)[:300]}. پستی ارسال نشد.")
    # برای عیب‌یابی: کلیدهای موجود و مقدار آیتم‌های مورد نیاز (کلید API چاپ نمی‌شه)
    print("کلیدهای جواب نوسان:", sorted(data.keys())[:80])
    for _, _, names in ITEMS:
        for n in names:
            if n in data:
                print(f"  {n}: {data[n]}")
    return data


def signed_number(v):
    """مقدار «change» نوسان (می‌تونه منفی باشه)."""
    if isinstance(v, dict):
        v = v.get("change")
    if v is None:
        return None
    s = str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).replace(",", "").replace("−", "-").strip()
    try:
        return float(s)
    except ValueError:
        return None


def save_cache(data, prices, iran_time):
    """
    قیمت‌های تأییدشده رو در navasan_cache.json ذخیره می‌کنه تا کارت تصویری طلا و سکه
    بدون درخواست اضافه به نوسان (و بدون مصرف سهمیه) ازشون استفاده کنه.
    فقط قیمت‌هایی که از بررسی sanity رد شدن ذخیره می‌شن (همه به تومان).
    """
    items = {}
    for key, _, names in ITEMS:
        price = prices.get(key)
        if not price:
            continue
        raw = next((data[n] for n in names if n in data and to_number(data[n])), None)
        change = signed_number(raw)
        if change is not None and key in THOUSAND_TOMAN:
            change *= 1000
        items[key] = {"price": round(price), "change": change}
    with open("navasan_cache.json", "w", encoding="utf-8") as f:
        json.dump({"fetched_at": iran_time.isoformat(timespec="seconds"), "items": items},
                  f, ensure_ascii=False, indent=2)
    print("navasan_cache.json ذخیره شد:", list(items))


NERKH_URL = "https://nerkh.jahankhahan.shop/data/live.json"
NERKH_KEYS = {"usd": "dollar", "cad": "cad", "gold_18": "gold", "bahar": "bahar",
              "emami": "coin", "nim": "half", "rob": "quarter"}


def get_nerkh():
    """پشتیبان رایگان (بدون کلید/سهمیه) وقتی نوسان جواب نده. همه‌ی مبالغ به تومان."""
    try:
        d = requests.get(NERKH_URL, headers=REQUEST_HEADERS, timeout=20).json()
        jy, jm, jd = (int(x) for x in str(d["date"]).split("/"))
        hh, mm = (int(x) for x in str(d.get("time") or "00:00").split(":")[:2])
        at = jdatetime.datetime(jy, jm, jd, hh, mm).togregorian().replace(tzinfo=ZoneInfo("Asia/Tehran"))
        age = (datetime.now(ZoneInfo("Asia/Tehran")) - at).total_seconds() / 3600
        print(f"nerkh: {d['date']} {d.get('time')} ({age:.1f} ساعت پیش)")
        if age > 30:
            raise SystemExit("داده‌ی nerkh هم قدیمی است. پستی ارسال نشد.")
        rates = d.get("rates") or {}
        return {k: to_number(rates.get(nk)) for k, nk in NERKH_KEYS.items()}
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"nerkh هم در دسترس نبود ({e}). پستی ارسال نشد.")


def get_btc():
    try:
        d = requests.get(OKX_TICKER, params={"instId": "BTC-USDT"}, headers=REQUEST_HEADERS, timeout=20).json()
        return to_number(d["data"][0]["last"])
    except Exception as e:
        print("BTC (OKX) error:", e)
        return None


def pick(data):
    out = {}
    for key, _, names in ITEMS:
        val = next((to_number(data[n]) for n in names if n in data and to_number(data[n])), None)
        if val and key in THOUSAND_TOMAN:
            val *= 1000
        out[key] = val
    return out


def sanity(p):
    """قیمت‌های غیرمنطقی رو حذف می‌کنه؛ اگه دلار نامعتبر باشه، کل گزارش متوقف می‌شه."""
    usd = p.get("usd")
    if not usd or not USD_MIN <= usd <= USD_MAX:
        raise SystemExit(f"قیمت دلار نامعتبر است ({usd}). پستی ارسال نشد.")
    coins = [p.get("emami") or p.get("bahar"), p.get("nim"), p.get("rob")]
    if all(coins) and not (coins[0] > coins[1] > coins[2]):
        print("ترتیب قیمت سکه‌ها منطقی نیست (تمام > نیم > ربع)؛ سکه‌ها نمایش داده نمی‌شن.", coins)
        for k in ("bahar", "emami", "nim", "rob"):
            p[k] = None
    if p.get("gold_18") and p.get("rob") and p["rob"] < p["gold_18"]:
        # ربع سکه حدود ۲ گرم طلای ۲۲ عیاره؛ اگه از یک گرم طلای ۱۸ ارزون‌تر باشه، واحدها به‌هم ریخته
        print("قیمت ربع سکه از یک گرم طلا کمتره؛ احتمالاً واحد سکه‌ها فرق داره. سکه‌ها نمایش داده نمی‌شن.")
        for k in ("bahar", "emami", "nim", "rob"):
            p[k] = None
    return p


def send_to_telegram(text):
    r = requests.post(TELEGRAM_API_URL, data={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=20)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال پیام: {res}")
        raise SystemExit(1)
    print(f"پیام با موفقیت ارسال شد - {datetime.now()}")


def main():
    iran_time = datetime.now(ZoneInfo("Asia/Tehran"))
    if iran_time.weekday() == 4:                                   # جمعه
        print("جمعه - بازار ایران تعطیله، پستی ارسال نمی‌شه (سهمیه‌ی نوسان مصرف نمی‌شه).")
        return
    if iran_time.hour >= 22 or iran_time.hour < 8:
        print(f"ساعت {iran_time.strftime('%H:%M')} به وقت ایران - داخل بازه‌ی سکوت، پیامی ارسال نمی‌شه.")
        return

    try:
        data = get_navasan()
        prices = sanity(pick(data))
        save_cache(data, prices, iran_time)     # فقط داده‌ی نوسان ذخیره می‌شه (برای کنترل متقابل)
    except SystemExit as e:
        print(f"نوسان: {e} → استفاده از منبع پشتیبان nerkh")
        prices = sanity(get_nerkh())
    btc = get_btc()

    shamsi = jdatetime.datetime.fromgregorian(datetime=iran_time).strftime("%Y/%m/%d %H:%M")
    lines = ["📊 <b>گزارش نرخ ارز و طلا</b>", ""]
    for key, label, _ in ITEMS:
        v = prices.get(key)
        if v:                                                      # قیمت نامعتبر نمایش داده نمی‌شه
            lines += [f"{label}: {int(round(v)):,} تومان", ""]
    if btc:
        lines += [f"₿ بیت‌کوین: {btc:,.0f} دلار", ""]
    lines += [f"🕐 {shamsi}", "", "#دلار #طلا #سکه #قیمت_ارز"]
    send_to_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
