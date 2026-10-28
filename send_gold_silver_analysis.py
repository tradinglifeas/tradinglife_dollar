"""
کارت «Gold & Silver — Fundamental Analysis» — کاملاً بنیادی و مستقل از Market Intelligence (که تکنیکاله).

همه‌ی عددها از منابع داده میان، نه از هوش مصنوعی:
  - قیمت لحظه‌ای و تاریخچه‌ی روزانه: Alpha Vantage (GOLD_SILVER_SPOT / GOLD_SILVER_HISTORY) — ۴ درخواست
  - موقعیت معامله‌گران بزرگ: گزارش هفتگی CFTC (Managed Money)، با تاریخ گزارش
  - کلان: شاخص دلار (فرمول DXY از نرخ‌های ECB)، بازده ۱۰ ساله (خزانه‌داری آمریکا)، تورم (BLS)
  - بازده واقعی ۱۰ ساله (TIPS) و تورم مورد انتظار (breakeven) از خزانه‌داری آمریکا
  - نسبت طلا به نقره در برابر بازه‌ی یک‌ساله
  - جمله‌ی خلاصه و متن زیر عکس رو خود کد می‌سازه (جهت‌ها هیچ‌وقت اشتباه نمی‌شن)
  - هیچ تحلیل تکنیکالی (RSI، میانگین متحرک، نمودار) نداره — اون کار Market Intelligence ـه
  - هیچ امتیاز یا برچسب سیگنال‌مانندی نمایش داده نمی‌شه

اخبار: از RSS (CNBC، MarketWatch، Bloomberg، Mining.com)، فقط خبرهای طلا/نقره،
  با همون چک‌های Key Market News. اگه خبر معتبری نبود، کارت بدون بخش اخبار می‌ره.

فقط دوشنبه تا جمعه (آخر هفته بازار بسته‌ست و پستی نمی‌ره).
اگه قیمت‌ها دریافت نشن یا غیرعادی باشن، پستی نمی‌ره و اجرا قرمز می‌شه.
"""

import os
import io
import re
import csv
import json
import html
import time
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from PIL import Image, ImageDraw, ImageFont

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
# کلید جداگانه برای این کارت (سهمیه‌ی مستقل از Market Intelligence)؛ اگه نبود، از کلید مشترک استفاده می‌شه
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_METALS_KEY") or os.environ["ALPHA_VANTAGE_API_KEY"]
print("Alpha Vantage key:", "dedicated (ALPHA_VANTAGE_METALS_KEY)" if os.environ.get("ALPHA_VANTAGE_METALS_KEY")
      else "shared (ALPHA_VANTAGE_API_KEY)")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

ET_TZ = ZoneInfo("America/New_York")
AV = "https://www.alphavantage.co/query"
CFTC_DISAGG = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
CFTC_MAX_AGE_DAYS = 21
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]

METALS = [
    {"symbol": "XAU", "name": "Gold", "api_symbol": "GOLD", "code": "088691", "cftc": "disagg"},
    {"symbol": "XAG", "name": "Silver", "api_symbol": "SILVER", "code": "084691", "cftc": "disagg"},
]
MAX_DAILY_MOVE = 12.0   # تغییر روزانه‌ی بیشتر از این = داده‌ی مشکوک، پست نمی‌ره

NEWS_FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
    ("Bloomberg", "https://feeds.bloomberg.com/economics/news.rss"),
    ("Mining.com", "https://www.mining.com/feed/"),
]
NEWS_MAX = 2
NEWS_CANDIDATES = 25

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
HEADERS = dict(UA, Accept="application/json, application/rss+xml, application/xml, */*")


def sf(v, default=None):
    try:
        if v in (None, "", ".", "N/A", "null"):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def pct(a, b):
    return ((a / b) - 1) * 100 if a is not None and b not in (None, 0) else None


def get_json(url, params=None, timeout=40):
    r = requests.get(url, params=params, timeout=timeout, headers=UA)
    r.raise_for_status()
    return r.json()


# ================================================================ Alpha Vantage (از Market Intelligence)

def av(function, **kwargs):
    d=get_json(AV, {'function':function,'apikey':ALPHA_KEY,**kwargs})
    for k in ('Information','Note','Error Message'):
        if k in d:
            msg=str(d[k])
            if 'rate limit' in msg.lower() or 'requests per day' in msg.lower() or 'sparingly' in msg.lower():
                msg='rate limit reached (free tier: 25 requests/day, 1 request/second)'
            raise RuntimeError('Alpha Vantage: '+msg.replace(ALPHA_KEY,'***'))
    time.sleep(1.6)
    return d

def parse_metal(d):
    out=[]
    def add(date,row):
        if not isinstance(row,dict): row={'value':row}
        val=None
        for k in ('value','price','close','rate','spot'):
            if k in row and sf(row[k]) is not None: val=sf(row[k]); break
        if val is None:
            for k,v in row.items():
                if any(t in k.lower() for t in ('value','price','close','rate','spot')) and sf(v) is not None:
                    val=sf(v); break
        if date and val is not None:
            out.append({'date':str(date),'open':sf(row.get('open'),val),'high':sf(row.get('high'),val),
                        'low':sf(row.get('low'),val),'close':val,'volume':sf(row.get('volume')),'market_cap':None})
    c=d.get('data') if isinstance(d,dict) else None
    if isinstance(c,list):
        for r in c:
            if isinstance(r,dict): add(r.get('date') or r.get('timestamp') or r.get('time'),r)
    elif isinstance(c,dict):
        for date,r in c.items(): add((r.get('date') if isinstance(r,dict) else None) or date,r)
    if not out and isinstance(d,dict):
        for k,v in d.items():
            if isinstance(v,dict) and any(x in k.lower() for x in ('time series','history','prices')):
                for date,r in v.items(): add(date,r)
    out=sorted({r['date']:r for r in out}.values(),key=lambda x:x['date'])
    if len(out)<55:
        raise RuntimeError(f'Not enough metal history ({len(out)} rows). Response: {json.dumps(d)[:700]}')
    return out



def metal_spot(m):
    d = av("GOLD_SILVER_SPOT", symbol=m["api_symbol"])
    for k in ("price", "Price", "spot_price", "Spot Price", "ask", "bid"):
        if sf(d.get(k)) is not None:
            return sf(d[k])
    for v in d.values():
        if isinstance(v, dict):
            for k, x in v.items():
                if ("price" in k.lower() or "rate" in k.lower()) and sf(x) is not None:
                    return sf(x)
    raise RuntimeError(f"{m['name']} spot could not be parsed: {json.dumps(d)[:300]}")


