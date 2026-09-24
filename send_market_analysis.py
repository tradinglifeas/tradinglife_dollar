import os
import io
import csv
import json
import math
import time
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

ALPHA_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
RUN_MODE = os.getenv("RUN_MODE", "full").strip().lower()
ASSET_FILTER = os.getenv("ASSET_FILTER", "ALL").strip().upper()

BASE_URL = "https://www.alphavantage.co/query"
CACHE_FILE = "market_analysis_cache.json"
OUTPUT_DIR = "market_cards"

ASSETS = [
    {"symbol": "XAU", "name": "Gold", "kind": "metal", "api_symbol": "GOLD"},
    {"symbol": "XAG", "name": "Silver", "kind": "metal", "api_symbol": "SILVER"},
    {"symbol": "BTC", "name": "Bitcoin", "kind": "crypto"},
    {"symbol": "ETH", "name": "Ethereum", "kind": "crypto"},
    {"symbol": "SOL", "name": "Solana", "kind": "crypto"},
    {"symbol": "XRP", "name": "XRP", "kind": "crypto"},
    {"symbol": "ADA", "name": "Cardano", "kind": "crypto"},
    {"symbol": "DOGE", "name": "Dogecoin", "kind": "crypto"},
]

# -----------------------------
# HTTP helpers
# -----------------------------

def get_json(url, params=None, timeout=35):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def av_request(function, **kwargs):
    params = {"function": function, "apikey": ALPHA_KEY, **kwargs}
    data = get_json(BASE_URL, params=params)
    for key in ("Information", "Note", "Error Message"):
        if key in data:
            raise RuntimeError(f"Alpha Vantage: {data[key]}")
    return data

def polite_pause():
    # Keeps bursts gentle; the free plan also has a daily request limit.
    time.sleep(1.2)

# -----------------------------
# Alpha Vantage parsers
# -----------------------------

def parse_metal_history(data):
    """
    Alpha Vantage commodity responses have changed shape across endpoints.
    This parser accepts:
      - {"data": [{"date": "...", "value": "..."}]}
      - {"data": [{"date": "...", "price": "..."}]}
      - {"data": {"2026-09-22": "..." ...}}
      - {"Time Series ...": {"2026-09-22": {...}}}
      - nested dict/list variants containing date/timestamp + value/price/close
    """
    out = []

    def to_float(v):
        try:
            if v in (None, "", ".", "null", "None", "N/A"):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    def add_point(date, value, row=None):
        value = to_float(value)
        if not date or value is None:
            return
        row = row if isinstance(row, dict) else {}
        o = to_float(row.get("open")) or value
        h = to_float(row.get("high")) or value
        l = to_float(row.get("low")) or value
        c = (
            to_float(row.get("close"))
            or to_float(row.get("price"))
            or to_float(row.get("value"))
            or to_float(row.get("rate"))
            or value
        )
        out.append({
            "date": str(date),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": None,
            "market_cap": None,
        })

    def row_value(row):
        if not isinstance(row, dict):
            return to_float(row)
        # Exact common field names first.
        for key in ("value", "price", "close", "rate", "spot", "usd"):
            if key in row:
                v = to_float(row.get(key))
                if v is not None:
                    return v
        # Then any numeric field whose name looks price-like.
        for k, v in row.items():
            lk = str(k).lower()
            if any(token in lk for token in ("value", "price", "close", "rate", "spot")):
                num = to_float(v)
                if num is not None:
                    return num
        return None

    # 1) Standard `data` container.
    container = data.get("data") if isinstance(data, dict) else None
    if isinstance(container, list):
        for row in container:
            if not isinstance(row, dict):
                continue
            date = row.get("date") or row.get("timestamp") or row.get("time")
            add_point(date, row_value(row), row)

    elif isinstance(container, dict):
        for date, row in container.items():
            if isinstance(row, dict):
                add_point(
                    row.get("date") or row.get("timestamp") or date,
                    row_value(row),
                    row,
                )
            else:
                add_point(date, row)

    # 2) Time-series-style top-level container.
    if not out and isinstance(data, dict):
        for key, value in data.items():
            if not isinstance(value, dict):
                continue
            lk = str(key).lower()
            if "time series" in lk or "history" in lk or "prices" in lk:
                for date, row in value.items():
                    if isinstance(row, dict):
                        add_point(
                            row.get("date") or row.get("timestamp") or date,
                            row_value(row),
                            row,
                        )
                    else:
                        add_point(date, row)

    # 3) Generic recursive scan for objects with a date/timestamp.
    if not out:
        def walk(obj):
            if isinstance(obj, dict):
                date = obj.get("date") or obj.get("timestamp") or obj.get("time")
                if date:
                    val = row_value(obj)
                    if val is not None:
                        add_point(date, val, obj)
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)
        walk(data)

    # De-duplicate by date, then sort.
    dedup = {}
    for row in out:
        dedup[row["date"]] = row
    out = list(dedup.values())
    out.sort(key=lambda x: x["date"])

    if len(out) < 55:
        # Make GitHub Actions show what Alpha Vantage actually returned,
        # without ever printing the API key (it is not part of the response).
        preview = json.dumps(data, ensure_ascii=False)[:1200]
        raise RuntimeError(
            f"Not enough metal history returned ({len(out)} rows). "
            f"Alpha Vantage response preview: {preview}"
        )
    return out

