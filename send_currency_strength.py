"""
اسکریپت ساخت و ارسال گزارش «قدرت ارزهای G10» به کانال تلگرام (انگلیسی).

داده‌ی عددی: Frankfurter API (رایگان، بدون کلید) = نرخ‌های مرجع رسمی بانک مرکزی اروپا (ECB).
  - ECB هر روز کاری حدود ساعت 14:15 به وقت اروپای مرکزی نرخ‌ها رو تثبیت می‌کنه.
  - آخر هفته و تعطیلات نرخ جدید منتشر نمی‌شه.
  - برای همین همیشه «دو تاریخ آخری که واقعاً منتشر شدن» مقایسه می‌شن و روی پست نوشته می‌شن.

بخش «چرا»: Groq فقط دسته‌بندی + لینک منبع انتخاب می‌کنه؛ متن رو خود کد با قالب ثابت می‌سازه.

ارزها: ۹ ارز G10 در برابر دلار آمریکا (USD دهمین ارز G10 است).
"""

import os
import re
import math
import time
import html
import requests
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
FRANKFURTER_URLS = ["https://api.frankfurter.app", "https://api.frankfurter.dev/v1"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ۹ ارز G10 به‌جز خود دلار
G10_CURRENCIES = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "NOK", "SEK"]

# اگه آخرین نرخ منتشرشده قدیمی‌تر از این باشه، پست نمی‌ره (داده‌ی کهنه)
MAX_DATA_AGE_DAYS = 4

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


# ---------------------------------------------------------------- داده‌ی نرخ‌ها