def metal_data(m, today):
    """
    قیمت و تغییر روزانه (فقط برای زمینه) + تاریخچه برای نسبت طلا به نقره.
    قیمت قبلی = آخرین بسته‌شدن روزانه‌ی «قبل از امروز».
    """
    hist = parse_metal(av("GOLD_SILVER_HISTORY", symbol=m["api_symbol"], interval="daily"))
    spot = metal_spot(m)
    past = [r for r in hist if r["date"][:10] < today.isoformat()]
    if len(past) < 30:
        raise RuntimeError(f"{m['name']}: not enough history before today ({len(past)})")
    prev = past[-1]
    day = pct(spot, prev["close"])
    if day is None or abs(day) > MAX_DAILY_MOVE:
        raise SystemExit(f"{m['name']}: تغییر روزانه‌ی غیرعادی ({day}) - داده مشکوکه، پستی ارسال نشد.")
    wk = [r for r in past if r["date"][:10] <= (today - timedelta(days=7)).isoformat()]
    print(f"✓ {m['name']}: {spot} (prev close {prev['close']} on {prev['date'][:10]}, {day:+.2f}%)")
    return {"price": spot, "prev_date": prev["date"][:10], "day": day,
            "week": pct(spot, wk[-1]["close"]) if wk else None,
            "closes": {r["date"][:10]: r["close"] for r in past}}


def ratio_stats(g, s):
    """نسبت طلا به نقره: عدد فعلی، میانگین و جایگاه در یک سال اخیر."""
    now = g["price"] / s["price"]
    common = sorted(set(g["closes"]) & set(s["closes"]))[-252:]
    series = [g["closes"][d] / s["closes"][d] for d in common if s["closes"][d]]
    if len(series) < 120:
        return {"now": now, "avg": None, "pctile": None, "lo": None, "hi": None}
    return {"now": now, "avg": sum(series) / len(series), "lo": min(series), "hi": max(series),
            "pctile": 100 * sum(1 for x in series if x <= now) / len(series)}


SUP, NEU, HEAD, STR = "Supportive", "Neutral", "Headwind", "Stretched"


def heat_factors(c, macro_d):
    """
    فقط عوامل بنیادی، هر کدوم با یک قانون ثابت و شفاف (بدون امتیاز ترکیبی و بدون پیش‌بینی):
      Supportive = معمولاً به نفع فلزات · Headwind = معمولاً به ضرر · Stretched = موقعیت‌ها شلوغ
    """
    f = []
    r = macro_d.get("r105")
    f.append(("Real yield, 10Y (5 days)", NEU if r is None or abs(r) < 5 else SUP if r < 0 else HEAD,
              "n/a" if r is None else f"{r:+.0f} bps"))
    u = macro_d.get("usd5")
    f.append(("U.S. dollar (5 days)", NEU if u is None or abs(u) < 0.3 else SUP if u < 0 else HEAD,
              "n/a" if u is None else f"{u:+.2f}%"))
    b = macro_d.get("be5")
    f.append(("Inflation expectations (5 days)", NEU if b is None or abs(b) < 3 else SUP if b > 0 else HEAD,
              "n/a" if b is None else f"{b:+.0f} bps"))
    if c.get("available") and c.get("net_change") is not None and c.get("oi"):
        ch = c["net_change"]
        f.append(("Speculator flow (week)", NEU if abs(ch) < 0.01 * c["oi"] else SUP if ch > 0 else HEAD,
                  f"{ch / 1000:+.1f}K contracts"))
    else:
        f.append(("Speculator flow (week)", NEU, "n/a"))
    pc = c.get("pctile") if c.get("available") else None
    f.append(("Positioning vs 1 year", NEU if pc is None else STR if pc >= 85 else NEU,
              "n/a" if pc is None else f"{pc:.0f}th pct · " + ("Crowded long" if pc >= 85 else
                                                               "Light" if pc <= 15 else "Mid-range")))
    return f


def tally(f):
    n = {k: sum(1 for _, s, _ in f if s == k) for k in (SUP, NEU, HEAD, STR)}
    if IS_FA:
        parts = [f"{n[SUP]} حمایتی", f"{n[HEAD]} فشار منفی"] + ([f"{n[STR]} کشیده"] if n[STR] else [])
        return " · ".join(parts)
    parts = [f"{n[SUP]} supportive", f"{n[HEAD]} headwind{'s' if n[HEAD] != 1 else ''}"]
    if n[STR]:
        parts.append(f"{n[STR]} stretched")
    return " · ".join(parts)


def summary_sentence(macro_d):
    """خلاصه‌ی بنیادی، فقط از روی عددها."""
    def mv(label, v, unit, small):
        if v is None:
            return None
        if abs(v) < small:
            return f"{label} was little changed"
        return f"{label} {'rose' if v > 0 else 'fell'} {abs(v):.{1 if unit == '%' else 0}f}{'%' if unit == '%' else ' bps'}"
    bits = [x for x in (mv("the 10-year real yield", macro_d.get("r105"), "bps", 1),
                        mv("the dollar index", macro_d.get("usd5"), "%", 0.05),
                        mv("market inflation expectations", macro_d.get("be5"), "bps", 1)) if x]
    txt = ("Over five trading days, " + ", ".join(bits[:-1]) + (" and " if len(bits) > 1 else "") + bits[-1] + "."
           ) if bits else ""
    if macro_d.get("cpi") is not None and macro_d.get("cpi_prev") is not None:
        d = macro_d["cpi"] - macro_d["cpi_prev"]
        trend = "accelerated" if d >= 0.05 else "eased" if d <= -0.05 else "held steady"
        txt += (f" U.S. CPI inflation {trend} to {macro_d['cpi']:.1f}% in {macro_d['cpi_month']}"
                if trend != "held steady" else
                f" U.S. CPI inflation held steady at {macro_d['cpi']:.1f}% in {macro_d['cpi_month']}")
        txt += f" (from {macro_d['cpi_prev']:.1f}%)." if trend != "held steady" else "."
    return txt.strip()


