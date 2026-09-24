import os, io, csv, json, time, statistics
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from PIL import Image, ImageDraw, ImageFont

ALPHA_KEY = os.environ['ALPHA_VANTAGE_API_KEY']
BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
RUN_MODE = os.getenv('RUN_MODE', 'full').lower()
ASSET_FILTER = os.getenv('ASSET_FILTER', 'ALL').upper()

AV = 'https://www.alphavantage.co/query'
BINANCE_FUTURES = 'https://fapi.binance.com'
BYBIT_PUBLIC = 'https://api.bybit.com'
OKX_PUBLIC = 'https://www.okx.com'
CFTC_DISAGG = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
CFTC_LEGACY = 'https://publicreporting.cftc.gov/resource/6dca-aqww.json'
CACHE = 'market_intelligence_cache.json'
STATE = 'tradinglife_state.json'
OUT = 'market_cards'

ASSETS = [
    {'symbol':'XAU','name':'Gold','kind':'metal','api_symbol':'GOLD','cftc':'disagg','code':'088691'},
    {'symbol':'XAG','name':'Silver','kind':'metal','api_symbol':'SILVER','cftc':'disagg','code':'084691'},
    {'symbol':'BTC','name':'Bitcoin','kind':'crypto','cftc':'legacy','code':'133741','binance':'BTCUSDT'},
    {'symbol':'ETH','name':'Ethereum','kind':'crypto','cftc':'legacy','code':'146021','binance':'ETHUSDT'},
    {'symbol':'SOL','name':'Solana','kind':'crypto','cftc':None,'code':None,'binance':'SOLUSDT'},
    {'symbol':'XRP','name':'XRP','kind':'crypto','cftc':None,'code':None,'binance':'XRPUSDT'},
    {'symbol':'ADA','name':'Cardano','kind':'crypto','cftc':None,'code':None,'binance':'ADAUSDT'},
    {'symbol':'DOGE','name':'Dogecoin','kind':'crypto','cftc':None,'code':None,'binance':'DOGEUSDT'},
]

def sf(v, default=None):
    try:
        if v in (None,'','.', 'N/A','null'): return default
        return float(v)
    except: return default

def clamp(v,a=0,b=100): return max(a,min(b,v))
def pct(a,b): return ((a/b)-1)*100 if b not in (None,0) else 0.0

def get_json(url, params=None, timeout=40):
    r=requests.get(url,params=params,timeout=timeout); r.raise_for_status(); return r.json()

def av(function, **kwargs):
    d=get_json(AV, {'function':function,'apikey':ALPHA_KEY,**kwargs})
    for k in ('Information','Note','Error Message'):
        if k in d: raise RuntimeError('Alpha Vantage: '+str(d[k]))
    time.sleep(1)
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

def pick(row, token, prefer_usd=False):
    arr=[]
    for k,v in row.items():
        if token in k.lower() and sf(v) is not None: arr.append(('usd' in k.lower(),sf(v)))
    if not arr:return None
    if prefer_usd:
        for isusd,v in arr:
            if isusd:return v
    return arr[0][1]

def parse_crypto(d):
    key=next((k for k in d if 'time series' in k.lower()),None)
    if not key or not isinstance(d[key],dict): raise RuntimeError('Crypto time series missing')
    out=[]
    for date,row in d[key].items():
        c=pick(row,'close',True)
        if c is None: continue
        out.append({'date':date,'open':pick(row,'open',True) or c,'high':pick(row,'high',True) or c,
                    'low':pick(row,'low',True) or c,'close':c,'volume':pick(row,'volume'),
                    'market_cap':pick(row,'market cap',True)})
    out.sort(key=lambda x:x['date'])
    if len(out)<55: raise RuntimeError(f'Not enough crypto history ({len(out)} rows)')
    return out

def history(asset):
    if asset['kind']=='metal': return parse_metal(av('GOLD_SILVER_HISTORY',symbol=asset['api_symbol'],interval='daily'))
    return parse_crypto(av('DIGITAL_CURRENCY_DAILY',symbol=asset['symbol'],market='USD'))

def spot(asset):
    if asset['kind']=='metal':
        d=av('GOLD_SILVER_SPOT',symbol=asset['api_symbol'])
        for k in ('price','Price','spot_price','Spot Price','ask','bid'):
            if sf(d.get(k)) is not None:return sf(d[k])
        for v in d.values():
            if isinstance(v,dict):
                for k,x in v.items():
                    if ('price' in k.lower() or 'rate' in k.lower()) and sf(x) is not None:return sf(x)
        raise RuntimeError('Metal spot could not be parsed')
    d=av('CURRENCY_EXCHANGE_RATE',from_currency=asset['symbol'],to_currency='USD')
    x=d.get('Realtime Currency Exchange Rate',{}).get('5. Exchange Rate')
    if sf(x) is None: raise RuntimeError('Crypto spot could not be parsed')
    return sf(x)

def fred_series(sid):
    r=requests.get('https://fred.stlouisfed.org/graph/fredgraph.csv',params={'id':sid},timeout=35); r.raise_for_status()
    rows=[]
    for row in csv.DictReader(io.StringIO(r.text)):
        try: rows.append((row['DATE'],float(row[sid])))
        except: pass
    return rows