def pick_num(row, contains, prefer_usd=False):
    candidates = []
    for k, v in row.items():
        lk = k.lower()
        if contains in lk:
            try:
                n = float(v)
                usd = "(usd)" in lk or "usd" in lk
                candidates.append((usd, n))
            except (TypeError, ValueError):
                pass
    if not candidates:
        return None
    if prefer_usd:
        usd_vals = [n for usd, n in candidates if usd]
        if usd_vals:
            return usd_vals[0]
    return candidates[0][1]

def parse_crypto_history(data):
    ts_key = next((k for k in data if "time series" in k.lower()), None)
    if not ts_key or not isinstance(data[ts_key], dict):
        raise RuntimeError(f"Crypto daily series missing. Keys: {list(data.keys())[:8]}")
    out = []
    for date, row in data[ts_key].items():
        if not isinstance(row, dict):
            continue
        o = pick_num(row, "open", True)
        h = pick_num(row, "high", True)
        l = pick_num(row, "low", True)
        c = pick_num(row, "close", True)
        vol = pick_num(row, "volume", False)
        mcap = pick_num(row, "market cap", True)
        if c is None:
            continue
        out.append({
            "date": date,
            "open": o if o is not None else c,
            "high": h if h is not None else c,
            "low": l if l is not None else c,
            "close": c,
            "volume": vol,
            "market_cap": mcap,
        })
    out.sort(key=lambda x: x["date"])
    if len(out) < 55:
        raise RuntimeError(f"Not enough crypto history returned ({len(out)} rows).")
    return out

def fetch_history(asset):
    if asset["kind"] == "metal":
        data = av_request("GOLD_SILVER_HISTORY",
                          symbol=asset["api_symbol"], interval="daily")
        polite_pause()
        return parse_metal_history(data)

    data = av_request("DIGITAL_CURRENCY_DAILY",
                      symbol=asset["symbol"], market="USD")
    polite_pause()
    return parse_crypto_history(data)

def fetch_spot(asset):
    if asset["kind"] == "metal":
        data = av_request("GOLD_SILVER_SPOT", symbol=asset["api_symbol"])
        polite_pause()
        # Current documented response exposes a price field; keep fallbacks.
        for key in ("price", "Price", "spot_price", "Spot Price", "ask", "bid"):
            try:
                if key in data:
                    return float(data[key])
            except (TypeError, ValueError):
                pass
        # Search nested dictionaries as a fallback.
        for value in data.values():
            if isinstance(value, dict):
                for key, v in value.items():
                    if "price" in key.lower() or "rate" in key.lower():
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
        raise RuntimeError(f"Could not read {asset['symbol']} spot price.")

    data = av_request("CURRENCY_EXCHANGE_RATE",
                      from_currency=asset["symbol"], to_currency="USD")
    polite_pause()
    info = data.get("Realtime Currency Exchange Rate", {})
    rate = info.get("5. Exchange Rate")
    if rate is None:
        raise RuntimeError(f"Could not read {asset['symbol']}/USD exchange rate.")
    return float(rate)

# -----------------------------
# FRED macro data (no Alpha call)
# -----------------------------

def fred_series(series_id):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
    r = requests.get(url, params={"id": series_id}, timeout=35)
    r.raise_for_status()
    rows = []
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        try:
            rows.append((row["DATE"], float(row[series_id])))
        except (KeyError, TypeError, ValueError):
            continue
    return rows