def cftc_sentence(name, c):
    if not c.get("available"):
        return None
    if IS_FA:
        nm = {"Gold": "طلا", "Silver": "نقره"}.get(name, name)
        side = "خالص خرید" if c["net"] >= 0 else "خالص فروش"
        s_ = f"صندوق‌های مدیریت‌شده در {nm} {side} {abs(c['net']) / 1000:.1f}K قرارداد بودن"
        if c.get("net_change") is not None:
            v = abs(c["net_change"]) / 1000
            if c["net"] >= 0:
                s_ += f" و در هفته‌ی اخیر {v:.1f}K قرارداد {'اضافه' if c['net_change'] > 0 else 'کم'} کردن"
            else:
                s_ += f" و در هفته‌ی اخیر {v:.1f}K قرارداد از موقعیت فروش {'کم' if c['net_change'] > 0 else 'اضافه'} کردن"
        return s_ + "."
    side = "net long" if c["net"] >= 0 else "net short"
    ch = ""
    if c.get("net_change") is not None:
        verb = "added" if c["net_change"] > 0 else "cut"
        ch = f", having {verb} {abs(c['net_change']) / 1000:.1f}K in the latest week"
        if c["net"] < 0 and c["net_change"] > 0:
            ch = f", having reduced shorts by {abs(c['net_change']) / 1000:.1f}K in the latest week"
    return f"Money managers were {side} {name.lower()} by {abs(c['net']) / 1000:.1f}K contracts{ch}."


# ================================================================ کلان و CFTC (از Market Intelligence)

DXY_W = {'EUR':0.576,'JPY':0.136,'GBP':0.119,'CAD':0.091,'SEK':0.042,'CHF':0.036}

def usd_index_series():
    """ICE-DXY formula rebuilt from ECB reference rates (Frankfurter, free, no key).
    With base=USD each rate r = units of X per 1 USD, so DXY = 50.14348112 * prod(r^w)."""
    from datetime import timedelta
    end=datetime.utcnow().date(); start=end-timedelta(days=25)
    last_err=None
    for base in ('https://api.frankfurter.app','https://api.frankfurter.dev/v1'):
        try:
            d=get_json(f"{base}/{start}..{end}",{'from':'USD','to':','.join(DXY_W)},timeout=25)
            out=[]
            for day in sorted(d.get('rates',{})):
                r=d['rates'][day]
                if all(sf(r.get(c)) for c in DXY_W):
                    v=50.14348112
                    for c,w in DXY_W.items(): v*=sf(r[c])**w
                    out.append((day,v))
            if len(out)>=6: return out
            last_err=f'only {len(out)} rows'
        except Exception as e:
            last_err=e
    raise RuntimeError(f'USD index unavailable: {last_err}')

def treasury_curve_10y(kind):
    """
    داده‌ی رسمی روزانه‌ی خزانه‌داری آمریکا (رایگان، بدون کلید).
    kind = daily_treasury_yield_curve (بازده اسمی) یا daily_treasury_real_yield_curve (بازده واقعی TIPS)
    خروجی: {تاریخ: بازده ۱۰ ساله}
    """
    rows = {}
    yr = datetime.utcnow().year
    for y in (yr, yr - 1):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{y}/all")
        r = requests.get(url, params={"type": kind, "field_tdr_date_value": str(y), "_format": "csv"},
                         headers=UA, timeout=25)
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(r.text.lstrip("\ufeff"))):
            col = next((k for k in row if k and re.fullmatch(r"10\s*y(r|ear)?", k.strip(), re.I)), None)
            v = sf(row.get(col)) if col else None
            dd = row.get("Date", "")
            if v is None or not dd:
                continue
            try:
                rows[datetime.strptime(dd, "%m/%d/%Y").strftime("%Y-%m-%d")] = v
            except ValueError:
                continue
        if len(rows) >= 6:
            break
    if len(rows) < 6:
        raise RuntimeError(f"Treasury {kind}: only {len(rows)} rows")
    return rows


def cpi_trend():
    """BLS (رایگان): تورم سالانه‌ی CPI این ماه و ماه قبل، برای تشخیص روند."""
    d = get_json("https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0", timeout=25)
    if d.get("status") != "REQUEST_SUCCEEDED":
        raise RuntimeError(f"BLS: {d.get('status')} {d.get('message')}")
    m = [x for x in d["Results"]["series"][0]["data"]
         if str(x.get("period", "")).startswith("M") and x["period"] != "M13" and sf(x.get("value")) is not None]
    m.sort(key=lambda x: (int(x["year"]), int(x["period"][1:])), reverse=True)
    if len(m) < 14:
        raise RuntimeError(f"BLS CPI: only {len(m)} months")
    now_yoy = pct(sf(m[0]["value"]), sf(m[12]["value"]))
    prev_yoy = pct(sf(m[1]["value"]), sf(m[13]["value"]))
    month = datetime(int(m[0]["year"]), int(m[0]["period"][1:]), 1).strftime("%b %Y")
    return now_yoy, prev_yoy, month


def macro():
    o = {}
    try:
        x = usd_index_series(); o["usd"] = x[-1][1]; o["usd5"] = pct(x[-1][1], x[-6][1])
    except Exception as e:
        print("MACRO USD", e)
    nom = real = None
    try:
        nom = treasury_curve_10y("daily_treasury_yield_curve")
        k = sorted(nom); o["y10"] = nom[k[-1]]; o["y105"] = (nom[k[-1]] - nom[k[-6]]) * 100
    except Exception as e:
        print("MACRO 10Y", e)
    try:
        real = treasury_curve_10y("daily_treasury_real_yield_curve")
        k = sorted(real); o["r10"] = real[k[-1]]; o["r105"] = (real[k[-1]] - real[k[-6]]) * 100
        o["r_date"] = k[-1]
    except Exception as e:
        print("MACRO REAL 10Y", e)
    if nom and real:
        # نرخ تورم مورد انتظار بازار (breakeven) = بازده اسمی − بازده واقعی، روی تاریخ‌های مشترک
        common = sorted(set(nom) & set(real))
        if len(common) >= 6:
            be = [nom[d] - real[d] for d in common]
            o["be"] = be[-1]; o["be5"] = (be[-1] - be[-6]) * 100
    try:
        o["cpi"], o["cpi_prev"], o["cpi_month"] = cpi_trend()
    except Exception as e:
        print("MACRO CPI", e)
    print("MACRO result", o)
    return o