def macro():
    o={}
    try:
        x=fred_series('DTWEXBGS'); o['usd']=x[-1][1]; o['usd5']=pct(x[-1][1],x[-6][1])
    except Exception as e: print('FRED USD',e)
    try:
        x=fred_series('DGS10'); o['y10']=x[-1][1]; o['y105']=(x[-1][1]-x[-6][1])*100
    except Exception as e: print('FRED 10Y',e)
    try:
        x=fred_series('CPIAUCSL'); o['cpi']=pct(x[-1][1],x[-13][1])
    except Exception as e: print('FRED CPI',e)
    return o

def cftc(asset):
    if not asset['code']: return {'available':False,'reason':'No direct CFTC contract mapping'}
    url=CFTC_DISAGG if asset['cftc']=='disagg' else CFTC_LEGACY
    try:
        rows=get_json(url,{'$limit':10,'$where':f"cftc_contract_market_code='{asset['code']}'"})
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
    net=L-S; pnet=(pL-pS) if pL is not None and pS is not None else None
    return {'available':True,'cohort':cohort,'long':L,'short':S,'net':net,'net_change':(net-pnet if pnet is not None else None),
            'oi':oi,'oi_change':pct(oi,poi) if oi is not None and poi not in (None,0) else None,'date':dt(cur)[:10]}


def _metric_count(d):
    keys = (
        'oi_value', 'oi_change_pct', 'funding_rate',
        'futures_quote_volume', 'futures_24h_change',
        'long_short_ratio', 'taker_ratio'
    )
    return sum(1 for k in keys if d.get(k) is not None)

def binance_public(asset):
    """Free/public Binance futures data. May be unavailable from some cloud/IP regions."""
    if asset.get('kind') != 'crypto' or not asset.get('binance'):
        return {'available': False, 'source': 'Binance'}

    symbol = asset['binance']
    out = {'available': False, 'source': 'Binance', 'symbol': symbol}

    try:
        oi = get_json(
            BINANCE_FUTURES + '/futures/data/openInterestHist',
            {'symbol': symbol, 'period': '1h', 'limit': 2}
        )
        if isinstance(oi, list) and oi:
            cur = oi[-1]
            prev = oi[-2] if len(oi) > 1 else {}
            out['oi_value'] = sf(cur.get('sumOpenInterestValue'))
            out['oi_contracts'] = sf(cur.get('sumOpenInterest'))
            pv = sf(prev.get('sumOpenInterestValue'))
            if out.get('oi_value') is not None and pv not in (None, 0):
                out['oi_change_pct'] = pct(out['oi_value'], pv)
    except Exception as e:
        print('Binance OI error', symbol, e)

    try:
        taker = get_json(
            BINANCE_FUTURES + '/futures/data/takerlongshortRatio',
            {'symbol': symbol, 'period': '1h', 'limit': 1}
        )
        if isinstance(taker, list) and taker:
            row = taker[-1]
            out['taker_ratio'] = sf(row.get('buySellRatio'))
            out['taker_buy'] = sf(row.get('buyVol'))
            out['taker_sell'] = sf(row.get('sellVol'))
    except Exception as e:
        print('Binance taker error', symbol, e)

    try:
        ls = get_json(
            BINANCE_FUTURES + '/futures/data/globalLongShortAccountRatio',
            {'symbol': symbol, 'period': '1h', 'limit': 1}
        )
        if isinstance(ls, list) and ls:
            row = ls[-1]
            out['long_short_ratio'] = sf(row.get('longShortRatio'))
            out['long_account'] = sf(row.get('longAccount'))
            out['short_account'] = sf(row.get('shortAccount'))
    except Exception as e:
        print('Binance long/short error', symbol, e)

    try:
        funding = get_json(
            BINANCE_FUTURES + '/fapi/v1/fundingRate',
            {'symbol': symbol, 'limit': 1}
        )
        if isinstance(funding, list) and funding:
            out['funding_rate'] = sf(funding[-1].get('fundingRate'))
    except Exception as e:
        print('Binance funding error', symbol, e)

    try:
        ticker = get_json(
            BINANCE_FUTURES + '/fapi/v1/ticker/24hr',
            {'symbol': symbol}
        )
        if isinstance(ticker, dict):
            out['futures_24h_change'] = sf(ticker.get('priceChangePercent'))
            out['futures_quote_volume'] = sf(ticker.get('quoteVolume'))
    except Exception as e:
        print('Binance ticker error', symbol, e)

    out['available'] = _metric_count(out) > 0
    return out