def latest_change(rows, lookback=5):
    if not rows:
        return None, None
    latest = rows[-1][1]
    old_idx = max(0, len(rows) - 1 - lookback)
    old = rows[old_idx][1]
    pct = ((latest / old) - 1) * 100 if old else None
    return latest, pct

def macro_context():
    result = {}
    try:
        usd = fred_series("DTWEXBGS")  # Trade Weighted U.S. Dollar Index: Broad
        val, chg = latest_change(usd, 5)
        result["usd_index"] = val
        result["usd_5d_pct"] = chg
    except Exception as e:
        print("FRED USD error:", e)

    try:
        y10 = fred_series("DGS10")  # 10-Year Treasury Constant Maturity Rate
        val, _ = latest_change(y10, 5)
        old = y10[max(0, len(y10) - 6)][1] if y10 else None
        result["yield_10y"] = val
        result["yield_5d_bps"] = (val - old) * 100 if val is not None and old is not None else None
    except Exception as e:
        print("FRED 10Y error:", e)

    try:
        cpi = fred_series("CPIAUCSL")
        if len(cpi) >= 13:
            latest = cpi[-1][1]
            year_ago = cpi[-13][1]
            result["cpi_yoy"] = ((latest / year_ago) - 1) * 100
    except Exception as e:
        print("FRED CPI error:", e)

    return result

# -----------------------------
# Indicators
# -----------------------------

def ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = sum(values[:period]) / period
    for x in values[period:]:
        value = x * k + value * (1 - k)
    return value

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def macd(values):
    if len(values) < 35:
        return None, None
    line = []
    for i in range(26, len(values) + 1):
        subset = values[:i]
        fast = ema(subset, 12)
        slow = ema(subset, 26)
        line.append(fast - slow)
    signal = ema(line, 9)
    return line[-1], signal

def pct_change(a, b):
    return ((a / b) - 1) * 100 if b else 0.0

def avg_abs_return(values, n=14):
    vals = values[-(n + 1):]
    rets = [abs(pct_change(vals[i], vals[i-1])) / 100 for i in range(1, len(vals))]
    return statistics.mean(rets) if rets else 0.01