def cftc(asset):
    if not asset['code']: return {'available':False,'reason':'No direct CFTC contract mapping'}
    url=CFTC_DISAGG if asset['cftc']=='disagg' else CFTC_LEGACY
    try:
        rows=get_json(url,{'$limit':60,'$order':'report_date_as_yyyy_mm_dd DESC',
                           '$where':f"cftc_contract_market_code='{asset['code']}'"})
    except Exception as e:
        print('CFTC query error',asset['symbol'],e); return {'available':False,'reason':'CFTC unavailable'}
    if not rows:return {'available':False,'reason':'No CFTC rows'}
    def dt(r):return str(r.get('report_date_as_yyyy_mm_dd') or r.get('as_of_date_in_form_yyyy_mm_dd') or r.get('report_date_as_mm_dd_yyyy') or '')
    rows=sorted(rows,key=dt,reverse=True)
    cur=rows[0]; prev=rows[1] if len(rows)>1 else {}
    def f(r,*names):
        for n in names:
            if sf(r.get(n)) is not None:return sf(r[n])
        return None
    if asset['cftc']=='disagg':
        L=f(cur,'m_money_positions_long_all'); S=f(cur,'m_money_positions_short_all')
        pL=f(prev,'m_money_positions_long_all'); pS=f(prev,'m_money_positions_short_all'); cohort='Managed Money'
    else:
        L=f(cur,'noncomm_positions_long_all','non_comm_positions_long_all'); S=f(cur,'noncomm_positions_short_all','non_comm_positions_short_all')
        pL=f(prev,'noncomm_positions_long_all','non_comm_positions_long_all'); pS=f(prev,'noncomm_positions_short_all','non_comm_positions_short_all'); cohort='Large Non-Commercial'
    oi=f(cur,'open_interest_all'); poi=f(prev,'open_interest_all')
    if L is None or S is None:
        print('CFTC keys',asset['symbol'],list(cur.keys())[:50]); return {'available':False,'reason':'CFTC fields not parsed'}
    try:
        rep_date=datetime.strptime(dt(cur)[:10],'%Y-%m-%d').date()
        age=(datetime.utcnow().date()-rep_date).days
    except Exception:
        age=None
    if age is None or age>CFTC_MAX_AGE_DAYS:
        print('CFTC stale/undated',asset['symbol'],dt(cur),'age',age)
        return {'available':False,'reason':f'CFTC data stale ({dt(cur)[:10]})'}
    net=L-S; pnet=(pL-pS) if pL is not None and pS is not None else None
    # جایگاه موقعیت فعلی در یک سال اخیر (۰ = کمترین، ۱۰۰ = بیشترین)
    nets=[]
    for r in rows[:52]:
        a=f(r,'m_money_positions_long_all'); b=f(r,'m_money_positions_short_all')
        if a is not None and b is not None: nets.append(a-b)
    pctile=(100*sum(1 for x in nets if x<=net)/len(nets)) if len(nets)>=26 else None
    return {'available':True,'pctile':pctile,'weeks':len(nets),'cohort':cohort,'long':L,'short':S,'net':net,'net_change':(net-pnet if pnet is not None else None),
            'oi':oi,'oi_change':pct(oi,poi) if oi is not None and poi not in (None,0) else None,'date':dt(cur)[:10]}


# ================================================================ اخبار (همون روش Key Market News)

def strip_tags(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    return re.sub(r"\s+", " ", html.unescape(t)).strip()


def parse_date(t):
    if not t:
        return None
    try:
        d = parsedate_to_datetime(t.strip())
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(t.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def fetch_feed(source, url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"  ✗ {source}: {e}")
        return []
    items = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        f = {c.tag.split("}")[-1]: (c.text or "") for c in node}
        link = (f.get("link") or "").strip()
        title = strip_tags(f.get("title", ""))
        low = (link + " " + title).lower()
        if any(k in low for k in ("/video", "/videos/", "/opinion/", "podcast", "sponsored",
                                  "press-release", "/learn/", "price-prediction", "price prediction")):
            continue
        when = parse_date(f.get("pubDate") or f.get("published") or f.get("updated") or f.get("date"))
        desc = strip_tags(f.get("description") or f.get("summary") or "")[:300]
        if title and when:
            items.append({"source": source, "title": title, "desc": desc, "time": when})
    print(f"  ✓ {source}: {len(items)} items")
    return items


def collect_news():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=72 if datetime.now(ET_TZ).weekday() == 0 else 30)
    print("دریافت فیدهای خبری:")
    allit = []
    for s, u in NEWS_FEEDS:
        allit += fetch_feed(s, u)
    # فقط خبرهای مربوط به طلا/نقره (Goldman Sachs و Golden نه)
    kw = re.compile(r"\b(gold|silver|bullion|precious metals?|xau|xag|comex)\b", re.I)
    allit = [it for it in allit if kw.search(it["title"] + " " + it["desc"])
             and not re.search(r"goldman", it["title"], re.I)]
    print(f"خبرهای مرتبط با طلا/نقره: {len(allit)}")
    fresh, seen = [], set()
    for it in sorted(allit, key=lambda x: x["time"], reverse=True):
        if not (cutoff <= it["time"] <= now + timedelta(minutes=10)):
            continue
        k = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:60]
        if k in seen:
            continue
        seen.add(k)
        fresh.append(it)
    print(f"خبرهای طلا/نقره در بازه‌ی زمانی: {len(fresh)}")
    return fresh[:NEWS_CANDIDATES]


def numbers_in(t):
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*\.?\d*", t)}


def common_run(a, b):
    wa, wb = re.findall(r"[a-z]+", a.lower()), re.findall(r"[a-z]+", b.lower())   # فقط کلمات، نه عدد
    best = 0
    for i in range(len(wa)):
        for j in range(len(wb)):
            k = 0
            while i + k < len(wa) and j + k < len(wb) and wa[i + k] == wb[j + k]:
                k += 1
            best = max(best, k)
    return best


from tl_fa import IS_FA, fa_check, FA_NEWS_RULES, FA_NEWS_FIELDS


def validate(selection, cands):
    ok = []
    for s in selection:
        try:
            idx = int(s.get("id"))
            label = str(s.get("label", "")).strip().rstrip(":")
            summ = re.sub(r"[【〖][^】〗]*[】〗]", "", str(s.get("summary", ""))).strip()
        except (TypeError, ValueError, AttributeError):
            continue
        if not 1 <= idx <= len(cands) or idx in [o["id"] for o in ok]:
            continue
        it = cands[idx - 1]
        src = it["title"] + " " + it["desc"]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 &/.\-]{0,17}", label):
            label = "Metals"
        if not 30 <= len(summ) <= 230:
            print(f"  رد شد #{idx}: طول"); continue
        extra = numbers_in(summ) - numbers_in(src)
        if extra:
            print(f"  رد شد #{idx}: عدد ساختگی {extra}"); continue
        if max(common_run(summ, it["title"]), common_run(summ, it["desc"])) >= 9:
            print(f"  رد شد #{idx}: کپی متن"); continue
        if re.search(r"\b(week|weekly|month|monthly|quarter|year[- ]to[- ]date|ytd)\b", src, re.I) \
                and re.search(r"\d\s*%|percent", summ, re.I) \
                and not re.search(r"\b(week|weekly|month|monthly|quarter|year|ytd)\b", summ, re.I):
            print(f"  رد شد #{idx}: بازه‌ی زمانی حذف شده"); continue
        if re.search(r"\b(reports?|reported|according to|citing|sources say|people familiar)\b", src, re.I) \
                and not re.search(r"\b(report|reported|reports|according|citing|said|says|told)\b", summ, re.I):
            print(f"  رد شد #{idx}: منبعِ گزارش حذف شده"); continue
        if IS_FA:
            fa, lab_fa, why = fa_check(s, summ, src, "فلزات")
            if why:
                print(f"  رد شد #{idx}: {why}"); continue
            label, summ = lab_fa, fa
        ok.append({"id": idx, "label": label, "summary": summ, "source": it["source"]})
        print(f"  قبول شد #{idx} [{it['source']}] {label}: {summ}")
    return ok[:NEWS_MAX]