def bybit_public(asset):
    """Free/public Bybit V5 market data. No key is sent."""
    if asset.get('kind') != 'crypto' or not asset.get('binance'):
        return {'available': False, 'source': 'Bybit'}

    symbol = asset['binance']
    out = {'available': False, 'source': 'Bybit', 'symbol': symbol}

    try:
        d = get_json(
            BYBIT_PUBLIC + '/v5/market/tickers',
            {'category': 'linear', 'symbol': symbol}
        )
        rows = ((d.get('result') or {}).get('list') or []) if isinstance(d, dict) else []
        if rows:
            r = rows[0]
            out['oi_value'] = sf(r.get('openInterestValue'))
            out['oi_contracts'] = sf(r.get('openInterest'))
            out['funding_rate'] = sf(r.get('fundingRate'))
            p24 = sf(r.get('price24hPcnt'))
            out['futures_24h_change'] = p24 * 100 if p24 is not None else None
            out['futures_quote_volume'] = sf(r.get('turnover24h'))
    except Exception as e:
        print('Bybit ticker error', symbol, e)

    try:
        d = get_json(
            BYBIT_PUBLIC + '/v5/market/open-interest',
            {'category': 'linear', 'symbol': symbol, 'intervalTime': '1h', 'limit': 2}
        )
        rows = ((d.get('result') or {}).get('list') or []) if isinstance(d, dict) else []
        if len(rows) >= 2:
            # Bybit can return newest first, so sort by timestamp.
            rows = sorted(rows, key=lambda x: int(x.get('timestamp', 0)))
            a = sf(rows[-1].get('openInterest'))
            b = sf(rows[-2].get('openInterest'))
            if a is not None and b not in (None, 0):
                out['oi_change_pct'] = pct(a, b)
    except Exception as e:
        print('Bybit OI history error', symbol, e)

    try:
        d = get_json(
            BYBIT_PUBLIC + '/v5/market/account-ratio',
            {'category': 'linear', 'symbol': symbol, 'period': '1h', 'limit': 1}
        )
        rows = ((d.get('result') or {}).get('list') or []) if isinstance(d, dict) else []
        if rows:
            r = rows[0]
            la = sf(r.get('buyRatio'))
            sa = sf(r.get('sellRatio'))
            out['long_account'] = la
            out['short_account'] = sa
            if la is not None and sa not in (None, 0):
                out['long_short_ratio'] = la / sa
    except Exception as e:
        print('Bybit long/short error', symbol, e)

    out['available'] = _metric_count(out) > 0
    return out

def okx_public(asset):
    """Free/public OKX market data. No key is sent."""
    if asset.get('kind') != 'crypto':
        return {'available': False, 'source': 'OKX'}

    inst = f"{asset['symbol']}-USDT-SWAP"
    out = {'available': False, 'source': 'OKX', 'symbol': inst}

    try:
        d = get_json(
            OKX_PUBLIC + '/api/v5/public/open-interest',
            {'instType': 'SWAP', 'instId': inst}
        )
        rows = d.get('data', []) if isinstance(d, dict) else []
        if rows:
            r = rows[0]
            out['oi_value'] = sf(r.get('oiUsd')) or sf(r.get('oiCcy')) or sf(r.get('oi'))
            out['oi_contracts'] = sf(r.get('oi'))
    except Exception as e:
        print('OKX OI error', inst, e)

    try:
        d = get_json(
            OKX_PUBLIC + '/api/v5/public/funding-rate',
            {'instId': inst}
        )
        rows = d.get('data', []) if isinstance(d, dict) else []
        if rows:
            out['funding_rate'] = sf(rows[0].get('fundingRate'))
    except Exception as e:
        print('OKX funding error', inst, e)

    try:
        d = get_json(
            OKX_PUBLIC + '/api/v5/market/ticker',
            {'instId': inst}
        )
        rows = d.get('data', []) if isinstance(d, dict) else []
        if rows:
            r = rows[0]
            last = sf(r.get('last'))
            op = sf(r.get('open24h'))
            if last is not None and op not in (None, 0):
                out['futures_24h_change'] = pct(last, op)
            # OKX returns 24h volume fields; volCcy24h is the more useful
            # quote/base-currency volume depending on instrument rules.
            out['futures_quote_volume'] = sf(r.get('volCcy24h')) or sf(r.get('vol24h'))
    except Exception as e:
        print('OKX ticker error', inst, e)

    out['available'] = _metric_count(out) > 0
    return out

def free_derivatives(asset, prev_state):
    """
    Try several FREE public derivatives feeds.
    Choose the first sufficiently complete feed; otherwise keep the best partial feed.
    No exchange API key is used.
    """
    candidates = []

    for provider in (binance_public, bybit_public, okx_public):
        d = provider(asset)
        candidates.append(d)
        if _metric_count(d) >= 3:
            best = d
            break
    else:
        best = max(candidates, key=_metric_count) if candidates else {
            'available': False, 'source': 'None'
        }

    # If a provider gives current OI but not a 1h historical OI change,
    # calculate change versus the previous TradingLife report — only when
    # the provider is the same as the previous report.
    if (
        best.get('oi_change_pct') is None
        and best.get('oi_value') is not None
        and prev_state.get('deriv_source') == best.get('source')
    ):
        prev_oi = sf(prev_state.get('deriv_oi_value'))
        if prev_oi not in (None, 0):
            best['oi_change_pct'] = pct(best['oi_value'], prev_oi)
            best['oi_change_period'] = 'since prior report'

    if best.get('oi_change_period') is None:
        best['oi_change_period'] = '1H'

    return best