def compute_analysis(asset, hist, macro, spot=None):
    rows = [dict(x) for x in hist[-180:]]
    if spot is not None and rows:
        # Use current spot as the newest observation for intraday refresh.
        last = dict(rows[-1])
        last["close"] = spot
        last["high"] = max(last.get("high") or spot, spot)
        last["low"] = min(last.get("low") or spot, spot)
        rows[-1] = last

    closes = [r["close"] for r in rows]
    current = closes[-1]
    prev = closes[-2]
    change = pct_change(current, prev)

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi14 = rsi(closes, 14)
    macd_val, macd_sig = macd(closes)
    sma20 = statistics.mean(closes[-20:])
    support = min(closes[-20:])
    resistance = max(closes[-20:])
    ret7 = pct_change(current, closes[-8]) if len(closes) >= 8 else 0
    ret30 = pct_change(current, closes[-31]) if len(closes) >= 31 else 0

    tech = 0
    tech += 1 if current > ema20 else -1
    tech += 1 if ema20 > ema50 else -1
    if rsi14 >= 55:
        tech += 1
    elif rsi14 <= 45:
        tech -= 1
    tech += 1 if macd_val > macd_sig else -1
    tech += 1 if current > sma20 else -1

    fundamental = 0.0
    fundamental_lines = []

    if asset["kind"] == "metal":
        usd = macro.get("usd_index")
        usdchg = macro.get("usd_5d_pct")
        y10 = macro.get("yield_10y")
        ychg = macro.get("yield_5d_bps")
        cpi = macro.get("cpi_yoy")

        if usdchg is not None:
            if usdchg <= -0.10:
                fundamental += 1
            elif usdchg >= 0.10:
                fundamental -= 1
            arrow = "DOWN" if usdchg < 0 else "UP"
            fundamental_lines.append(("USD Index (5D)", f"{arrow} {abs(usdchg):.2f}%"))
        else:
            fundamental_lines.append(("USD Index (5D)", "N/A"))

        if ychg is not None:
            if ychg <= -2:
                fundamental += 1
            elif ychg >= 2:
                fundamental -= 1
            arrow = "DOWN" if ychg < 0 else "UP"
            fundamental_lines.append(("US 10Y (5D)", f"{arrow} {abs(ychg):.0f} bps"))
        else:
            fundamental_lines.append(("US 10Y (5D)", "N/A"))

        fundamental_lines.append(("US 10Y Yield", f"{y10:.2f}%" if y10 is not None else "N/A"))
        fundamental_lines.append(("US CPI YoY", f"{cpi:.2f}%" if cpi is not None else "N/A"))

    else:
        latest = rows[-1]
        volumes = [r["volume"] for r in rows[-20:] if r.get("volume") is not None]
        vol = latest.get("volume")
        avgvol = statistics.mean(volumes) if volumes else None
        vol_ratio = (vol / avgvol) if vol is not None and avgvol else None

        # Volume confirms the direction but is not treated as a standalone fundamental fact.
        if vol_ratio is not None and vol_ratio >= 1.20:
            fundamental += 1 if ret7 > 0 else -1

        mcap = latest.get("market_cap")
        fundamental_lines.append(("24H Volume", compact_money(vol) if vol is not None else "N/A"))
        fundamental_lines.append(("Volume / 20D Avg", f"{vol_ratio:.2f}x" if vol_ratio is not None else "N/A"))
        fundamental_lines.append(("Market Cap", compact_money(mcap) if mcap is not None else "N/A"))
        fundamental_lines.append(("30D Return", f"{ret30:+.2f}%"))

    total = tech + fundamental

    if total >= 5:
        signal = "STRONG BUY"
        signal_side = "buy"
    elif total >= 3:
        signal = "BUY SETUP"
        signal_side = "buy"
    elif total <= -5:
        signal = "STRONG SELL"
        signal_side = "sell"
    elif total <= -3:
        signal = "SELL SETUP"
        signal_side = "sell"
    else:
        signal = "WAIT / NEUTRAL"
        signal_side = "neutral"

    risk_unit = max(current * avg_abs_return(closes, 14) * 1.5, current * 0.005)

    if signal_side == "buy":
        entry_low = current - 0.25 * risk_unit
        entry_high = current + 0.10 * risk_unit
        stop = current - risk_unit
        target1 = current + 1.5 * risk_unit
        target2 = current + 2.5 * risk_unit
    elif signal_side == "sell":
        entry_low = current - 0.10 * risk_unit
        entry_high = current + 0.25 * risk_unit
        stop = current + risk_unit
        target1 = current - 1.5 * risk_unit
        target2 = current - 2.5 * risk_unit
    else:
        entry_low = entry_high = stop = target1 = target2 = None

    return {
        "symbol": asset["symbol"],
        "name": asset["name"],
        "kind": asset["kind"],
        "price": current,
        "change_pct": change,
        "date": rows[-1]["date"],
        "rsi": rsi14,
        "ema20": ema20,
        "ema50": ema50,
        "macd": macd_val,
        "macd_signal": macd_sig,
        "support": support,
        "resistance": resistance,
        "return7": ret7,
        "return30": ret30,
        "tech_score": tech,
        "fund_score": fundamental,
        "total_score": total,
        "signal": signal,
        "signal_side": signal_side,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "target1": target1,
        "target2": target2,
        "stop": stop,
        "fundamental_lines": fundamental_lines,
        "closes": closes[-60:],
    }

# -----------------------------
# Formatting
# -----------------------------

def compact_money(v):
    if v is None:
        return "N/A"
    v = float(v)
    if abs(v) >= 1e12:
        return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.2f}M"
    if abs(v) >= 1e3:
        return f"${v/1e3:.2f}K"
    return f"${v:,.2f}"

def price_text(v):
    if v >= 1000:
        return f"${v:,.2f}"
    if v >= 10:
        return f"${v:,.3f}"
    if v >= 1:
        return f"${v:,.4f}"
    return f"${v:,.6f}"

def score_text(v):
    return f"{v:+.1f}" if isinstance(v, float) else f"{v:+d}"

# -----------------------------
# Card rendering
# -----------------------------

W, H = 1080, 1350
BG = (12, 15, 22)
PANEL = (24, 29, 39)
PANEL2 = (18, 22, 31)
TEXT = (240, 244, 248)
MUTED = (157, 167, 181)
GREEN = (45, 200, 135)
RED = (239, 83, 80)
AMBER = (245, 185, 66)
GOLD = (212, 175, 55)
SILVER = (195, 202, 212)
BLUE = (91, 155, 255)

def font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

F12 = font(22)
F14 = font(26)
F16 = font(30)
F18 = font(34, True)
F20 = font(40, True)
F24 = font(48, True)
F32 = font(66, True)