def call_groq(payload):
    h = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    for _ in range(3):
        try:
            data = requests.post(GROQ_URL, json=payload, headers=h, timeout=90).json()
        except Exception as e:
            print(f"خطا در Groq: {e}")
            return None
        err = data.get("error") if isinstance(data, dict) else None
        if err and err.get("code") == "rate_limit_exceeded":
            m = re.search(r"try again in ([\d.]+)s", err.get("message", ""))
            time.sleep(min(float(m.group(1)) + 2 if m else 15, 30))
            continue
        if err:
            print(f"خطای Groq: {err}")
            return None
        return data
    return None


def select_news(cands):
    if not GROQ_API_KEY or len(cands) < 1:
        return []
    listing = "\n".join(f"[{i}] ({c['source']}, {c['time'].strftime('%b %d %H:%M UTC')}) {c['title']}"
                        + (f" — {c['desc'][:200]}" if c["desc"] else "")
                        for i, c in enumerate(cands, 1))
    prompt = (
        "Below is a numbered list of real, recent news items mentioning gold or silver.\n"
        f"Select up to {NEWS_MAX} items that matter MOST for gold and silver prices (price moves, central-bank "
        "buying, ETF flows, Fed/dollar/yield drivers explicitly linked to metals, supply or demand news). "
        "Prefer concrete events over opinion, forecasts, or mining-company stories. Skip duplicates.\n"
        "For each, write ONE neutral sentence (max 30 words).\n"
        "STRICT RULES:\n"
        "- Use ONLY information stated in that item's title/description; add no facts, causes, numbers "
        "or predictions.\n"
        "- Paraphrase; do not copy the headline or description.\n"
        "- If the item is a report or attributed claim, keep the attribution. Never attribute price "
        "moves to a person.\n"
        "- Keep time qualifiers attached to numbers (e.g. 'for the week').\n"
        "- label = 1-2 word topic, e.g. Gold, Silver, Central Banks, ETFs, Macro, Supply.\n"
        + (FA_NEWS_RULES if IS_FA else "")
        + ('Return ONLY JSON: {"items":[{"id":<number>,"label":"<topic>","summary":"<sentence>"'
           + (("," + FA_NEWS_FIELDS) if IS_FA else "") + '}]}\n\n')
        + f"NEWS ITEMS:\n{listing}")
    for model in GROQ_MODELS:
        for effort in ("low", "medium"):
            data = call_groq({"model": model, "messages": [{"role": "user", "content": prompt}],
                              "temperature": 0.2, "max_tokens": 2500, "reasoning_effort": effort})
            if not data:
                continue
            try:
                raw = data["choices"][0]["message"].get("content") or ""
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                sel = json.loads(m.group(0))["items"] if m else []
            except Exception as e:
                print(f"خروجی Groq قابل خواندن نبود: {e}")
                continue
            if not sel:
                continue
            print(f"Groq ({model}/{effort}) انتخاب کرد. اعتبارسنجی:")
            v = validate(sel, cands)
            if len(v) >= 1:
                return v
    return []




# ================================================================ کارت تصویری

W = 1080
BG = (11, 14, 20); PANEL = (24, 29, 39); PANEL2 = (18, 22, 31); LINE = (38, 44, 56)
TEXT = (240, 244, 248); MUTED = (155, 165, 178); GREEN = (44, 200, 134); RED = (239, 83, 80)
GOLD = (232, 185, 60); SILVER = (200, 208, 218); BLUE = (91, 155, 255)
HEAT_COL = {SUP: (28, 110, 78), NEU: (52, 58, 72), HEAD: (130, 45, 48), STR: (140, 98, 22)}


def font(n, b=False):
    p = f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if b else ''}.ttf"
    return ImageFont.truetype(p, n) if os.path.exists(p) else ImageFont.load_default(size=n)


F38B, F30B, F28B, F24B, F22B, F20B, F18B = (font(38, 1), font(30, 1), font(28, 1), font(24, 1),
                                           font(22, 1), font(20, 1), font(18, 1))
F22, F21, F20, F18 = font(22), font(21), font(20), font(18)


def colr(v):
    return MUTED if v is None else GREEN if v > 0 else RED if v < 0 else MUTED


def fp(v):
    return "—" if v is None else f"{v:+.2f}%"


def right(d, x, y, t, f, fill):
    d.text((x - d.textlength(t, font=f), y), t, font=f, fill=fill)


def rr(d, xy, fill, r=18):
    d.rounded_rectangle(xy, radius=r, fill=fill)


def wrap(d, text, f, width):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= width:
            cur = t
        else:
            lines.append(cur); cur = w
    return lines + ([cur] if cur else [])


def section(d, y, title):
    d.text((78, y + 20), title, font=F18B, fill=BLUE)


def price_strip(d, y, g, s):
    for x, name, sym, acc, m in ((50, "GOLD", "XAU", GOLD, g), (560, "SILVER", "XAG", SILVER, s)):
        rr(d, (x, y, x + 470, y + 118), PANEL)
        d.text((x + 26, y + 18), name, font=F22B, fill=acc)
        right(d, x + 444, y + 20, f"{sym}/USD", F18, MUTED)
        d.text((x + 26, y + 52), f"${m['price']:,.2f}", font=F28B, fill=TEXT)
        right(d, x + 444, y + 56, f"{fp(m['day'])} day", F20B, colr(m["day"]))
        right(d, x + 444, y + 84, f"{fp(m['week'])} week", F18, colr(m["week"]))
    return y + 118 + 22