def derivatives_pressure(bd, t):
    """
    0-100 FREE derivatives pressure score.
    This is not a measurement of total capital entering/leaving the entire asset.
    """
    if not bd.get('available'):
        return None

    s = 50.0
    oi = bd.get('oi_change_pct')

    if oi is not None:
        if oi > 0.15 and t['daily'] > 0:
            s += min(16, 7 + abs(oi) * 3)
        elif oi > 0.15 and t['daily'] < 0:
            s -= min(16, 7 + abs(oi) * 3)
        elif oi < -0.15 and t['daily'] > 0:
            s += 3
        elif oi < -0.15 and t['daily'] < 0:
            s -= 3

    tr = bd.get('taker_ratio')
    if tr is not None:
        s += max(-18, min(18, (tr - 1.0) * 35))

    fr = bd.get('funding_rate')
    if fr is not None:
        s += max(-7, min(7, fr * 10000 * 1.5))

    ls = bd.get('long_short_ratio')
    if ls is not None:
        s += max(-6, min(6, (ls - 1.0) * 8))

    # If the free source has no taker or long/short ratio, use the exchange's
    # futures 24h change as a small supporting input rather than leaving the
    # pressure score effectively empty.
    if tr is None and ls is None:
        pc = bd.get('futures_24h_change')
        if pc is not None:
            s += max(-8, min(8, pc * 0.8))

    return clamp(s)

def derivatives_label(s):
    if s is None: return 'NO BINANCE DERIVATIVES DATA'
    if s >= 75: return 'STRONG UPSIDE FLOW PRESSURE'
    if s >= 60: return 'UPSIDE FLOW PRESSURE'
    if s >= 40: return 'BALANCED / MIXED'
    if s >= 25: return 'DOWNSIDE FLOW PRESSURE'
    return 'STRONG DOWNSIDE FLOW PRESSURE'

def funding_label(fr):
    if fr is None: return 'N/A'
    pctv = fr * 100
    if pctv >= 0.05: return 'Long crowded'
    if pctv <= -0.05: return 'Short crowded'
    if pctv > 0: return 'Positive'
    if pctv < 0: return 'Negative'
    return 'Neutral'

def ema(v,n):
    if len(v)<n:return None
    k=2/(n+1); x=sum(v[:n])/n
    for z in v[n:]:x=z*k+x*(1-k)
    return x

def rsi(v,n=14):
    if len(v)<n+1:return None
    ch=[v[i]-v[i-1] for i in range(1,len(v))]; g=[max(x,0) for x in ch]; l=[max(-x,0) for x in ch]
    ag=sum(g[:n])/n; al=sum(l[:n])/n
    for i in range(n,len(g)):ag=(ag*(n-1)+g[i])/n; al=(al*(n-1)+l[i])/n
    return 100 if al==0 else 100-100/(1+ag/al)

def macd(v):
    if len(v)<35:return None,None
    line=[]
    for i in range(26,len(v)+1):line.append(ema(v[:i],12)-ema(v[:i],26))
    return line[-1],ema(line,9)

def tech(hist,sp):
    rows=[dict(x) for x in hist[-260:]]; rows[-1]['close']=sp
    c=[x['close'] for x in rows]; e20=ema(c,20); e50=ema(c,50); e200=ema(c,200) if len(c)>=200 else None
    rr=rsi(c); m,ms=macd(c); r7=pct(c[-1],c[-8]) if len(c)>=8 else 0; r30=pct(c[-1],c[-31]) if len(c)>=31 else 0
    s=50; s+=8 if c[-1]>e20 else -8; s+=8 if e20>e50 else -8
    if e200 is not None:s+=8 if c[-1]>e200 else -8
    if rr is not None:
        s+=7 if 55<=rr<=75 else (-7 if 25<=rr<=45 else (3 if rr>75 else (-3 if rr<25 else 0)))
    if m is not None and ms is not None:s+=7 if m>ms else -7
    s+=max(-7,min(7,r7*1.4)); s=clamp(s)
    vols=[x['volume'] for x in rows[-20:] if x.get('volume') is not None]; lv=rows[-1].get('volume'); avgv=statistics.mean(vols) if vols else None
    return {'score':s,'price':c[-1],'daily':pct(c[-1],c[-2]),'ema20':e20,'ema50':e50,'ema200':e200,'rsi':rr,'macd':m,'macd_signal':ms,
            'r7':r7,'r30':r30,'support':min(c[-20:]),'resistance':max(c[-20:]),'volume':lv,'volume_ratio':(lv/avgv if lv is not None and avgv else None),
            'market_cap':rows[-1].get('market_cap'),'closes':c[-60:],'date':rows[-1]['date']}

def macro_score(m):
    s=50; u=m.get('usd5'); y=m.get('y105')
    if u is not None:s+=max(-15,min(15,-u*8))
    if y is not None:s+=max(-15,min(15,-y*.7))
    return clamp(s)

def inst_score(ci,t):
    if not ci.get('available'):return None
    s=50; oi=ci.get('oi'); net=ci.get('net'); ch=ci.get('net_change'); oich=ci.get('oi_change')
    if oi not in (None,0) and net is not None:s+=max(-20,min(20,(net/oi)*65))
    if oi not in (None,0) and ch is not None:s+=max(-18,min(18,(ch/oi)*350))
    if oich is not None:
        if oich>.25 and t['daily']>0:s+=10
        elif oich>.25 and t['daily']<0:s-=10
        elif oich<-.25 and t['daily']>0:s+=3
        elif oich<-.25 and t['daily']<0:s-=3
    return clamp(s)

def flow_proxy(t):
    s=50+max(-18,min(18,t['r7']*2))+max(-12,min(12,t['r30']*.55)); vr=t.get('volume_ratio')
    if vr is not None and vr>=1.3:s+=12 if t['daily']>0 else -12
    return clamp(s)

