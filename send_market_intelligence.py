import os, io, re, csv, json, time, statistics
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

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
CFTC_MAX_AGE_DAYS = 21  # COT is weekly; anything older than ~3 weeks is treated as stale

def get_json(url, params=None, timeout=40):
    r=requests.get(url,params=params,timeout=timeout,headers=UA); r.raise_for_status(); return r.json()

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

def okx_history(asset, days=300):
    """Daily UTC candles from OKX spot (free, no key). Newest-first pages of 100."""
    inst=f"{asset['symbol']}-USDT"; rows={}; after=None
    for _ in range(4):
        params={'instId':inst,'bar':'1Dutc','limit':100}
        if after: params['after']=after
        d=get_json(OKX_PUBLIC+'/api/v5/market/history-candles',params,timeout=25)
        data=d.get('data',[]) if isinstance(d,dict) else []
        if not data: break
        for c in data:
            ts=int(c[0]); day=datetime.utcfromtimestamp(ts/1000).strftime('%Y-%m-%d')
            rows[day]={'date':day,'open':sf(c[1]),'high':sf(c[2]),'low':sf(c[3]),'close':sf(c[4]),
                       'volume':sf(c[7]) if len(c)>7 else sf(c[5]),'market_cap':None}
        after=data[-1][0]
        if len(rows)>=days: break
        time.sleep(0.3)
    out=sorted((r for r in rows.values() if r['close'] is not None),key=lambda x:x['date'])
    if len(out)<55: raise RuntimeError(f'Not enough OKX history for {inst} ({len(out)} rows)')
    return out

def okx_spot(asset):
    d=get_json(OKX_PUBLIC+'/api/v5/market/ticker',{'instId':f"{asset['symbol']}-USDT"},timeout=20)
    rows=d.get('data',[]) if isinstance(d,dict) else []
    v=sf(rows[0].get('last')) if rows else None
    if v is None: raise RuntimeError('OKX spot could not be parsed')
    return v

def history(asset):
    if asset['kind']=='metal': return parse_metal(av('GOLD_SILVER_HISTORY',symbol=asset['api_symbol'],interval='daily'))
    try:
        return okx_history(asset)
    except Exception as e:
        print('OKX history failed, falling back to Alpha Vantage',asset['symbol'],e)
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
    try:
        return okx_spot(asset)
    except Exception as e:
        print('OKX spot failed, falling back to Alpha Vantage',asset['symbol'],e)
    d=av('CURRENCY_EXCHANGE_RATE',from_currency=asset['symbol'],to_currency='USD')
    x=d.get('Realtime Currency Exchange Rate',{}).get('5. Exchange Rate')
    if sf(x) is None: raise RuntimeError('Crypto spot could not be parsed')
    return sf(x)

# ---------------- MACRO (keyless sources; FRED times out from GitHub runners) ----------------
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

def treasury_10y_series():
    """US Treasury official daily par yield curve CSV (free, no key). Returns oldest->newest."""
    rows=[]
    yr=datetime.utcnow().year
    for y in (yr, yr-1):
        url=('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
             f'daily-treasury-rates.csv/{y}/all')
        r=requests.get(url,params={'type':'daily_treasury_yield_curve','field_tdr_date_value':str(y),'_format':'csv'},
                       headers=UA,timeout=25)
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(r.text.lstrip('\ufeff'))):
            v=sf(row.get('10 Yr')); dd=row.get('Date','')
            if v is None or not dd: continue
            try: iso=datetime.strptime(dd,'%m/%d/%Y').strftime('%Y-%m-%d')
            except Exception: continue
            rows.append((iso,v))
        if len(rows)>=6: break
    rows=sorted(set(rows))
    if len(rows)<6: raise RuntimeError(f'Treasury 10Y: only {len(rows)} rows')
    return rows