def rounded(draw, xy, radius=24, fill=PANEL, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)

def metric_row(draw, x, y, label, value, value_color=TEXT):
    draw.text((x, y), label, font=F14, fill=MUTED)
    bbox = draw.textbbox((0, 0), value, font=F14)
    draw.text((x + 430 - (bbox[2]-bbox[0]), y), value, font=F14, fill=value_color)

def draw_sparkline(draw, box, values, accent):
    x1, y1, x2, y2 = box
    if len(values) < 2:
        return
    mn, mx = min(values), max(values)
    span = mx - mn or 1.0
    pts = []
    for i, v in enumerate(values):
        x = x1 + (x2-x1) * i / (len(values)-1)
        y = y2 - (y2-y1) * (v-mn) / span
        pts.append((x, y))
    for g in range(1, 4):
        gy = y1 + (y2-y1)*g/4
        draw.line((x1, gy, x2, gy), fill=(45, 50, 62), width=1)
    draw.line(pts, fill=accent, width=5, joint="curve")

def render_card(a):
    accent = GOLD if a["symbol"] == "XAU" else SILVER if a["symbol"] == "XAG" else BLUE
    signal_color = GREEN if a["signal_side"] == "buy" else RED if a["signal_side"] == "sell" else AMBER

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # Header
    d.text((60, 48), "TRADINGLIFE", font=F20, fill=TEXT)
    d.text((60, 100), "Market Intelligence", font=F14, fill=MUTED)
    d.text((1020, 55), f"{a['name'].upper()}  |  {a['symbol']}/USD",
           font=F16, fill=accent, anchor="ra")

    # Price block
    rounded(d, (50, 160, 1030, 390), fill=PANEL)
    d.text((85, 195), price_text(a["price"]), font=F32, fill=TEXT)
    change_color = GREEN if a["change_pct"] >= 0 else RED
    d.text((88, 285), f"{a['change_pct']:+.2f}% vs prior daily close",
           font=F16, fill=change_color)
    d.text((720, 190), "ALGORITHMIC SIGNAL", font=F12, fill=MUTED)
    rounded(d, (720, 235, 990, 315), radius=18, fill=signal_color)
    d.text((855, 275), a["signal"], font=F18, fill=(10, 13, 18), anchor="mm")
    d.text((720, 335), f"Score {score_text(a['total_score'])}", font=F14, fill=TEXT)

    # Sparkline
    rounded(d, (50, 420, 1030, 650), fill=PANEL2)
    d.text((78, 445), "60-DAY PRICE TREND", font=F12, fill=MUTED)
    draw_sparkline(d, (85, 500, 995, 620), a["closes"], accent)

    # Technical panel
    rounded(d, (50, 680, 520, 1050), fill=PANEL)
    d.text((80, 710), "TECHNICAL", font=F18, fill=TEXT)
    macd_label = "Bullish" if a["macd"] > a["macd_signal"] else "Bearish"
    macd_color = GREEN if macd_label == "Bullish" else RED
    metric_row(d, 80, 770, "RSI (14)", f"{a['rsi']:.1f}")
    metric_row(d, 80, 815, "EMA 20", price_text(a["ema20"]))
    metric_row(d, 80, 860, "EMA 50", price_text(a["ema50"]))
    metric_row(d, 80, 905, "MACD", macd_label, macd_color)
    metric_row(d, 80, 950, "Support", price_text(a["support"]))
    metric_row(d, 80, 995, "Resistance", price_text(a["resistance"]))

    # Fundamental/market panel
    rounded(d, (550, 680, 1030, 1050), fill=PANEL)
    title = "MACRO / FUNDAMENTAL" if a["kind"] == "metal" else "MARKET / FUNDAMENTAL"
    d.text((580, 710), title, font=F18, fill=TEXT)
    y = 770
    for label, value in a["fundamental_lines"][:4]:
        metric_row(d, 580, y, label, value)
        y += 52
    metric_row(d, 580, 978, "7D Return", f"{a['return7']:+.2f}%",
               GREEN if a["return7"] >= 0 else RED)
    metric_row(d, 580, 1023, "Score", score_text(a["fund_score"]))

    # Trade plan
    rounded(d, (50, 1080, 1030, 1260), fill=PANEL2)
    d.text((80, 1105), "SETUP PLAN", font=F18, fill=TEXT)
    if a["signal_side"] == "neutral":
        d.text((80, 1160), f"Wait for confirmation above {price_text(a['resistance'])} or below {price_text(a['support'])}.",
               font=F14, fill=MUTED)
        d.text((80, 1205), "No active entry/target while the model is neutral.", font=F14, fill=MUTED)
    else:
        side = "BUY" if a["signal_side"] == "buy" else "SELL"
        d.text((80, 1160), f"{side} entry", font=F14, fill=MUTED)
        d.text((255, 1160), f"{price_text(a['entry_low'])} – {price_text(a['entry_high'])}", font=F14, fill=TEXT)
        d.text((600, 1160), "Stop / invalidation", font=F14, fill=MUTED)
        d.text((845, 1160), price_text(a["stop"]), font=F14, fill=RED)
        d.text((80, 1210), "Target 1", font=F14, fill=MUTED)
        d.text((255, 1210), price_text(a["target1"]), font=F14, fill=GREEN)
        d.text((600, 1210), "Target 2", font=F14, fill=MUTED)
        d.text((845, 1210), price_text(a["target2"]), font=F14, fill=GREEN)

    # Footer
    tehran = datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")
    d.text((55, 1290), f"Prepared by TradingLife  •  Updated {tehran} Iran",
           font=F12, fill=MUTED)
    d.text((1025, 1290), "Data: Alpha Vantage + FRED  •  Algorithmic setup, not financial advice",
           font=font(18), fill=MUTED, anchor="ra")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, f"{a['symbol'].lower()}_tradinglife.png")
    img.save(path, "PNG", optimize=True)
    return path