def strength_label(s):
    return 'VERY STRONG' if s>=80 else 'STRONG' if s>=65 else 'MODERATELY STRONG' if s>=55 else 'NEUTRAL' if s>=45 else 'MODERATELY WEAK' if s>=35 else 'WEAK' if s>=20 else 'VERY WEAK'

def flow_label(s):
    if s is None:return 'NO DIRECT INSTITUTIONAL DATA'
    return 'STRONG INFLOW PRESSURE' if s>=75 else 'INFLOW PRESSURE' if s>=60 else 'NEUTRAL / MIXED' if s>=40 else 'OUTFLOW PRESSURE' if s>=25 else 'STRONG OUTFLOW PRESSURE'

def intelligence(asset,h,sp,m,prev):
    t = tech(h, sp)
    ci = cftc(asset)
    ins = inst_score(ci, t)
    proxy = flow_proxy(t)
    ms = macro_score(m) if asset['kind'] == 'metal' else None

    bd = free_derivatives(asset, prev) if asset['kind'] == 'crypto' else {'available': False, 'source': 'None'}
    dscore = derivatives_pressure(bd, t) if asset['kind'] == 'crypto' else None

    if asset['kind'] == 'metal':
        strength = (
            .50*t['score'] + .30*ins + .20*ms
            if ins is not None else
            .65*t['score'] + .35*ms
        )
        primary_flow = ins
        primary_flow_label = flow_label(ins)
        flow_kind = 'institutional'
    else:
        if ins is not None and dscore is not None:
            # BTC/ETH: official CFTC large-trader positioning + Binance derivatives.
            strength = .45*t['score'] + .25*ins + .30*dscore
            primary_flow = .45*ins + .55*dscore
            primary_flow_label = derivatives_label(primary_flow)
            flow_kind = 'institutional+derivatives'
        elif dscore is not None:
            # Altcoins: do NOT call this institutional flow.
            strength = .55*t['score'] + .45*dscore
            primary_flow = dscore
            primary_flow_label = derivatives_label(dscore)
            flow_kind = 'derivatives'
        else:
            strength = .65*t['score'] + .35*proxy
            primary_flow = proxy
            primary_flow_label = 'MARKET FLOW PROXY'
            flow_kind = 'market_proxy'

    strength = clamp(strength)
    ps = sf(prev.get('strength'))
    pf = sf(prev.get('flow_score'))
    ds = (strength - ps) if ps is not None else None
    df = (primary_flow - pf) if primary_flow is not None and pf is not None else None

    alert = 'NO MAJOR ALERT'
    detail = 'No major threshold change detected.'

    # Crypto flow-pressure alerts from FREE derivatives data.
    if asset['kind'] == 'crypto' and dscore is not None:
        oi = bd.get('oi_change_pct')
        tr = bd.get('taker_ratio')

        if dscore >= 72 and (oi is None or oi > 0) and (tr is None or tr >= 1.03):
            alert = 'DERIVATIVES INFLOW PRESSURE'
            detail = f"{bd.get('source','Public')} derivatives data show stronger upside flow pressure."
        elif dscore <= 28 and (oi is None or oi > 0) and (tr is None or tr <= 0.97):
            alert = 'DERIVATIVES OUTFLOW PRESSURE'
            detail = f"{bd.get('source','Public')} derivatives data show stronger downside flow pressure."
        elif abs((bd.get('funding_rate') or 0) * 100) >= 0.05:
            alert = 'FUNDING CROWDING ALERT'
            detail = f"Funding is {funding_label(bd.get('funding_rate')).lower()}, which can indicate crowded positioning."
        elif ds is not None and ds >= 8:
            alert = 'MARKET STRENGTH IMPROVING'
            detail = f'Strength rose {ds:.1f} points since the prior report.'
        elif ds is not None and ds <= -8:
            alert = 'MARKET STRENGTH WEAKENING'
            detail = f'Strength fell {abs(ds):.1f} points since the prior report.'
        elif strength >= 68 and t['daily'] > 0 and (ds is None or ds >= 4):
            alert = 'UPWARD PRESSURE INCREASING'
            detail = 'Market strength, price momentum, and derivatives pressure are strengthening together.'
        elif strength <= 32 and t['daily'] < 0 and (ds is None or ds <= -4):
            alert = 'DOWNSIDE PRESSURE INCREASING'
            detail = 'Market strength and derivatives pressure are weakening together.'

    else:
        # Metals: existing CFTC / macro logic.
        if ins is not None and ins >= 72 and (df is None or df >= 5):
            alert = 'INSTITUTIONAL INFLOW ALERT'
            detail = 'Large-trader positioning and participation show stronger inflow pressure.'
        elif ins is not None and ins <= 28 and (df is None or df <= -5):
            alert = 'CAPITAL OUTFLOW ALERT'
            detail = 'Large-trader positioning and participation show stronger outflow pressure.'
        elif strength >= 68 and t['daily'] > 0 and (ds is None or ds >= 4):
            alert = 'UPWARD PRESSURE INCREASING'
            detail = 'Market strength and price momentum are strengthening together.'
        elif strength <= 32 and t['daily'] < 0 and (ds is None or ds <= -4):
            alert = 'DOWNSIDE PRESSURE INCREASING'
            detail = 'Market strength is weakening while downside pressure increases.'
        elif ds is not None and ds >= 8:
            alert = 'MARKET STRENGTH IMPROVING'
            detail = f'Strength rose {ds:.1f} points since the prior report.'
        elif ds is not None and ds <= -8:
            alert = 'MARKET STRENGTH WEAKENING'
            detail = f'Strength fell {abs(ds):.1f} points since the prior report.'

    return {
        **t,
        'symbol': asset['symbol'],
        'name': asset['name'],
        'kind': asset['kind'],
        'market_strength': strength,
        'strength_label': strength_label(strength),
        'strength_delta': ds,
        'macro': m,
        'macro_score': ms,
        'institutional': ci,
        'flow_score': primary_flow,
        'flow_label': primary_flow_label,
        'flow_kind': flow_kind,
        'proxy': proxy,
        'derivatives': bd,
        'derivatives_score': dscore,
        'derivatives_label': derivatives_label(dscore),
        'alert': alert,
        'alert_detail': detail
    }