def drivers_panel(d, y, md):
    rr(d, (50, y, 1030, y + 290), PANEL)
    section(d, y, "FUNDAMENTAL DRIVERS")
    cells = [
        ("10-year real yield (TIPS)", f"{md['r10']:.2f}%" if md.get("r10") is not None else "N/A",
         f"{md['r105']:+.0f} bps 5 days" if md.get("r105") is not None else ""),
        ("U.S. dollar index", f"{md['usd']:.2f}" if md.get("usd") else "N/A",
         f"{fp(md.get('usd5'))} 5 days" if md.get("usd5") is not None else ""),
        ("10-year Treasury yield", f"{md['y10']:.2f}%" if md.get("y10") is not None else "N/A",
         f"{md['y105']:+.0f} bps 5 days" if md.get("y105") is not None else ""),
        ("Inflation expectations (10Y)", f"{md['be']:.2f}%" if md.get("be") is not None else "N/A",
         f"{md['be5']:+.0f} bps 5 days" if md.get("be5") is not None else ""),
        ("CPI inflation (y/y)", f"{md['cpi']:.1f}%" if md.get("cpi") is not None else "N/A",
         (f"{md['cpi_month']} · prior {md['cpi_prev']:.1f}%" if md.get("cpi_prev") is not None else "")),
    ]
    for i, (lab, val, sub) in enumerate(cells):
        col, row = i % 3, i // 3
        xx, yy = 78 + col * 315, y + 58 + row * 115
        d.text((xx, yy), lab, font=F18, fill=MUTED)
        d.text((xx, yy + 26), val, font=F24B, fill=TEXT)
        d.text((xx, yy + 60), sub, font=F18, fill=MUTED)
    return y + 290 + 10


def heat_panel(d, y, fg, fs):
    tl = [wrap(d, tally(fg), F18B, 285), wrap(d, tally(fs), F18B, 280)]
    extra = 26 * (max(len(t) for t in tl) - 1)
    h = 110 + len(fg) * 62 + 70 + extra
    rr(d, (50, y, 1030, y + h), PANEL)
    section(d, y, "FUNDAMENTAL HEAT MAP")
    d.text((440, y + 60), "GOLD", font=F18B, fill=GOLD)
    d.text((750, y + 60), "SILVER", font=F18B, fill=SILVER)
    yy = y + 92
    for (name, sg, vg), (_, ss, vs) in zip(fg, fs):
        for k, ln in enumerate(wrap(d, name, F20, 330)):
            d.text((78, yy + (16 if len(wrap(d, name, F20, 330)) == 1 else 3) + 24 * k), ln, font=F20, fill=TEXT)
        for x0, st, val in ((430, sg, vg), (740, ss, vs)):
            d.rounded_rectangle((x0, yy, x0 + 275, yy + 52), radius=10, fill=HEAT_COL[st])
            d.text((x0 + 14, yy + 5), st.upper(), font=F18B, fill=TEXT)
            d.text((x0 + 14, yy + 27), val, font=F18, fill=(215, 222, 230))
        yy += 62
    for x0, lines in zip((430, 740), tl):
        for k, ln in enumerate(lines):
            d.text((x0, yy + 6 + 26 * k), ln, font=F18B, fill=TEXT)
    yy += extra
    d.text((78, yy + 36), "Fixed rules on typical relationships — a snapshot of conditions, not a forecast.",
           font=F18, fill=MUTED)
    return y + h + 22


def cftc_panel(d, y, cf):
    rows = [(n, c) for n, c in (("Gold", cf["XAU"]), ("Silver", cf["XAG"])) if c.get("available")]
    if not rows:
        return y
    h = 70 + 88 * len(rows)
    rr(d, (50, y, 1030, y + h), PANEL)
    section(d, y, "LARGE SPECULATORS (CFTC · MANAGED MONEY)")
    yy = y + 58
    for n, c in rows:
        d.text((78, yy), n, font=F22B, fill=GOLD if n == "Gold" else SILVER)
        side = "Net long" if c["net"] >= 0 else "Net short"
        d.text((210, yy), f"{side} {abs(c['net']) / 1000:.1f}K contracts", font=F22, fill=TEXT)
        if c.get("net_change") is not None:
            right(d, 1005, yy, f"{c['net_change'] / 1000:+.1f}K vs prior week", F20B, colr(c["net_change"]))
        dt = datetime.strptime(c["date"], "%Y-%m-%d")
        pc = f" · {c['pctile']:.0f}th percentile of past year" if c.get("pctile") is not None else ""
        d.text((210, yy + 32), f"As of {dt.strftime('%b %d')} (published {(dt + timedelta(days=3)).strftime('%b %d')}){pc}",
               font=F18, fill=MUTED)
        yy += 88
    return y + h + 22


def ratio_panel(d, y, rs):
    rr(d, (50, y, 1030, y + 120), PANEL)
    section(d, y, "GOLD / SILVER RATIO")
    d.text((78, y + 54), f"{rs['now']:.1f}", font=F28B, fill=TEXT)
    d.text((78, y + 90), "ounces of silver per ounce of gold", font=F18, fill=MUTED)
    if rs.get("avg") is not None:
        d.text((470, y + 56), f"1-year average {rs['avg']:.1f}  ·  range {rs['lo']:.1f}–{rs['hi']:.1f}", font=F20, fill=TEXT)
        where = ("near the top of its 1-year range (silver relatively cheap vs gold)" if rs["pctile"] >= 85 else
                 "near the bottom of its 1-year range (silver relatively rich vs gold)" if rs["pctile"] <= 15 else
                 "within its 1-year range")
        txt = f"{rs['pctile']:.0f}th percentile — {where}"
        if IS_FA:
            where = ("نزدیک سقف بازه‌ی یک‌ساله (نقره نسبت به طلا ارزون‌تره)" if rs["pctile"] >= 85 else
                     "نزدیک کف بازه‌ی یک‌ساله (نقره نسبت به طلا گرون‌تره)" if rs["pctile"] <= 15 else
                     "داخل بازه‌ی یک‌ساله")
            txt = f"صدک {rs['pctile']:.0f} — {where}"
        for k, ln in enumerate(wrap(d, txt, F18, 530)):
            d.text((470, y + 86 + 22 * k), ln, font=F18, fill=MUTED)
    return y + 120 + 22


def text_panel(d, y, title, text, fill=PANEL2):
    ls = wrap(d, text, F22, 910)
    h = 64 + 32 * len(ls)
    rr(d, (50, y, 1030, y + h), fill)
    section(d, y, title)
    for k, ln in enumerate(ls):
        d.text((78, y + 50 + 32 * k), ln, font=F22, fill=TEXT)
    return y + h + 22