def cpi_yoy():
    """BLS public API v1 (free, no key). CPI-U all items, not seasonally adjusted = headline YoY."""
    d=get_json('https://api.bls.gov/publicAPI/v1/timeseries/data/CUUR0000SA0',timeout=25)
    if d.get('status')!='REQUEST_SUCCEEDED': raise RuntimeError(f"BLS: {d.get('status')} {d.get('message')}")
    data=d['Results']['series'][0]['data']
    m=[x for x in data if str(x.get('period','')).startswith('M') and x['period']!='M13' and sf(x.get('value')) is not None]
    m.sort(key=lambda x:(int(x['year']),int(x['period'][1:])),reverse=True)
    if len(m)<13: raise RuntimeError(f'BLS CPI: only {len(m)} months')
    return pct(sf(m[0]['value']),sf(m[12]['value'])), f"{m[0]['year']}-{m[0]['period'][1:]}"

def macro():
    o={}
    try:
        x=usd_index_series(); o['usd']=x[-1][1]; o['usd5']=pct(x[-1][1],x[-6][1])
    except Exception as e: print('MACRO USD',e)
    try:
        x=treasury_10y_series(); o['y10']=x[-1][1]; o['y105']=(x[-1][1]-x[-6][1])*100
    except Exception as e: print('MACRO 10Y',e)
    try:
        o['cpi'],o['cpi_month']=cpi_yoy()
    except Exception as e: print('MACRO CPI',e)
    print('MACRO result',o)
    return o