def compact(v):
    if v is None:return 'N/A'
    a=abs(v)
    if a>=1e9:return f'{v/1e9:.2f}B'
    if a>=1e6:return f'{v/1e6:.2f}M'
    if a>=1e3:return f'{v/1e3:.1f}K'
    return f'{v:,.0f}'

def money(v):
    if v is None:return 'N/A'
    a=abs(v)
    if a>=1e12:return f'${v/1e12:.2f}T'
    if a>=1e9:return f'${v/1e9:.2f}B'
    if a>=1e6:return f'${v/1e6:.2f}M'
    return f'${v:,.2f}'

def price(v):
    if v is None:return 'N/A'
    return f'${v:,.2f}' if v>=1000 else f'${v:,.3f}' if v>=10 else f'${v:,.4f}' if v>=1 else f'${v:,.6f}'

W,H=1080,1680; BG=(11,14,20); PANEL=(24,29,39); PANEL2=(18,22,31); TEXT=(240,244,248); MUTED=(155,165,178); GREEN=(44,200,134); RED=(239,83,80); AMBER=(245,185,66); GOLD=(212,175,55); SILVER=(195,202,212); BLUE=(91,155,255)
def font(n,b=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(p,n) if os.path.exists(p) else ImageFont.load_default()
F16=font(16);F18=font(18);F21=font(21);F24=font(24);F26=font(26,1);F30=font(30,1);F38=font(38,1);F46=font(46,1);F64=font(64,1)
def rounded(d,xy,fill=PANEL,outline=None,w=1):d.rounded_rectangle(xy,radius=22,fill=fill,outline=outline,width=w)
def col(s):return GREEN if s>=60 else RED if s<=40 else AMBER
def metric(d,x,y,width,label,value,color=TEXT):
    d.text((x,y),label,font=F21,fill=MUTED); bb=d.textbbox((0,0),value,font=F21); d.text((x+width-(bb[2]-bb[0]),y),value,font=F21,fill=color)
def spark(d,box,v,accent):
    if len(v)<2:return
    x1,y1,x2,y2=box; mn,mx=min(v),max(v); span=mx-mn or 1; pts=[]
    for i,z in enumerate(v):pts.append((x1+(x2-x1)*i/(len(v)-1),y2-(y2-y1)*(z-mn)/span))
    for i in range(1,4):yy=y1+(y2-y1)*i/4;d.line((x1,yy,x2,yy),fill=(43,49,61),width=1)
    d.line(pts,fill=accent,width=5,joint='curve')

def render(a):
    accent=GOLD if a['symbol']=='XAU' else SILVER if a['symbol']=='XAG' else BLUE
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)

    d.text((55,42),'TRADINGLIFE',font=F38,fill=TEXT)
    d.text((55,91),'Market Intelligence',font=F21,fill=MUTED)
    d.text((1025,52),f"{a['name'].upper()} | {a['symbol']}/USD",font=F30,fill=accent,anchor='ra')

    rounded(d,(50,140,1030,375))
    d.text((82,172),price(a['price']),font=F64,fill=TEXT)
    d.text((85,260),f"{a['daily']:+.2f}% vs prior daily close",font=F24,fill=GREEN if a['daily']>=0 else RED)
    d.text((690,175),'MARKET STRENGTH',font=F18,fill=MUTED)
    d.text((690,212),f"{a['market_strength']:.0f} / 100",font=F46,fill=col(a['market_strength']))
    d.text((690,275),a['strength_label'],font=F24,fill=col(a['market_strength']))
    if a['strength_delta'] is not None:
        d.text((690,315),f"Change: {a['strength_delta']:+.1f} pts",font=F21,fill=GREEN if a['strength_delta']>=0 else RED)

    rounded(d,(50,400,1030,625),fill=PANEL2)
    d.text((78,425),'60-DAY PRICE TREND',font=F18,fill=MUTED)
    spark(d,(82,475,995,595),a['closes'],accent)

    # Left: technical condition
    rounded(d,(50,650,520,1015))
    d.text((78,678),'MARKET CONDITION',font=F26,fill=TEXT)
    metric(d,78,735,405,'RSI (14)',f"{a['rsi']:.1f}" if a['rsi'] is not None else 'N/A')
    metric(d,78,780,405,'EMA 20',price(a['ema20']))
    metric(d,78,825,405,'EMA 50',price(a['ema50']))
    metric(d,78,870,405,'EMA 200',price(a['ema200']))
    md='Positive' if a['macd'] is not None and a['macd_signal'] is not None and a['macd']>a['macd_signal'] else 'Negative'
    metric(d,78,915,405,'Momentum (MACD)',md,GREEN if md=='Positive' else RED)
    metric(d,78,960,405,'7D / 30D',f"{a['r7']:+.1f}% / {a['r30']:+.1f}%")

    # Right: institutional / derivatives
    rounded(d,(550,650,1030,1015))
    ci=a['institutional']

    if a['kind']=='metal':
        d.text((578,678),'INSTITUTIONAL POSITIONING',font=F26,fill=TEXT)
        if ci.get('available'):
            metric(d,578,735,410,ci['cohort']+' Net',compact(ci['net']),GREEN if ci['net']>=0 else RED)
            metric(d,578,780,410,'Weekly Net Change',compact(ci['net_change']),GREEN if (ci['net_change'] or 0)>=0 else RED)
            metric(d,578,825,410,'Open Interest',compact(ci['oi']))
            metric(d,578,870,410,'OI Weekly Change',f"{ci['oi_change']:+.2f}%" if ci['oi_change'] is not None else 'N/A',GREEN if (ci['oi_change'] or 0)>=0 else RED)
            metric(d,578,915,410,'Institutional Flow',f"{a['flow_score']:.0f} / 100",col(a['flow_score']))
            d.text((578,962),a['flow_label'],font=F18,fill=col(a['flow_score']))
            d.text((578,987),f"CFTC report: {ci.get('date','')}",font=F18,fill=MUTED)
        else:
            d.text((578,755),'No direct CFTC institutional data.',font=F21,fill=AMBER)

    else:
        bd=a.get('derivatives',{})
        src=bd.get('source','Public')
        title = f"INSTITUTIONAL + {src.upper()}" if ci.get('available') else f"DERIVATIVES FLOW — {src.upper()}"
        d.text((578,678),title,font=F24,fill=TEXT)

        rows=[]
        if ci.get('available'):
            rows.append(('CFTC Large Traders Net', compact(ci['net']), GREEN if ci['net']>=0 else RED))

        if bd.get('oi_value') is not None:
            rows.append(('Open Interest', money(bd.get('oi_value')), TEXT))
        if bd.get('oi_change_pct') is not None:
            period=bd.get('oi_change_period','1H')
            v=bd['oi_change_pct']
            rows.append((f'OI Change ({period})', f"{v:+.2f}%", GREEN if v>=0 else RED))
        if bd.get('funding_rate') is not None:
            fr=bd['funding_rate']
            rows.append(('Funding Rate', f"{fr*100:+.4f}%", GREEN if fr<=0.0005 else AMBER))
        if bd.get('long_short_ratio') is not None:
            ls=bd['long_short_ratio']
            rows.append(('Long / Short', f"{ls:.2f}x", GREEN if ls>=1 else RED))
        if bd.get('taker_ratio') is not None:
            tr=bd['taker_ratio']
            rows.append(('Taker Buy / Sell', f"{tr:.2f}x", GREEN if tr>=1 else RED))
        if bd.get('futures_24h_change') is not None:
            pc=bd['futures_24h_change']
            rows.append(('Futures 24H', f"{pc:+.2f}%", GREEN if pc>=0 else RED))

        ds=a.get('derivatives_score')
        if ds is not None:
            rows.append(('Derivatives Pressure', f"{ds:.0f} / 100", col(ds)))

        y=735
        for label,value,color in rows[:6]:
            metric(d,578,y,410,label,value,color)
            y+=43

        if not rows:
            d.text((578,755),'Public derivatives feed unavailable.',font=F21,fill=AMBER)
            d.text((578,795),'Market Strength uses price/history only.',font=F18,fill=MUTED)
        else:
            d.text((578,985),f"Free source: {src}",font=F18,fill=MUTED)

    # Bottom data panel
    rounded(d,(50,1040,1030,1285),fill=PANEL2)
    if a['kind']=='metal':
        d.text((78,1068),'MACRO CONTEXT',font=F26,fill=TEXT)
        m=a['macro']
        d.text((78,1120),f"USD Index (5D): {m.get('usd5'):+.2f}%" if m.get('usd5') is not None else 'USD Index (5D): N/A',font=F21,fill=MUTED)
        d.text((390,1120),f"US 10Y (5D): {m.get('y105'):+.0f} bps" if m.get('y105') is not None else 'US 10Y (5D): N/A',font=F21,fill=MUTED)
        d.text((730,1120),f"CPI YoY: {m.get('cpi'):.2f}%" if m.get('cpi') is not None else 'CPI YoY: N/A',font=F21,fill=MUTED)
        d.text((78,1175),f"Macro Support Score: {a['macro_score']:.0f} / 100",font=F24,fill=col(a['macro_score']))
        d.text((78,1225),'Lower USD/yields are treated as more supportive for precious metals.',font=F18,fill=MUTED)
    else:
        bd=a.get('derivatives',{})
        src=bd.get('source','Public')
        d.text((78,1068),'FREE CRYPTO DERIVATIVES DATA',font=F26,fill=TEXT)
        d.text((78,1115),f"Active source: {src}",font=F21,fill=BLUE)

        qv=bd.get('futures_quote_volume')
        if qv is not None:
            d.text((78,1160),f"24H Turnover / Volume: {money(qv)}",font=F21,fill=MUTED)

        pc=bd.get('futures_24h_change')
        if pc is not None:
            d.text((500,1160),f"Futures 24H: {pc:+.2f}%",font=F21,fill=GREEN if pc>=0 else RED)

        la=bd.get('long_account'); sa=bd.get('short_account')
        if la is not None and sa is not None:
            # Some APIs return 0-1 ratios; display as percentages.
            d.text((78,1205),f"Accounts: Long {la*100:.1f}% | Short {sa*100:.1f}%",font=F21,fill=MUTED)
        elif bd.get('funding_rate') is not None:
            d.text((78,1205),f"Funding: {bd['funding_rate']*100:+.4f}%",font=F21,fill=MUTED)

        d.text((78,1248),'Exchange derivatives pressure ≠ total-market capital flow or verified institutional flow.',
               font=F16,fill=MUTED)

    # Alert
    rounded(d,(50,1310,1030,1475),fill=(27,31,42),outline=col(a['market_strength']),w=2)
    d.text((78,1335),'ALERT',font=F21,fill=MUTED)
    ac=RED if any(x in a['alert'] for x in ('OUTFLOW','DOWNSIDE','WEAKENING')) else GREEN if any(x in a['alert'] for x in ('INFLOW','UPWARD','IMPROVING')) else AMBER
    d.text((78,1372),a['alert'],font=F30,fill=ac)
    # Keep alert detail safely inside the box.
    detail=a['alert_detail']
    if len(detail)>86:
        split=detail.rfind(' ',0,86)
        if split<40: split=86
        d.text((78,1420),detail[:split],font=F18,fill=TEXT)
        d.text((78,1445),detail[split+1:],font=F18,fill=TEXT)
    else:
        d.text((78,1420),detail,font=F18,fill=TEXT)

    # Dedicated footer area — no caption overlay from Telegram.
    rounded(d,(50,1500,1030,1635),fill=PANEL2)
    ir=datetime.now(ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M')
    d.text((75,1525),f'TradingLife  •  Updated {ir} Iran',font=F18,fill=TEXT)
    src='Alpha Vantage • CFTC COT • FRED'
    if a['kind']=='crypto':
        src += f" • {a.get('derivatives',{}).get('source','Public')} public derivatives data"
    d.text((75,1567),f"Sources: {src}",font=F16,fill=MUTED)
    d.text((75,1598),'Market intelligence only • Not investment advice',font=F16,fill=MUTED)

    os.makedirs(OUT,exist_ok=True)
    p=os.path.join(OUT,f"{a['symbol'].lower()}_tradinglife.png")
    im.save(p,'PNG',optimize=True)
    return p

def send(p,a):
    # Everything the user needs is already inside the image.
    # Sending without a Telegram caption prevents the app's media-viewer
    # caption overlay from covering the card footer.
    with open(p,'rb') as f:
        r=requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',
            data={'chat_id':CHAT_ID},
            files={'photo':f},
            timeout=60
        )
        r.raise_for_status()