def news_panel(d, y, news):
    if not news:
        return y
    blocks = []
    for n in news:
        head = f"{n['label']}: "
        blocks.append((head, wrap(d, head + n["summary"] + f"  ({n['source']})", F21, 900)))
    h = 64 + sum(30 * len(b) + 18 for _, b in blocks)
    rr(d, (50, y, 1030, y + h), PANEL)
    section(d, y, "METALS NEWS")
    yy = y + 56
    for head, body in blocks:
        d.ellipse((66, yy + 9, 74, yy + 17), fill=GOLD)
        for k, ln in enumerate(body):
            if k == 0 and ln.startswith(head):
                d.text((82, yy), head, font=F22B, fill=TEXT)
                d.text((82 + d.textlength(head, font=F22B), yy + 1), ln[len(head):], font=F21, fill=TEXT)
            else:
                d.text((82, yy + 1), ln, font=F21, fill=TEXT)
            yy += 30
        yy += 18
    return y + h + 22


def render(g, s, cf, md, rs, summary, news, now):
    im = Image.new("RGB", (W, 3400), BG)
    d = ImageDraw.Draw(im)
    if IS_FA:
        d = MirrorDraw(d, W, GS_TR)
    d.text((55, 42), "TRADINGLIFE", font=F38B, fill=TEXT)
    d.text((55, 91), "Gold & Silver · Fundamental Analysis", font=F21, fill=MUTED)
    right(d, 1025, 48, "FUNDAMENTALS", F30B, GOLD)
    right(d, 1025, 92, now.strftime("%a, %b %d, %Y"), F22, TEXT)
    y = price_strip(d, 140, g, s)
    y = drivers_panel(d, y, md)
    d.text((60, y), "Typically: lower real yields, a weaker dollar and rising inflation expectations support metals.",
           font=F18, fill=MUTED)
    y += 40
    y = heat_panel(d, y, heat_factors(cf["XAU"], md), heat_factors(cf["XAG"], md))
    y = cftc_panel(d, y, cf)
    y = ratio_panel(d, y, rs)
    if summary:
        y = text_panel(d, y, "SUMMARY", summary)
    y = news_panel(d, y, news)
    rr(d, (50, y, 1030, y + 120), PANEL2)
    d.text((75, y + 20), f"TradingLife  •  Updated {now.strftime('%Y-%m-%d %H:%M')} ET", font=F18, fill=TEXT)
    d.text((75, y + 52), "Sources: U.S. Treasury (yields, TIPS) • ECB (dollar index) • BLS (CPI) • CFTC • Alpha Vantage",
           font=F18, fill=MUTED)
    d.text((75, y + 80), "News summarized from named sources • Not investment advice", font=F18, fill=MUTED)
    y += 120 + 30
    im = im.crop((0, 0, W, y))
    os.makedirs("metal_cards", exist_ok=True)
    p = "metal_cards/gold_silver_fundamentals.png"
    im.save(p, "PNG", optimize=True)
    return p


def caption_text(g, s, summary, cf, md, now):
    parts = [f"🥇 Gold & Silver Fundamentals — {now.strftime('%a, %b %d')}",
             f"Gold ${g['price']:,.2f} ({fp(g['day'])}) · Silver ${s['price']:,.2f} ({fp(s['day'])})", ""]
    if summary:
        parts.append(summary)
    c = cftc_sentence("Gold", cf["XAU"])
    if c:
        parts.append(c)
    parts.append(f"Heat map — Gold: {tally(heat_factors(cf['XAU'], md))}; "
                 f"Silver: {tally(heat_factors(cf['XAG'], md))}.")
    parts += ["", "#Gold #Silver"]
    txt = "\n".join(parts)
    if len(txt) > 1024:                       # سقف کپشن تلگرام
        txt = txt[:1024 - 20].rsplit("\n", 1)[0] + "\n\n#Gold #Silver"
    return txt


def send_photo(path, caption):
    with open(path, "rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                          data={"chat_id": CHAT_ID, "caption": caption}, files={"photo": f}, timeout=60)
    res = r.json()
    if not res.get("ok"):
        print(f"خطا در ارسال: {res}")
        raise SystemExit(1)
    print("کارت بنیادی طلا و نقره ارسال شد.")



# ================================================================ نسخه‌ی فارسی
from tl_fa import MirrorDraw, Translator, fa_date, hashtags, TEHRAN, FA_MONTHS


def month_fa(txt):
    try:
        d_ = datetime.strptime(txt, "%b %Y")
        return f"{FA_MONTHS[d_.month - 1]} {d_.year}"
    except (ValueError, TypeError):
        return txt


def _updated_fa(m):
    t = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=ET_TZ).astimezone(TEHRAN)
    return f"TradingLife · به‌روزرسانی {t.strftime('%H:%M')} به وقت تهران"


def _date_fa(m):
    return fa_date(datetime.strptime(m.group(0), "%a, %b %d, %Y"))


def _asof_fa(m):
    a = datetime.strptime(m.group(1), "%b %d"); b = datetime.strptime(m.group(2), "%b %d")
    pc = f" · صدک {m.group(3)} در یک سال اخیر" if m.group(3) else ""
    return f"وضعیت {fa_date(a, weekday=False, year=False)} (انتشار {fa_date(b, weekday=False, year=False)}){pc}"