def cftc(asset):
    if not asset['code']: return {'available':False,'reason':'No direct CFTC contract mapping'}
    url=CFTC_DISAGG if asset['cftc']=='disagg' else CFTC_LEGACY
    try:
        rows=get_json(url,{'$limit':10,'$order':'report_date_as_yyyy_mm_dd DESC',
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
            out['oi_value'] = sf(r.get('oiUsd'))
            out['oi_ccy'] = sf(r.get('oiCcy'))  # coin units; converted to USD below if oiUsd missing
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
            # For OKX USDT swaps, volCcy24h is in COIN units (e.g. BTC), not USD.
            # Convert to USD turnover by multiplying by the last price.
            vccy = sf(r.get('volCcy24h'))
            if vccy is not None and last is not None:
                out['futures_quote_volume'] = vccy * last
            if out.get('oi_value') is None and out.get('oi_ccy') is not None and last is not None:
                out['oi_value'] = out['oi_ccy'] * last
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
    if s is None: return 'NO DERIVATIVES DATA'
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

W,H=1080,1750; BG=(11,14,20); PANEL=(24,29,39); PANEL2=(18,22,31); TEXT=(240,244,248); MUTED=(155,165,178); GREEN=(44,200,134); RED=(239,83,80); AMBER=(245,185,66); GOLD=(212,175,55); SILVER=(195,202,212); BLUE=(91,155,255)
def font(n,b=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(p,n) if os.path.exists(p) else ImageFont.load_default()
F16=font(16);F18=font(18);F21=font(21);F24=font(24);F26=font(26,1);F30=font(30,1);F38=font(38,1);F46=font(46,1);F64=font(64,1)
def rounded(d,xy,fill=PANEL,outline=None,w=1):d.rounded_rectangle(xy,radius=22,fill=fill,outline=outline,width=w)
def col(s):return GREEN if s>=60 else RED if s<=40 else AMBER
def metric(d,x,y,width,label,value,color=TEXT):
    d.text((x,y),label,font=F21,fill=MUTED); bb=d.textbbox((0,0),value,font=F21); d.text((x+width-(bb[2]-bb[0]),y),value,font=F21,fill=color)

# ---------------------------------------------------------------- نسخه‌ی فارسی کارت (آینه‌ای + ترجمه)
from tl_fa import IS_FA, MirrorDraw, Translator, hashtags, wrap as wrap_fa, fa_date

NAME_FA = {'GOLD': 'طلا', 'SILVER': 'نقره', 'BITCOIN': 'بیت‌کوین', 'ETHEREUM': 'اتریوم', 'SOLANA': 'سولانا',
           'XRP': 'ریپل (XRP)', 'CARDANO': 'کاردانو', 'DOGECOIN': 'دوج‌کوین'}
_EX = {
    'Market Intelligence': 'تحلیل تکنیکال بازار', 'MARKET STRENGTH': 'قدرت بازار',
    'VERY STRONG': 'خیلی قوی', 'STRONG': 'قوی', 'MODERATELY STRONG': 'نسبتاً قوی', 'NEUTRAL': 'خنثی',
    'MODERATELY WEAK': 'نسبتاً ضعیف', 'WEAK': 'ضعیف', 'VERY WEAK': 'خیلی ضعیف',
    '60-DAY PRICE TREND': 'روند قیمت 60 روزه', 'MARKET CONDITION': 'وضعیت بازار',
    'Momentum (MACD)': 'مومنتوم (MACD)', 'Positive': 'مثبت', 'Negative': 'منفی', '7D / 30D': '7 روز / 30 روز',
    'INSTITUTIONAL POSITIONING': 'موقعیت معامله‌گران بزرگ', 'Managed Money Net': 'خالص صندوق‌ها (Managed Money)',
    'CFTC Large Traders Net': 'خالص معامله‌گران بزرگ (CFTC)', 'Weekly Net Change': 'تغییر خالص هفتگی',
    'Open Interest': 'قراردادهای باز', 'OI Weekly Change': 'تغییر هفتگی قراردادهای باز',
    'Institutional Flow': 'جریان نهادی', 'STRONG INFLOW PRESSURE': 'فشار ورود قوی',
    'INFLOW PRESSURE': 'فشار ورود', 'NEUTRAL / MIXED': 'خنثی / مختلط', 'OUTFLOW PRESSURE': 'فشار خروج',
    'STRONG OUTFLOW PRESSURE': 'فشار خروج قوی', 'NO DIRECT INSTITUTIONAL DATA': 'داده‌ی مستقیم نهادی نیست',
    'MARKET FLOW PROXY': 'تخمین جریان بازار', 'NO DERIVATIVES DATA': 'داده‌ی مشتقات نیست',
    'STRONG UPSIDE FLOW PRESSURE': 'فشار صعودی قوی', 'UPSIDE FLOW PRESSURE': 'فشار صعودی',
    'BALANCED / MIXED': 'متعادل / مختلط', 'DOWNSIDE FLOW PRESSURE': 'فشار نزولی',
    'STRONG DOWNSIDE FLOW PRESSURE': 'فشار نزولی قوی',
    'No direct CFTC institutional data.': 'داده‌ی مستقیم CFTC در دسترس نیست.',
    'Funding Rate': 'نرخ تأمین مالی (Funding)', 'Long / Short': 'نسبت خرید به فروش',
    'Taker Buy / Sell': 'خرید به فروش تهاجمی', 'Futures 24H': 'تغییر آتی 24 ساعته',
    'Derivatives Pressure': 'فشار مشتقات',
    'Public derivatives feed unavailable.': 'داده‌ی عمومی مشتقات در دسترس نیست.',
    'Market Strength uses price/history only.': 'قدرت بازار فقط از قیمت و تاریخچه حساب شده.',
    'MACRO CONTEXT': 'زمینه‌ی کلان',
    'Lower USD/yields are treated as more supportive for precious metals.':
        'دلار و بازده پایین‌تر معمولاً برای فلزات گران‌بها حمایتی حساب می‌شن.',
    'FREE CRYPTO DERIVATIVES DATA': 'داده‌های رایگان مشتقات کریپتو',
    'Exchange derivatives pressure ≠ total-market capital flow or verified institutional flow.':
        'فشار مشتقات یک صرافی با جریان کل سرمایه‌ی بازار یا جریان نهادی یکی نیست.',
    'ALERT': 'هشدار', 'NO MAJOR ALERT': 'هشدار مهمی نیست',
    'MARKET STRENGTH IMPROVING': 'بهبود قدرت بازار', 'MARKET STRENGTH WEAKENING': 'تضعیف قدرت بازار',
    'UPWARD PRESSURE INCREASING': 'افزایش فشار صعودی', 'DOWNSIDE PRESSURE INCREASING': 'افزایش فشار نزولی',
    'INSTITUTIONAL INFLOW ALERT': 'هشدار ورود سرمایه‌ی نهادی', 'CAPITAL OUTFLOW ALERT': 'هشدار خروج سرمایه',
    'DERIVATIVES INFLOW PRESSURE': 'فشار ورود در بازار مشتقات',
    'DERIVATIVES OUTFLOW PRESSURE': 'فشار خروج در بازار مشتقات', 'FUNDING CROWDING ALERT': 'هشدار شلوغی موقعیت‌ها',
    'No major threshold change detected.': 'تغییر مهمی از آستانه‌ها دیده نشد.',
    'Large-trader positioning and participation show stronger inflow pressure.':
        'موقعیت و مشارکت معامله‌گران بزرگ فشار ورود قوی‌تری نشون می‌ده.',
    'Large-trader positioning and participation show stronger outflow pressure.':
        'موقعیت و مشارکت معامله‌گران بزرگ فشار خروج قوی‌تری نشون می‌ده.',
    'Market strength and derivatives pressure are weakening together.': 'قدرت بازار و فشار مشتقات با هم ضعیف می‌شن.',
    'Market strength and price momentum are strengthening together.': 'قدرت بازار و مومنتوم قیمت با هم قوی‌تر می‌شن.',
    'Market strength is weakening while downside pressure increases.': 'قدرت بازار ضعیف می‌شه و فشار نزولی بیشتر می‌شه.',
    'Market strength, price momentum, and derivatives pressure are strengthening together.':
        'قدرت بازار، مومنتوم قیمت و فشار مشتقات با هم قوی‌تر می‌شن.',
    'Market intelligence only • Not investment advice': 'صرفاً تحلیل بازار · توصیه‌ی سرمایه‌گذاری نیست',
}
_RX = [
    (r'(GOLD|SILVER|BITCOIN|ETHEREUM|SOLANA|XRP|CARDANO|DOGECOIN) \| (.+)', lambda m: f"{NAME_FA[m.group(1)]} | {m.group(2)}"),
    (r'(.+) vs prior daily close', r'\1 نسبت به بسته‌شدن روز قبل'),
    (r'Change: (.+) pts', r'تغییر: \1 امتیاز'),
    (r'CFTC report: (.+)', r'گزارش CFTC: \1'),
    (r'INSTITUTIONAL \+ (.+)', r'نهادی + \1'),
    (r'DERIVATIVES FLOW — (.+)', r'جریان مشتقات — \1'),
    (r'OI Change \((.+)\)', 'تغییر قراردادهای باز'),
    (r'Free source: (.+)', r'منبع رایگان: \1'),
    (r'USD Index \(5D\): (.+)', r'شاخص دلار (5 روز): \1'),
    (r'US 10Y \(5D\): (.+) bps', r'بازده 10 ساله (5 روز): \1 واحد'),
    (r'US 10Y \(5D\): (.+)', r'بازده 10 ساله (5 روز): \1'),
    (r'CPI YoY: (.+)', r'تورم سالانه: \1'),
    (r'Macro Support Score: (.+)', r'امتیاز حمایت کلان: \1'),
    (r'Active source: (.+)', r'منبع فعال: \1'),
    (r'24H Turnover / Volume: (.+)', r'حجم معاملات 24 ساعته: \1'),
    (r'Futures 24H: (.+)', r'تغییر آتی 24 ساعته: \1'),
    (r'Accounts: Long (.+) \| Short (.+)', r'حساب‌ها: خرید \1 · فروش \2'),
    (r'Funding: (.+)', r'نرخ تأمین مالی: \1'),
    (r'Strength rose (.+) points since the prior report\.', r'قدرت بازار نسبت به گزارش قبل \1 امتیاز بالا رفت.'),
    (r'Strength fell (.+) points since the prior report\.', r'قدرت بازار نسبت به گزارش قبل \1 امتیاز پایین اومد.'),
    (r'(.+) derivatives data show stronger upside flow pressure\.', r'داده‌های مشتقات \1 فشار صعودی قوی‌تری نشون می‌ده.'),
    (r'(.+) derivatives data show stronger downside flow pressure\.', r'داده‌های مشتقات \1 فشار نزولی قوی‌تری نشون می‌ده.'),
    (r'Funding is (.+), which can indicate crowded positioning\.', r'نرخ تأمین مالی غیرعادیه (\1)؛ می‌تونه نشونه‌ی شلوغی موقعیت‌ها باشه.'),
    (r'TradingLife  •  Updated (.+) Iran', r'TradingLife · به‌روزرسانی \1 به وقت تهران'),
    (r'Sources: (.+)', r'منابع: \1'),
] + [(re.escape(k), v) for k, v in _EX.items()]
MI_TR = Translator(_RX)

def spark(d,box,v,accent):
    if len(v)<2:return
    x1,y1,x2,y2=box; mn,mx=min(v),max(v); span=mx-mn or 1; pts=[]
    for i,z in enumerate(v):pts.append((x1+(x2-x1)*i/(len(v)-1),y2-(y2-y1)*(z-mn)/span))
    for i in range(1,4):yy=y1+(y2-y1)*i/4;d.line((x1,yy,x2,yy),fill=(43,49,61),width=1)
    d.line(pts,fill=accent,width=5,joint='curve')

def render(a):
    accent=GOLD if a['symbol']=='XAU' else SILVER if a['symbol']=='XAG' else BLUE
    im=Image.new('RGB',(W,H),BG);d=ImageDraw.Draw(im)
    if IS_FA:
        d=MirrorDraw(d,W,MI_TR)

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
            foot=f"Free source: {src}"
            if ci.get('available') and ci.get('date'):
                foot+=f"  •  CFTC: {ci['date']}"
            d.text((578,985),foot,font=F18,fill=MUTED)

    # Bottom data panel
    rounded(d,(50,1040,1030,1285),fill=PANEL2)
    if a['kind']=='metal':
        d.text((78,1068),'MACRO CONTEXT',font=F26,fill=TEXT)
        m=a['macro']
        d.text((78,1120),f"USD Index (5D): {m.get('usd5'):+.2f}%" if m.get('usd5') is not None else 'USD Index (5D): N/A',font=F21,fill=MUTED)
        d.text((390,1120),f"US 10Y (5D): {m.get('y105'):+.0f} bps" if m.get('y105') is not None else 'US 10Y (5D): N/A',font=F21,fill=MUTED)
        d.text((730,1120),f"CPI YoY: {m.get('cpi'):.1f}%" if m.get('cpi') is not None else 'CPI YoY: N/A',font=F21,fill=MUTED)
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
    if IS_FA:
        for k,ln in enumerate(wrap_fa(d.d, MI_TR(detail), d._fa(F18), 900)[:2]):
            d.text((78,1420+25*k),ln,font=F18,fill=TEXT)
    elif len(detail)>86:
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
    if a['kind']=='metal':
        src='Alpha Vantage • CFTC COT • ECB • US Treasury • BLS'
    else:
        src='OKX market data'
        if a['institutional'].get('available'): src+=' • CFTC COT'
        src += f" • {a.get('derivatives',{}).get('source','Public')} public derivatives data"
    d.text((75,1567),f"Sources: {src}",font=F16,fill=MUTED)
    d.text((75,1598),'Market intelligence only • Not investment advice',font=F16,fill=MUTED)

    os.makedirs(OUT,exist_ok=True)
    p=os.path.join(OUT,f"{a['symbol'].lower()}_tradinglife.png")
    im.save(p,'PNG',optimize=True)
    return p

MI_TAGS = {'XAU': '#Gold #XAUUSD', 'XAG': '#Silver #XAGUSD', 'BTC': '#Bitcoin #BTC', 'ETH': '#Ethereum #ETH',
           'SOL': '#Solana #SOL', 'XRP': '#XRP', 'ADA': '#Cardano #ADA', 'DOGE': '#Dogecoin #DOGE'}
OZ_PER_CONTRACT = {'XAU': 100, 'XAG': 5000}   # قرارداد COMEX: طلا ۱۰۰ اونس، نقره ۵۰۰۰ اونس


def okx_taker_flow(sym, total_vol=None, last=None):
    """
    خرید و فروش «تهاجمی» (taker) قراردادهای آتی OKX در ۲۴ ساعت اخیر (رایگان، بدون کلید).
    خرید بیشتر از فروش = فشار خرید (پول بیشتری با عجله وارد شده). این جریان واقعی ورود/خروج
    سرمایه به صرافی نیست؛ فقط معیار فشار خرید و فروش معامله‌گرهاست.
    """
    try:
        d = get_json(OKX_PUBLIC + '/api/v5/rubik/stat/taker-volume',
                     {'ccy': sym, 'instType': 'CONTRACTS', 'period': '1H'}, timeout=20)
        rows = d.get('data', []) if isinstance(d, dict) else []
        rows = rows[:24]                                    # جدیدترین‌ها اول: ۲۴ ساعت اخیر
        if len(rows) < 20:
            return None
        sell = sum(sf(r[1]) or 0 for r in rows); buy = sum(sf(r[2]) or 0 for r in rows)
        # واحد باید دلار باشه؛ با حجم کل ۲۴ ساعته مقایسه می‌کنیم تا مطمئن بشیم
        if total_vol and not (0.05 * total_vol <= buy + sell <= 20 * total_vol) and last:
            buy, sell = buy * last, sell * last             # اگه به واحد کوین بود، به دلار تبدیل کن
            if not (0.05 * total_vol <= buy + sell <= 20 * total_vol):
                print('Taker flow unit check failed', sym); return None
        return {'buy': buy, 'sell': sell, 'net': buy - sell}
    except Exception as e:
        print('OKX taker flow error', sym, e)
        return None


def usd_big(v):
    a = abs(v); sgn = '-' if v < 0 else ''
    return f"{sgn}${a/1e9:.2f}B" if a >= 1e9 else f"{sgn}${a/1e6:.1f}M" if a >= 1e6 else f"{sgn}${a:,.0f}"


def oi_reading(price_ch, oi_ch):
    """تفسیر استاندارد بازار آتی از ترکیب جهت قیمت و Open Interest."""
    if oi_ch is None or price_ch is None:
        return None
    if abs(oi_ch) < 1:
        return "little changed — no strong positioning shift"
    if abs(price_ch) < 0.2:
        return "rising with flat price — positions building" if oi_ch > 0 else "falling with flat price — positions closing"
    if price_ch > 0 and oi_ch > 0:
        return "rising with price up → new long positions"
    if price_ch < 0 and oi_ch > 0:
        return "rising with price down → new short positions"
    if price_ch > 0 and oi_ch < 0:
        return "falling with price up → shorts covering"
    return "falling with price down → longs closing"


def funding_reading(fr):
    if fr is None:
        return None
    p = fr * 100
    if p >= 0.03:
        return f"Funding {p:+.3f}% — longs paying a premium (crowded long)"
    if p <= -0.01:
        return f"Funding {p:+.3f}% — shorts paying a premium (crowded short)"
    return None                                        # حالت عادی: ذکرش لازم نیست


def caption(a):
    """متن کوتاه زیر هر کارت: فشار خرید/فروش و تغییر موقعیت‌ها — بدون سیگنال."""
    lines = [f"{a['name']} ({a['symbol']}/USD) — {price(a['price'])} ({a['daily']:+.2f}%)"]
    if a['kind'] == 'crypto':
        f = a.get('flow24'); bd = a.get('derivatives') or {}
        if f and f['buy'] + f['sell'] > 0:
            bp = 100 * f['buy'] / (f['buy'] + f['sell'])
            side = "buyers in control" if bp >= 52 else "sellers in control" if bp <= 48 else "balanced"
            lines.append(f"Futures pressure (OKX, 24h): buyers {bp:.0f}% · sellers {100 - bp:.0f}% — {side}")
        r = oi_reading(a['daily'], bd.get('oi_change_pct'))
        if r:
            lines.append(f"Open interest {bd['oi_change_pct']:+.1f}% since yesterday: {r}")
        fr = funding_reading(bd.get('funding_rate'))
        if fr:
            lines.append(fr)
        tags = f"{MI_TAGS.get(a['symbol'], '')} #OrderFlow"
    else:
        c = a.get('institutional') or {}
        if c.get('available') and c.get('net_change') is not None:
            wk = datetime.strptime(c['date'][:10], '%Y-%m-%d').strftime('%b %d')
            chg = c['net_change']
            notional = abs(chg) * OZ_PER_CONTRACT[a['symbol']] * a['price']
            if c['net'] >= 0:
                verb = "added net longs" if chg > 0 else "cut net longs"
            else:
                verb = "reduced net shorts" if chg > 0 else "added net shorts"
            lines.append(f"Large speculators (CFTC, week to {wk}): {verb} by {abs(chg)/1000:.1f}K contracts "
                         f"(≈{usd_big(notional)} notional)")
            lines.append(f"Still net {'long' if c['net'] >= 0 else 'short'} {abs(c['net'])/1000:.1f}K contracts")
        tags = f"{MI_TAGS.get(a['symbol'], '')} #Positioning"
    lines += ["", tags.strip()]
    return "\n".join(lines)


OI_FA = {
    "little changed — no strong positioning shift": "تقریباً بدون تغییر؛ جابه‌جایی مهمی در موقعیت‌ها نیست",
    "rising with flat price — positions building": "افزایش با قیمت ثابت؛ موقعیت‌های جدید در حال ساخته شدن",
    "falling with flat price — positions closing": "کاهش با قیمت ثابت؛ موقعیت‌ها در حال بسته شدن",
    "rising with price up → new long positions": "افزایش همراه با رشد قیمت ← موقعیت‌های خرید جدید",
    "rising with price down → new short positions": "افزایش همراه با افت قیمت ← موقعیت‌های فروش جدید",
    "falling with price up → shorts covering": "کاهش همراه با رشد قیمت ← بسته شدن موقعیت‌های فروش",
    "falling with price down → longs closing": "کاهش همراه با افت قیمت ← بسته شدن موقعیت‌های خرید",
}
MI_TAGS_FA = {'XAU': ('طلا', 'XAUUSD'), 'XAG': ('نقره', 'XAGUSD'), 'BTC': ('بیت_کوین', 'BTC'),
              'ETH': ('اتریوم', 'ETH'), 'SOL': ('سولانا', 'SOL'), 'XRP': ('ریپل', 'XRP'),
              'ADA': ('کاردانو', 'ADA'), 'DOGE': ('دوج_کوین', 'DOGE')}


def caption_fa(a):
    name = NAME_FA.get(a['name'].upper(), a['name'])
    lines = [f"{name} ({a['symbol']}/USD) — {price(a['price'])} ({a['daily']:+.2f}%)"]
    if a['kind'] == 'crypto':
        f = a.get('flow24'); bd = a.get('derivatives') or {}
        if f and f['buy'] + f['sell'] > 0:
            bp = 100 * f['buy'] / (f['buy'] + f['sell'])
            side = "کنترل دست خریداران" if bp >= 52 else "کنترل دست فروشندگان" if bp <= 48 else "متعادل"
            lines.append(f"فشار بازار آتی (OKX، 24 ساعت): خریداران {bp:.0f}% · فروشندگان {100 - bp:.0f}% — {side}")
        r = oi_reading(a['daily'], bd.get('oi_change_pct'))
        if r:
            lines.append(f"قراردادهای باز {bd['oi_change_pct']:+.1f}% نسبت به دیروز: {OI_FA[r]}")
        fr = bd.get('funding_rate')
        if fr is not None and fr * 100 >= 0.03:
            lines.append(f"نرخ تأمین مالی {fr*100:+.3f}% — خریداران هزینه‌ی اضافه می‌دن (شلوغی خرید)")
        elif fr is not None and fr * 100 <= -0.01:
            lines.append(f"نرخ تأمین مالی {fr*100:+.3f}% — فروشندگان هزینه‌ی اضافه می‌دن (شلوغی فروش)")
        extra = "جریان_سفارش"
    else:
        c = a.get('institutional') or {}
        if c.get('available') and c.get('net_change') is not None:
            wk = fa_date(datetime.strptime(c['date'][:10], '%Y-%m-%d'), weekday=False, year=False)
            chg = c['net_change']
            notional = abs(chg) * OZ_PER_CONTRACT[a['symbol']] * a['price']
            if c['net'] >= 0:
                verb = "موقعیت خالص خرید رو افزایش دادن" if chg > 0 else "موقعیت خالص خرید رو کاهش دادن"
            else:
                verb = "موقعیت خالص فروش رو کاهش دادن" if chg > 0 else "موقعیت خالص فروش رو افزایش دادن"
            lines.append(f"معامله‌گران بزرگ (CFTC، هفته‌ی منتهی به {wk}): {abs(chg)/1000:.1f}K قرارداد "
                         f"{verb} (ارزش اسمی حدود {usd_big(notional)})")
            lines.append(f"موقعیت کلی همچنان خالص {'خرید' if c['net'] >= 0 else 'فروش'}: {abs(c['net'])/1000:.1f}K قرارداد")
        extra = "موقعیت_معامله_گران"
    fa_tag, sym_tag = MI_TAGS_FA.get(a['symbol'], (a['symbol'], ''))
    lines += ["", hashtags(fa_tag, sym_tag, extra)]
    return "\n".join(lines)


def send(p,a):
    with open(p,'rb') as f:
        r=requests.post(
            f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',
            data={'chat_id':CHAT_ID,'caption':caption_fa(a) if IS_FA else caption(a)},
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
    # آخر هفته بازار طلا و نقره بسته‌ست: کارت تکراری فلزات نمی‌فرستیم (کریپتو ۲۴/۷ ادامه داره)
    if ASSET_FILTER=='ALL' and datetime.now(ZoneInfo('America/New_York')).weekday()>=5:
        selected=[x for x in selected if x['kind']!='metal']
        print('Weekend: metals skipped (market closed).')
    state=load(STATE,{}); hs={}; m={}
    if RUN_MODE=='refresh':
        c=load(CACHE,{});hs=c.get('histories',{});m=c.get('macro',{})
        if any(a['symbol'] not in hs for a in selected):RUN_MODE='full'
    m=macro() or m   # macro sources are free; refresh every run, fall back to cache if they fail
    if RUN_MODE=='full':
        for a in selected:print('History',a['symbol']);hs[a['symbol']]=history(a)
        if ASSET_FILTER=='ALL':save(CACHE,{'saved_at':datetime.utcnow().isoformat()+'Z','histories':hs,'macro':m})
    results=[];fail=[]
    for a in selected:
        try:
            print('Spot',a['symbol']);sp=spot(a);results.append(intelligence(a,hs[a['symbol']],sp,m,state.get(a['symbol'],{})))
            if a['kind']=='crypto':
                bd=results[-1].get('derivatives') or {}
                results[-1]['flow24']=okx_taker_flow(a['symbol'],bd.get('futures_quote_volume'),sp); time.sleep(0.5)
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
    if fail:
        # Warnings go to the GitHub Actions log ONLY — never to the public channel.
        print('WARNINGS (not sent to Telegram):')
        for x in fail: print('  -',x.replace(ALPHA_KEY,'***'))
    print('Done',RUN_MODE,ASSET_FILTER,len(results))
if __name__=='__main__':main()