def load(path,default):
    try:
        with open(path,'r',encoding='utf-8') as f:return json.load(f)
    except:return default

def save(path,x):
    with open(path,'w',encoding='utf-8') as f:json.dump(x,f,ensure_ascii=False,indent=2)

def main():
    global RUN_MODE
    selected=ASSETS if ASSET_FILTER=='ALL' else [x for x in ASSETS if x['symbol']==ASSET_FILTER]
    if not selected:raise RuntimeError('Unknown ASSET_FILTER='+ASSET_FILTER)
    state=load(STATE,{}); hs={}; m={}
    if RUN_MODE=='refresh':
        c=load(CACHE,{});hs=c.get('histories',{});m=c.get('macro',{})
        if any(a['symbol'] not in hs for a in selected):RUN_MODE='full'
    if RUN_MODE=='full':
        m=macro()
        for a in selected:print('History',a['symbol']);hs[a['symbol']]=history(a)
        if ASSET_FILTER=='ALL':save(CACHE,{'saved_at':datetime.utcnow().isoformat()+'Z','histories':hs,'macro':m})
    results=[];fail=[]
    for a in selected:
        try:
            print('Spot',a['symbol']);sp=spot(a);results.append(intelligence(a,hs[a['symbol']],sp,m,state.get(a['symbol'],{})))
        except Exception as e:print('ERROR',a['symbol'],e);fail.append(f"{a['symbol']}: {e}")
    if not results:raise RuntimeError('No cards built. '+' | '.join(fail))
    for a in results:
        p=render(a);send(p,a);state[a['symbol']]={
            'strength':round(a['market_strength'],3),
            'flow_score':round(a['flow_score'],3) if a['flow_score'] is not None else None,
            'alert':a['alert'],
            'price':a['price'],
            'deriv_source':a.get('derivatives',{}).get('source'),
            'deriv_oi_value':a.get('derivatives',{}).get('oi_value'),
            'updated':datetime.utcnow().isoformat()+'Z'
        };time.sleep(.8)
    save(STATE,state)
    if fail:requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data={'chat_id':CHAT_ID,'text':'TradingLife warnings:\n'+'\n'.join(fail)},timeout=30)
    print('Done',RUN_MODE,ASSET_FILTER,len(results))
if __name__=='__main__':main()