GS_TR = Translator([
    (r"Gold & Silver · Fundamental Analysis", "طلا و نقره · تحلیل بنیادی"), (r"FUNDAMENTALS", "تحلیل بنیادی"),
    (r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), \w{3} \d{2}, \d{4}", _date_fa),
    (r"GOLD", "طلا"), (r"SILVER", "نقره"), (r"Gold", "طلا"), (r"Silver", "نقره"),
    (r"([+\-]?[\d.]+%|—) day", r"\1 روزانه"), (r"([+\-]?[\d.]+%|—) week", r"\1 هفتگی"),
    (r"FUNDAMENTAL DRIVERS", "عوامل بنیادی"),
    (r"10-year real yield \(TIPS\)", "بازده واقعی 10 ساله (TIPS)"), (r"U\.S\. dollar index", "شاخص دلار آمریکا"),
    (r"10-year Treasury yield", "بازده اسمی اوراق 10 ساله"), (r"Inflation expectations \(10Y\)", "تورم مورد انتظار (10 ساله)"),
    (r"CPI inflation \(y/y\)", "تورم CPI (سالانه)"),
    (r"(.+) bps 5 days", r"\1 واحد در 5 روز"), (r"(.+) 5 days", r"\1 در 5 روز"),
    (r"(\w{3} \d{4}) · prior (.+)", lambda m: f"{month_fa(m.group(1))} · قبلی {m.group(2)}"),
    (r"Typically: lower real yields, a weaker dollar and rising inflation expectations support metals\.",
     "معمولاً: بازده واقعی پایین‌تر، دلار ضعیف‌تر و تورم مورد انتظار بالاتر به نفع فلزات گران‌بهاست."),
    (r"FUNDAMENTAL HEAT MAP", "نقشه‌ی حرارتی بنیادی"),
    (r"Real yield, 10Y \(5 days\)", "بازده واقعی 10 ساله (5 روز)"), (r"U\.S\. dollar \(5 days\)", "دلار آمریکا (5 روز)"),
    (r"Inflation expectations \(5 days\)", "تورم مورد انتظار (5 روز)"), (r"Speculator flow \(week\)", "جریان سفته‌بازان (هفتگی)"),
    (r"Positioning vs 1 year", "موقعیت نسبت به یک سال"),
    (r"SUPPORTIVE", "حمایتی"), (r"NEUTRAL", "خنثی"), (r"HEADWIND", "فشار منفی"), (r"STRETCHED", "کشیده / شلوغ"),
    (r"(.+) bps", r"\1 واحد"), (r"(.+)K contracts", r"\1K قرارداد"),
    (r"(\d+)th pct · Crowded long", r"صدک \1 · خرید شلوغ"), (r"(\d+)th pct · Light", r"صدک \1 · سبک"),
    (r"(\d+)th pct · Mid-range", r"صدک \1 · میانه"),
    (r"Fixed rules on typical relationships — a snapshot of conditions, not a forecast\.",
     "قوانین ثابت بر اساس روابط معمول — تصویری از شرایط فعلی، نه پیش‌بینی."),
    (r"LARGE SPECULATORS \(CFTC · MANAGED MONEY\)", "سفته‌بازان بزرگ (CFTC · صندوق‌های مدیریت‌شده)"),
    (r"Net long (.+)K contracts", r"خالص خرید \1K قرارداد"), (r"Net short (.+)K contracts", r"خالص فروش \1K قرارداد"),
    (r"(.+)K vs prior week", r"\1K نسبت به هفته‌ی قبل"),
    (r"As of (\w{3} \d{2}) \(published (\w{3} \d{2})\)(?: · (\d+)th percentile of past year)?", _asof_fa),
    (r"GOLD / SILVER RATIO", "نسبت طلا به نقره"),
    (r"ounces of silver per ounce of gold", "اونس نقره در برابر هر اونس طلا"),
    (r"1-year average (.+)  ·  range (.+)", r"میانگین یک‌ساله \1 · بازه \2"),
    (r"SUMMARY", "خلاصه"), (r"METALS NEWS", "اخبار طلا و نقره"),
    (r"TradingLife  •  Updated (.+) ET", _updated_fa),
    (r"Sources: U\.S\. Treasury \(yields, TIPS\) • ECB \(dollar index\) • BLS \(CPI\) • CFTC • Alpha Vantage",
     "منابع: خزانه‌داری آمریکا (بازده و TIPS) · ECB (شاخص دلار) · BLS (تورم) · CFTC · Alpha Vantage"),
    (r"News summarized from named sources • Not investment advice", "خلاصه‌ی خبر از منابع ذکرشده · توصیه‌ی سرمایه‌گذاری نیست"),
])


def summary_sentence_fa(md):
    def mv(label, v, unit, small):
        if v is None:
            return None
        if abs(v) < small:
            return f"{label} تقریباً بدون تغییر بود"
        amt = f"{abs(v):.1f}%" if unit == "%" else f"{abs(v):.0f} واحد پایه"
        return f"{label} {amt} {'بالا رفت' if v > 0 else 'پایین اومد'}"
    bits = [x for x in (mv("بازده واقعی 10 ساله", md.get("r105"), "bps", 1),
                        mv("شاخص دلار", md.get("usd5"), "%", 0.05),
                        mv("تورم مورد انتظار بازار", md.get("be5"), "bps", 1)) if x]
    txt = ("در 5 روز کاری گذشته " + "، ".join(bits[:-1]) + (" و " if len(bits) > 1 else "") + bits[-1] + ".") if bits else ""
    if md.get("cpi") is not None and md.get("cpi_prev") is not None:
        d_ = md["cpi"] - md["cpi_prev"]
        if d_ >= 0.05:
            txt += f" تورم CPI آمریکا در {month_fa(md['cpi_month'])} از {md['cpi_prev']:.1f}% به {md['cpi']:.1f}% رسید و شتاب گرفت."
        elif d_ <= -0.05:
            txt += f" تورم CPI آمریکا در {month_fa(md['cpi_month'])} از {md['cpi_prev']:.1f}% به {md['cpi']:.1f}% کاهش یافت."
        else:
            txt += f" تورم CPI آمریکا در {month_fa(md['cpi_month'])} در {md['cpi']:.1f}% ثابت موند."
    return txt.strip()


def caption_fa(g, s, summary, cf, md, now):
    parts = [f"🥇 تحلیل بنیادی طلا و نقره — {fa_date(now.astimezone(TEHRAN), year=False)}",
             f"طلا ${g['price']:,.2f} ({fp(g['day'])}) · نقره ${s['price']:,.2f} ({fp(s['day'])})", ""]
    if summary:
        parts.append(summary)
    c = cftc_sentence("Gold", cf["XAU"])
    if c:
        parts.append(c)
    parts.append(f"نقشه‌ی حرارتی — طلا: {tally(heat_factors(cf['XAU'], md))}؛ "
                 f"نقره: {tally(heat_factors(cf['XAG'], md))}.")
    parts += ["", hashtags("طلا", "نقره", "تحلیل_بنیادی")]
    txt = "\n".join(parts)
    if len(txt) > 1024:
        txt = txt[:990].rsplit("\n", 1)[0] + "\n\n" + hashtags("طلا", "نقره", "تحلیل_بنیادی")
    return txt


def main():
    now = datetime.now(ET_TZ)
    if now.weekday() >= 5:
        print("آخر هفته - بازار طلا و نقره بسته‌ست، پستی ارسال نشد.")
        return
    today = now.date()
    g = metal_data(METALS[0], today)
    s = metal_data(METALS[1], today)
    cf = {m["symbol"]: cftc(m) for m in METALS}
    md = macro()
    rs = ratio_stats(g, s)
    summary = summary_sentence_fa(md) if IS_FA else summary_sentence(md)
    print(f"خلاصه: {summary}")
    news = select_news(collect_news())
    if not news:
        print("بخش اخبار حذف شد (خبر معتبر مرتبط با طلا/نقره پیدا نشد).")
    path = render(g, s, cf, md, rs, summary, news, now)
    send_photo(path, (caption_fa if IS_FA else caption_text)(g, s, summary, cf, md, now))


if __name__ == "__main__":
    main()