def get_rate_history(days_back=12):
    """
    نرخ‌های اصلی ECB (بر پایه‌ی یورو) رو می‌گیره و خودمون به «واحد ارز برای هر دلار» تبدیل می‌کنیم.
    این‌طوری گرد کردن میانی Frankfurter حذف می‌شه و عددها دقیقاً از نرخ رسمی ECB میان.
    خروجی: لیست (تاریخ، نرخ‌ها) از قدیم به جدید.
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    start = today - timedelta(days=days_back)
    others = [c for c in G10_CURRENCIES if c != "EUR"]
    params = {"from": "EUR", "to": ",".join(["USD"] + others)}
    last_error = None
    for base in FRANKFURTER_URLS:
        try:
            url = f"{base}/{start.isoformat()}..{today.isoformat()}"
            response = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=20)
            response.raise_for_status()
            data = response.json()
            rows = []
            for day, r in sorted(data.get("rates", {}).items()):
                if not all(c in r and float(r[c]) > 0 for c in ["USD"] + others):
                    continue
                usd = float(r["USD"])                       # دلار برای هر یورو
                per_usd = {"EUR": 1 / usd}                   # یورو برای هر دلار
                for c in others:
                    per_usd[c] = float(r[c]) / usd           # ارز برای هر دلار
                rows.append((datetime.strptime(day, "%Y-%m-%d").date(), per_usd))
            print(f"Frankfurter ({base}): {len(rows)} ECB dates -> {[d.isoformat() for d, _ in rows]}")
            if len(rows) >= 2:
                return rows
            last_error = f"only {len(rows)} complete dates"
        except (requests.RequestException, ValueError, KeyError) as e:
            last_error = e
            print(f"خطا در Frankfurter ({base}): {e}")
    raise RuntimeError(f"داده‌ی نرخ ارز دریافت نشد: {last_error}")


def compute_strength():
    """
    دو تاریخ آخری که ECB واقعاً نرخ منتشر کرده رو مقایسه می‌کنه (نه روزهای تقویمی).
    فرمول دقیق: نرخ به شکل «ارز برای هر دلار» است، پس ارزش ارز در برابر دلار عکس آن است:
        تغییر٪ = (نرخ قبلی ÷ نرخ جدید − 1) × 100
    """
    today = datetime.now(ZoneInfo("America/New_York")).date()
    # در روزهای کاری، نرخ امروز ECB باید منتشر شده باشه (حدود ۱۰ صبح نیویورک).
    # اگه هنوز نیومده، تا ۲۰ دقیقه صبر می‌کنیم؛ اگه باز نیومد (مثلاً تعطیلی ECB)، پست تکراری نمی‌ره.
    for attempt in range(5):
        rows = get_rate_history()
        if today.weekday() >= 5 or rows[-1][0] >= today:
            break
        if attempt < 4:
            print(f"نرخ امروز ECB هنوز منتشر نشده (آخرین: {rows[-1][0]}) - ۵ دقیقه صبر")
            time.sleep(300)
    else:
        print(f"ECB برای امروز ({today}) نرخی منتشر نکرده (احتمالاً تعطیلی) - پستی ارسال نشد.")
        return None, None, None, None
    (prev_date, prev_rates), (last_date, last_rates) = rows[-2], rows[-1]
    age = (today - last_date).days
    if age > MAX_DATA_AGE_DAYS:
        raise RuntimeError(f"آخرین نرخ ECB مال {last_date} است ({age} روز پیش) - داده کهنه است.")

    results = []
    for c in G10_CURRENCIES:
        percent = (float(prev_rates[c]) / float(last_rates[c]) - 1) * 100
        results.append({"currency": c, "percent": percent})
    results.sort(key=lambda x: x["percent"], reverse=True)

    print(f"مقایسه: {prev_date} -> {last_date}")
    for r in results:
        print(f"  {r['currency']}: {r['percent']:+.3f}%")
    index = strength_index(results)
    return results, index, prev_date, last_date


def strength_index(vs_usd):
    """
    شاخص قدرت واقعی: هر ارز (از جمله خود دلار) در برابر میانگین ۹ ارز دیگه‌ی G10.
    با بازده لگاریتمی: r_X = تغییر ارز X در برابر دلار (r_USD = 0)
        قدرت X = میانگین (r_X − r_Y) برای همه‌ی Y ≠ X
    """
    r = {x["currency"]: math.log(1 + x["percent"] / 100) for x in vs_usd}
    r["USD"] = 0.0
    total, n = sum(r.values()), len(r)
    out = [{"currency": c, "percent": (math.exp((n * v - total) / (n - 1)) - 1) * 100} for c, v in r.items()]
    out.sort(key=lambda x: x["percent"], reverse=True)
    print("شاخص قدرت (در برابر همه‌ی G10):")
    for x in out:
        print(f"  {x['currency']}: {x['percent']:+.3f}%")
    return out


# ---------------------------------------------------------------- تحلیل خبری
#
# طراحی: هوش مصنوعی هیچ متن آزادی برای کانال نمی‌نویسه.
#   ۱. جمله‌ی «واقعیت‌ها» (کدوم ارزها قوی/ضعیف شدن) رو خود کد از روی عددها می‌سازه.
#   ۲. برای «چرا»، Groq فقط از یک لیست ثابت دسته‌بندی انتخاب می‌کنه و برای هر کدوم
#      لینک مقاله + تاریخ انتشار می‌ده. کد این‌ها رو چک می‌کنه:
#        - دامنه‌ی سایت باید در لیست منابع معتبر باشه
#        - تاریخ انتشار باید در بازه‌ی حرکت باشه
#        - جهت دسته‌بندی باید با جهت واقعی دلار جور باشه
#      فقط اگه همه‌ی شرط‌ها رد نشن، جمله‌ی «چرا» با قالب ثابت ساخته می‌شه.
#   ۳. اگه روز «مختلط» باشه یا دلیل تأییدشده پیدا نشه، بخش «چرا» منتشر نمی‌شه.

# دامنه → اسم ناشر (اسم ناشر رو خود کد می‌نویسه، نه مدل)
TRUSTED_SOURCES = {
    "reuters.com": "Reuters", "apnews.com": "AP", "bloomberg.com": "Bloomberg",
    "ft.com": "FT", "wsj.com": "WSJ", "cnbc.com": "CNBC",
    "federalreserve.gov": "Federal Reserve", "bls.gov": "BLS", "bea.gov": "BEA",
    "treasury.gov": "U.S. Treasury", "ecb.europa.eu": "ECB", "boj.or.jp": "BoJ",
    "bankofengland.co.uk": "Bank of England", "bankofcanada.ca": "Bank of Canada",
    "rba.gov.au": "RBA", "snb.ch": "SNB", "norges-bank.no": "Norges Bank",
    "riksbank.se": "Riksbank", "rbnz.govt.nz": "RBNZ",
}

# کد دسته → (عبارت انگلیسی ثابت، جهتی از دلار که این عامل توضیح می‌ده)
DRIVER_TAGS = {
    "FED_HAWKISH":        ("expectations of tighter Fed policy", "strength"),
    "FED_DOVISH":         ("expectations of Fed rate cuts", "weakness"),
    "US_DATA_STRONG":     ("stronger-than-expected U.S. economic data", "strength"),
    "US_DATA_WEAK":       ("weaker-than-expected U.S. economic data", "weakness"),
    "US_YIELDS_UP":       ("higher U.S. Treasury yields", "strength"),
    "US_YIELDS_DOWN":     ("lower U.S. Treasury yields", "weakness"),
    "RISK_OFF_SAFE_HAVEN": ("safe-haven demand amid market stress", "strength"),
    "RISK_ON":            ("improved global risk appetite", "weakness"),
    "US_FISCAL_POLITICAL": ("U.S. fiscal or political uncertainty", "weakness"),
}

NAMES = {"EUR": "euro", "GBP": "pound", "JPY": "yen", "CHF": "Swiss franc",
         "CAD": "Canadian dollar", "AUD": "Australian dollar", "NZD": "New Zealand dollar",
         "NOK": "Norwegian krone", "SEK": "Swedish krona"}

GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

# ---- نسخه‌ی فارسی
from tl_fa import IS_FA, fa_date, hashtags
NAMES_FA = {"EUR": "یورو", "GBP": "پوند", "JPY": "ین", "CHF": "فرانک سوئیس", "CAD": "دلار کانادا",
            "AUD": "دلار استرالیا", "NZD": "دلار نیوزیلند", "NOK": "کرون نروژ", "SEK": "کرون سوئد",
            "USD": "دلار آمریکا"}
DRIVER_FA = {
    "FED_HAWKISH": "انتظار سیاست سخت‌گیرانه‌تر فدرال رزرو",
    "FED_DOVISH": "انتظار کاهش نرخ بهره‌ی فدرال رزرو",
    "US_DATA_STRONG": "داده‌های اقتصادی قوی‌تر از انتظار آمریکا",
    "US_DATA_WEAK": "داده‌های اقتصادی ضعیف‌تر از انتظار آمریکا",
    "US_YIELDS_UP": "افزایش بازده اوراق خزانه‌ی آمریکا",
    "US_YIELDS_DOWN": "کاهش بازده اوراق خزانه‌ی آمریکا",
    "RISK_OFF_SAFE_HAVEN": "تقاضا برای دارایی امن در فضای نگرانی بازار",
    "RISK_ON": "بهبود ریسک‌پذیری جهانی",
    "US_FISCAL_POLITICAL": "نااطمینانی مالی یا سیاسی در آمریکا",
}


def facts_sentence_fa(strength, usd_rank):
    move, up, down = dollar_move(strength)
    best = [r for r in strength if r["percent"] > 0][:2]
    worst = [r for r in reversed(strength) if r["percent"] < 0][:2]
    fmt = lambda rows: " و ".join(f"{NAMES_FA[r['currency']]} ({r['percent']:+.2f}%)" for r in rows)
    if move == "weakness":
        s = f"دلار در برابر {up} ارز از 9 ارز G10 ضعیف شد."
    elif move == "strength":
        s = f"دلار در برابر {down} ارز از 9 ارز G10 تقویت شد."
    else:
        s = f"حرکت دلار مختلط بود: {up} ارز در برابر دلار قوی‌تر و {down} ارز ضعیف‌تر شدند."
    if best:
        s += f" بیشترین رشد در برابر دلار: {fmt(best)}."
    if worst:
        s += f" بیشترین افت: {fmt(worst)}."
    s += f" در رتبه‌بندی کلی قدرت، دلار آمریکا رتبه‌ی {usd_rank} از 10 را داشت."
    return s


def dollar_move(strength):
    """strength / weakness / mixed — بر اساس اینکه حداقل ۷ از ۹ ارز هم‌جهت باشن."""
    up = sum(1 for r in strength if r["percent"] > 0)
    down = sum(1 for r in strength if r["percent"] < 0)
    if down >= 7:
        return "strength", up, down
    if up >= 7:
        return "weakness", up, down
    return "mixed", up, down


def facts_sentence(strength):
    """جمله‌ی واقعیت‌ها — فقط از روی عددها، بدون هوش مصنوعی."""
    move, up, down = dollar_move(strength)
    best = [r for r in strength if r["percent"] > 0][:2]
    worst = [r for r in reversed(strength) if r["percent"] < 0][:2]
    fmt = lambda rows: " and ".join(f"the {NAMES[r['currency']]} ({r['percent']:+.2f}%)" for r in rows)

    if move == "weakness":
        s = f"The dollar weakened against {up} of 9 G10 currencies."
    elif move == "strength":
        s = f"The dollar strengthened against {down} of 9 G10 currencies."
    else:
        s = (f"The dollar was mixed: {up} of 9 G10 currencies gained against it "
             f"and {down} lost ground.")
    if best:
        s += f" Biggest gains vs USD: {fmt(best)}."
    if worst:
        s += f" Biggest declines: {fmt(worst)}."
    return s


def trusted_publisher(url):
    m = re.match(r"https://([^/\s|]+)(/\S*)?$", url)
    if not m:
        return None
    host = m.group(1).lower().split(":")[0]
    path = m.group(2) or "/"
    if len(path) < 8 or "search" in path.lower() or "?q=" in url:
        return None  # صفحه‌ی اصلی یا صفحه‌ی جستجو، نه مقاله
    for domain, name in TRUSTED_SOURCES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return None


def url_exists(url):
    """لینک‌های ساختگی معمولاً 404 می‌دن. سایت‌هایی مثل Reuters ممکنه به ربات‌ها 401/403 بدن
    که یعنی صفحه وجود داره ولی دسترسی محدوده - اون‌ها رو قبول می‌کنیم. فقط 404/410 رد می‌شه."""
    try:
        r = requests.get(url, headers=REQUEST_HEADERS, timeout=12, allow_redirects=True, stream=True)
        r.close()
        return r.status_code not in (404, 410)
    except requests.RequestException:
        return False


def parse_drivers(raw, prev_date, last_date, move):
    """خروجی Groq رو خط‌به‌خط اعتبارسنجی می‌کنه. هر خط نامعتبر کنار گذاشته می‌شه."""
    # نرخ ECB ساعت 14:15 اروپا (حدود 8:15 صبح نیویورک) ثابت می‌شه.
    # اخبار مؤثر بر این حرکت: از یک روز قبل از تاریخ اول تا یک روز قبل از تاریخ دوم.
    # (خبرِ روز دوم ممکنه بعد از تثبیت نرخ منتشر شده باشه، پس قبول نمی‌شه.)
    window_start = prev_date - timedelta(days=1)
    window_end = last_date - timedelta(days=1)
    accepted, rejected = [], []
    for line in raw.splitlines():
        line = clean_citations(line)
        m = re.fullmatch(r"\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(https://\S+?)\s*\|\s*([A-Z_]+)\s*", line)
        if not m:
            continue
        day, url, tag = m.groups()
        try:
            published = datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            rejected.append((line, "bad date")); continue
        publisher = trusted_publisher(url)
        if not publisher:
            rejected.append((line, "untrusted source")); continue
        if not window_start <= published <= window_end:
            rejected.append((line, f"outside {window_start}..{window_end}")); continue
        if tag not in DRIVER_TAGS:
            rejected.append((line, "unknown category")); continue
        if DRIVER_TAGS[tag][1] != move:
            rejected.append((line, f"direction mismatch (dollar {move})")); continue
        if tag in [a[2] for a in accepted]:
            continue
        if not url_exists(url):
            rejected.append((line, "URL not found (404) - possibly invented")); continue
        accepted.append((day, url, tag, publisher))
    for line, why in rejected:
        print(f"  رد شد: {line}  ←  {why}")
    for a in accepted:
        print(f"  قبول شد: {a}")
    return accepted[:2]


def find_drivers(strength, prev_date, last_date):
    """فقط برای روزهایی که دلار جهت روشنی داشته، دلیل تأییدشده پیدا می‌کنه."""
    move, up, down = dollar_move(strength)
    if move == "mixed":
        print("حرکت دلار مختلط بوده - بخش «چرا» منتشر نمی‌شه.")
        return None
    if not GROQ_API_KEY:
        print("GROQ_API_KEY موجود نیست - بخش «چرا» حذف شد.")
        return None

    allowed = [t for t, (_, d) in DRIVER_TAGS.items() if d == move]
    ws, we = prev_date - timedelta(days=1), last_date - timedelta(days=1)
    prompt = (
        f"Between the ECB reference-rate fixings of {prev_date} and {last_date}, "
        f"the U.S. dollar showed broad {move} against G10 currencies.\n"
        f"Search Reuters, AP, Bloomberg, FT, WSJ, CNBC or official central-bank / "
        f"government releases for news articles published between {ws} and {we} "
        f"that EXPLICITLY attribute the dollar's {move} to a cause.\n"
        f"Allowed category codes: {', '.join(allowed)}.\n"
        "Rules: only include a category if a real article explicitly links it to the "
        "dollar's move. Give the article's real URL (not a search page or homepage) and its "
        "actual publication date. Maximum 2 lines. If you find nothing, output only the header.\n"
        "Output format (plain text, nothing else):\n"
        "DRIVERS:\nYYYY-MM-DD | https://article-url | CATEGORY_CODE"
    )
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for model in GROQ_MODELS:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 800,
            "reasoning_effort": "medium",
            "tools": [{"type": "browser_search"}],
            "tool_choice": "required",
        }
        data = call_groq_with_retry(payload, headers)
        if not data:
            print(f"Groq ({model}) پاسخ نداد.")
            continue
        try:
            raw = (data["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError):
            continue
        print(f"خروجی Groq ({model}):\n{raw}")
        drivers = parse_drivers(raw, prev_date, last_date, move)
        if not drivers:
            print("دلیل تأییدشده‌ای پیدا نشد - بخش «چرا» منتشر نمی‌شه.")
            return None
        pubs = sorted({d[3] for d in drivers})
        if IS_FA:
            return (f"به گزارش رسانه‌ها ({'، '.join(pubs)})، این حرکت به "
                    + " و ".join(DRIVER_FA[d[2]] for d in drivers) + " نسبت داده شد.")
        phrases = [DRIVER_TAGS[d[2]][0] for d in drivers]
        return (f"News reports ({', '.join(pubs)}) attributed the move to "
                + " and ".join(phrases) + ".")
    return None


def clean_citations(text):
    """علامت‌های استناد داخلی مدل (مثل 【3†L163-L170】) رو پاک می‌کنه"""
    return re.sub(r"[【〖][^】〗]*[】〗]", "", text).strip()


def call_groq_with_retry(payload, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = requests.post(GROQ_URL, json=payload, headers=headers, timeout=90)
            data = response.json()
        except (requests.RequestException, ValueError) as e:
            print(f"خطا در Groq: {e}")
            return None
        error = data.get("error") if isinstance(data, dict) else None
        if error and error.get("code") == "rate_limit_exceeded":
            match = re.search(r"try again in ([\d.]+)s", error.get("message", ""))
            wait = float(match.group(1)) + 2 if match else 15
            print(f"محدودیت نرخ Groq - {wait:.1f} ثانیه صبر (تلاش {attempt + 1}/{max_retries})")
            time.sleep(min(wait, 30))
            continue
        if error:
            print(f"خطای Groq: {error}")
            return None
        return data
    return None


# ---------------------------------------------------------------- ارسال

def send_to_telegram(text):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    response = requests.post(TELEGRAM_API_URL, data=payload, timeout=20)
    result = response.json()
    if not result.get("ok"):
        print(f"خطا در ارسال پیام: {result}")
        raise SystemExit(1)
    print("پیام قدرت ارزها با موفقیت ارسال شد.")


def main():
    ny_now = datetime.now(ZoneInfo("America/New_York"))
    # نکته: آخر هفته ECB نرخ جدید منتشر نمی‌کنه. زمان‌بندی این گزارش در cron-job.org
    # باید فقط دوشنبه تا جمعه باشه تا پست تکراری نره.

    strength, index, prev_date, last_date = compute_strength()
    if strength is None:
        return
    facts = facts_sentence(strength)
    usd_rank = next(i for i, x in enumerate(index, 1) if x["currency"] == "USD")
    facts += f" In the overall strength ranking, the U.S. dollar placed {usd_rank} of 10."
    drivers = find_drivers(strength, prev_date, last_date)

    fix_et = datetime(last_date.year, last_date.month, last_date.day, 14, 15,
                      tzinfo=ZoneInfo("Europe/Berlin")).astimezone(ZoneInfo("America/New_York"))
    fix_txt = fix_et.strftime("%I:%M %p ET").lstrip("0")

    if IS_FA:
        fix_ir = fix_et.astimezone(ZoneInfo("Asia/Tehran")).strftime("%H:%M")
        now_ir = datetime.now(ZoneInfo("Asia/Tehran"))
        lines = [
            "💱 <b>قدرت ارزهای G10</b>",
            f"📅 {fa_date(prev_date, weekday=False, year=False)} ← {fa_date(last_date, weekday=False)}"
            f" · نرخ مرجع ECB ساعت {fix_ir} به وقت تهران",
            "",
            "<b>قدرت هر ارز در برابر بقیه‌ی ارزهای G10:</b>",
        ]
        for i, r in enumerate(index, 1):
            emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
            nm = f"{NAMES_FA[r['currency']]} ({r['currency']})"
            nm = f"<b>{nm}</b>" if r["currency"] == "USD" else nm
            lines.append(f"{i}. {emoji} {nm}: {r['percent']:+.2f}%")
        lines += ["", "<b>تغییر در برابر دلار:</b>",
                  " · ".join(f"{r['currency']} {r['percent']:+.2f}%" for r in strength)]
        lines += ["", "📊 <b>خلاصه:</b>", html.escape(facts_sentence_fa(strength, usd_rank))]
        if drivers:
            lines += ["", "📰 <b>دلیل حرکت:</b>", html.escape(drivers)]
        lines += [
            "",
            "<i>نرخ‌های مرجع روزانه‌ی بانک مرکزی اروپا (نه قیمت لحظه‌ای). حرکت‌های بعد از این ساعت، "
            "از جمله واکنش به بیشتر داده‌های آمریکا، در گزارش بعدی دیده می‌شن. "
            "تفاوت‌های کمتر از حدود 0.05% معنادار نیستن.</i>",
            f"🕐 به‌روزرسانی: {now_ir.strftime('%H:%M')} به وقت تهران",
            "",
            hashtags("فارکس", "دلار", "قدرت_ارزها", NAMES_FA[index[0]["currency"]],
                     NAMES_FA[index[-1]["currency"]]),
        ]
        send_to_telegram("\n".join(lines))
        return

    lines = [
        "💱 <b>G10 Currency Strength</b>",
        f"📅 {prev_date.strftime('%b %d')} → {last_date.strftime('%b %d, %Y')} · ECB fixing {fix_txt}",
        "",
        "<b>Strength vs all other G10 currencies:</b>",
    ]
    for i, r in enumerate(index, 1):
        emoji = "🟢" if r["percent"] > 0 else ("🔴" if r["percent"] < 0 else "⚪")
        name = f"<b>{r['currency']}</b>" if r["currency"] == "USD" else r["currency"]
        lines.append(f"{i}. {emoji} {name}: {r['percent']:+.2f}%")

    lines += ["", "<b>Change vs USD:</b>",
              " · ".join(f"{r['currency']} {r['percent']:+.2f}%" for r in strength)]

    lines += ["", "📊 <b>Summary:</b>", html.escape(facts)]
    if drivers:
        lines += ["", "📰 <b>What's driving it:</b>", html.escape(drivers)]

    lines += [
        "",
        f"<i>ECB daily reference rates, fixed at {fix_txt} (not live quotes). Moves after that time, "
        "including most U.S. data releases, appear in the next report. Differences under ~0.05% are noise.</i>",
        f"🕐 Updated: {ny_now.strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        " ".join(dict.fromkeys(["#Forex", "#USD", "#G10", f"#{index[0]['currency']}",
                                f"#{index[-1]['currency']}"])),
    ]
    send_to_telegram("\n".join(lines))


if __name__ == "__main__":
    main()