# -----------------------------
# Telegram
# -----------------------------

def send_photo(path, a):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    caption = (
        f"TradingLife | {a['name']} ({a['symbol']}/USD)\n"
        f"Algorithmic signal: {a['signal']} | Score {score_text(a['total_score'])}\n"
        f"Educational market analysis — not financial advice."
    )
    with open(path, "rb") as f:
        r = requests.post(url, data={"chat_id": CHAT_ID, "caption": caption},
                          files={"photo": f}, timeout=60)
    r.raise_for_status()
    result = r.json()
    if not result.get("ok"):
        raise RuntimeError(result)

# -----------------------------
# Cache
# -----------------------------

def save_cache(histories, macro):
    payload = {
        "saved_at": datetime.utcnow().isoformat() + "Z",
        "macro": macro,
        "histories": histories,
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_cache():
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

# -----------------------------
# Main
# -----------------------------

def main():
    global RUN_MODE

    selected_assets = ASSETS if ASSET_FILTER == "ALL" else [
        a for a in ASSETS if a["symbol"] == ASSET_FILTER
    ]
    if not selected_assets:
        raise RuntimeError(f"Unknown ASSET_FILTER: {ASSET_FILTER}")

    histories = {}
    macro = {}

    if RUN_MODE == "refresh":
        try:
            cache = load_cache()
            histories = cache["histories"]
            macro = cache.get("macro", {})
            print("Loaded cached histories for refresh run.")
        except Exception as e:
            print("Refresh cache unavailable; falling back to full:", e)
            RUN_MODE = "full"

    if RUN_MODE == "full":
        macro = macro_context()
        for asset in selected_assets:
            print(f"History: {asset['symbol']}")
            histories[asset["symbol"]] = fetch_history(asset)
        # Only the scheduled/full ALL run should replace the shared cache.
        if ASSET_FILTER == "ALL":
            save_cache(histories, macro)

    analyses = []
    failures = []

    # Every run refreshes current spot/exchange rate.
    for asset in selected_assets:
        try:
            print(f"Spot: {asset['symbol']}")
            spot = fetch_spot(asset)
            analysis = compute_analysis(
                asset,
                histories[asset["symbol"]],
                macro,
                spot=spot
            )
            analyses.append(analysis)
        except Exception as e:
            failures.append(f"{asset['symbol']}: {e}")
            print("ERROR:", failures[-1])

    if not analyses:
        raise RuntimeError("No market cards could be built. " + " | ".join(failures))

    for a in analyses:
        path = render_card(a)
        send_photo(path, a)
        print("Sent:", a["symbol"], path)
        time.sleep(0.8)

    if failures:
        msg = "TradingLife market-card warnings:\n" + "\n".join(failures)
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=30
        )

    print(f"Done. Mode={RUN_MODE}. Filter={ASSET_FILTER}. Cards={len(analyses)}.")

if __name__ == "__main__":
    main()
