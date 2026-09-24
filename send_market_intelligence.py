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
CFTC_DISAGG = 'https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
CFTC_LEGACY = 'https://publicreporting.cftc.gov/resource/6dca-aqww.json'
CACHE = 'market_intelligence_cache.json'
STATE = 'tradinglife_state.json'
OUT = 'market_cards'

ASSETS = [
    {'symbol':'XAU','name':'Gold','kind':'metal','api_symbol':'GOLD','cftc':'disagg','code':'088691'},
    {'symbol':'XAG','name':'Silver','kind':'metal','api_symbol':'SILVER','cftc':'disagg','code':'084691'},
    {'symbol':'BTC','name':'Bitcoin','kind':'crypto','cftc':'legacy','code':'133741'},
    {'symbol':'ETH','name':'Ethereum','kind':'crypto','cftc':'legacy','code':'146021'},
    {'symbol':'SOL','name':'Solana','kind':'crypto','cftc':None,'code':None},
    {'symbol':'XRP','name':'XRP','kind':'crypto','cftc':None,'code':None},
    {'symbol':'ADA','name':'Cardano','kind':'crypto','cftc':None,'code':None},
    {'symbol':'DOGE','name':'Dogecoin','kind':'crypto','cftc':None,'code':None},
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
    t=tech(h,sp); ci=cftc(asset); ins=inst_score(ci,t); proxy=flow_proxy(t); ms=macro_score(m) if asset['kind']=='metal' else None
    if asset['kind']=='metal':strength=.5*t['score']+.3*ins+.2*ms if ins is not None else .65*t['score']+.35*ms
    else:strength=.55*t['score']+.3*ins+.15*proxy if ins is not None else .65*t['score']+.35*proxy
    strength=clamp(strength); ps=sf(prev.get('strength')); pf=sf(prev.get('flow_score'))
    ds=(strength-ps) if ps is not None else None; df=(ins-pf) if ins is not None and pf is not None else None
    alert='NO MAJOR ALERT'; detail='No major threshold change detected.'
    if ins is not None and ins>=72 and (df is None or df>=5):alert='INSTITUTIONAL INFLOW ALERT'; detail='Large-trader positioning and participation show stronger inflow pressure.'
    elif ins is not None and ins<=28 and (df is None or df<=-5):alert='CAPITAL OUTFLOW ALERT'; detail='Large-trader positioning and participation show stronger outflow pressure.'
    elif strength>=68 and t['daily']>0 and (ds is None or ds>=4):alert='UPWARD PRESSURE INCREASING'; detail='Market strength and price momentum are strengthening together.'
    elif strength<=32 and t['daily']<0 and (ds is None or ds<=-4):alert='DOWNSIDE PRESSURE INCREASING'; detail='Market strength is weakening while downside pressure increases.'
    elif ds is not None and ds>=8:alert='MARKET STRENGTH IMPROVING'; detail=f'Strength rose {ds:.1f} points since the prior report.'
    elif ds is not None and ds<=-8:alert='MARKET STRENGTH WEAKENING'; detail=f'Strength fell {abs(ds):.1f} points since the prior report.'
    return {**t,'symbol':asset['symbol'],'name':asset['name'],'kind':asset['kind'],'market_strength':strength,'strength_label':strength_label(strength),
            'strength_delta':ds,'macro':m,'macro_score':ms,'institutional':ci,'flow_score':ins,'flow_label':flow_label(ins),'proxy':proxy,'alert':alert,'alert_detail':detail}

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

W,H=1080,1600; BG=(11,14,20); PANEL=(24,29,39); PANEL2=(18,22,31); TEXT=(240,244,248); MUTED=(155,165,178); GREEN=(44,200,134); RED=(239,83,80); AMBER=(245,185,66); GOLD=(212,175,55); SILVER=(195,202,212); BLUE=(91,155,255)
def font(n,b=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(p,n) if os.path.exists(p) else ImageFont.load_default()
F18=font(18);F21=font(21);F24=font(24);F26=font(26,1);F30=font(30,1);F38=font(38,1);F46=font(46,1);F64=font(64,1)
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
    d.text((55,42),'TRADINGLIFE',font=F38,fill=TEXT);d.text((55,91),'Market Intelligence',font=F21,fill=MUTED)
    d.text((1025,52),f"{a['name'].upper()} | {a['symbol']}/USD",font=F30,fill=accent,anchor='ra')
    rounded(d,(50,140,1030,375));d.text((82,172),price(a['price']),font=F64,fill=TEXT);d.text((85,260),f"{a['daily']:+.2f}% vs prior daily close",font=F24,fill=GREEN if a['daily']>=0 else RED)
    d.text((690,175),'MARKET STRENGTH',font=F18,fill=MUTED);d.text((690,212),f"{a['market_strength']:.0f} / 100",font=F46,fill=col(a['market_strength']));d.text((690,275),a['strength_label'],font=F24,fill=col(a['market_strength']))
    if a['strength_delta'] is not None:d.text((690,315),f"Change: {a['strength_delta']:+.1f} pts",font=F21,fill=GREEN if a['strength_delta']>=0 else RED)
    rounded(d,(50,400,1030,625),fill=PANEL2);d.text((78,425),'60-DAY PRICE TREND',font=F18,fill=MUTED);spark(d,(82,475,995,595),a['closes'],accent)
    rounded(d,(50,650,520,1015));d.text((78,678),'MARKET CONDITION',font=F26,fill=TEXT)
    metric(d,78,735,405,'RSI (14)',f"{a['rsi']:.1f}" if a['rsi'] is not None else 'N/A');metric(d,78,780,405,'EMA 20',price(a['ema20']));metric(d,78,825,405,'EMA 50',price(a['ema50']));metric(d,78,870,405,'EMA 200',price(a['ema200']))
    md='Positive' if a['macd'] is not None and a['macd_signal'] is not None and a['macd']>a['macd_signal'] else 'Negative';metric(d,78,915,405,'Momentum (MACD)',md,GREEN if md=='Positive' else RED);metric(d,78,960,405,'7D / 30D',f"{a['r7']:+.1f}% / {a['r30']:+.1f}%")
    rounded(d,(550,650,1030,1015));d.text((578,678),'INSTITUTIONAL POSITIONING',font=F26,fill=TEXT);ci=a['institutional']
    if ci.get('available'):
        metric(d,578,735,410,ci['cohort']+' Net',compact(ci['net']),GREEN if ci['net']>=0 else RED);metric(d,578,780,410,'Weekly Net Change',compact(ci['net_change']),GREEN if (ci['net_change'] or 0)>=0 else RED);metric(d,578,825,410,'Open Interest',compact(ci['oi']));metric(d,578,870,410,'OI Weekly Change',f"{ci['oi_change']:+.2f}%" if ci['oi_change'] is not None else 'N/A',GREEN if (ci['oi_change'] or 0)>=0 else RED)
        metric(d,578,915,410,'Institutional Flow',f"{a['flow_score']:.0f} / 100",col(a['flow_score']));d.text((578,962),a['flow_label'],font=F18,fill=col(a['flow_score']));d.text((578,987),f"CFTC report: {ci.get('date','')}",font=F18,fill=MUTED)
    else:
        d.text((578,755),'No direct CFTC institutional',font=F21,fill=AMBER);d.text((578,790),'positioning used for this asset.',font=F21,fill=AMBER);d.text((578,850),'Market Flow Pressure',font=F21,fill=MUTED);d.text((578,890),f"{a['proxy']:.0f} / 100",font=F38,fill=col(a['proxy']));d.text((578,950),'Market-derived flow proxy only,',font=F18,fill=MUTED);d.text((578,978),'not an institutional-flow claim.',font=F18,fill=MUTED)
    rounded(d,(50,1040,1030,1285),fill=PANEL2)
    if a['kind']=='metal':
        d.text((78,1068),'MACRO CONTEXT',font=F26,fill=TEXT);m=a['macro'];d.text((78,1120),f"USD Index (5D): {m.get('usd5'):+.2f}%" if m.get('usd5') is not None else 'USD Index (5D): N/A',font=F21,fill=MUTED);d.text((390,1120),f"US 10Y (5D): {m.get('y105'):+.0f} bps" if m.get('y105') is not None else 'US 10Y (5D): N/A',font=F21,fill=MUTED);d.text((730,1120),f"CPI YoY: {m.get('cpi'):.2f}%" if m.get('cpi') is not None else 'CPI YoY: N/A',font=F21,fill=MUTED);d.text((78,1175),f"Macro Support Score: {a['macro_score']:.0f} / 100",font=F24,fill=col(a['macro_score']));d.text((78,1225),'Lower USD/yields are treated as more supportive for precious metals.',font=F18,fill=MUTED)
    else:
        d.text((78,1068),'MARKET FLOW',font=F26,fill=TEXT);vr=a.get('volume_ratio');d.text((78,1122),f"Volume / 20D Avg: {vr:.2f}x" if vr is not None else 'Volume / 20D Avg: N/A',font=F21,fill=MUTED);d.text((390,1122),f"30D Return: {a['r30']:+.2f}%",font=F21,fill=GREEN if a['r30']>=0 else RED);d.text((730,1122),f"Market Cap: {money(a.get('market_cap'))}",font=F21,fill=MUTED);d.text((78,1180),f"Market Flow Pressure: {a['proxy']:.0f} / 100",font=F24,fill=col(a['proxy']));
        if not ci.get('available'):d.text((78,1230),'No direct CFTC positioning: this flow score is market-derived only.',font=F18,fill=MUTED)
    rounded(d,(50,1310,1030,1495),fill=(27,31,42),outline=col(a['market_strength']),w=2);d.text((78,1338),'ALERT',font=F21,fill=MUTED)
    ac=RED if any(x in a['alert'] for x in ('OUTFLOW','DOWNSIDE','WEAKENING')) else GREEN if any(x in a['alert'] for x in ('INFLOW','UPWARD','IMPROVING')) else AMBER
    d.text((78,1375),a['alert'],font=F30,fill=ac);d.text((78,1425),a['alert_detail'],font=F21,fill=TEXT)
    ir=datetime.now(ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M');d.text((55,1533),f'TradingLife • Updated {ir} Iran',font=F18,fill=MUTED);d.text((1025,1533),'Sources: Alpha Vantage • CFTC COT • FRED | Market intelligence, not investment advice',font=F18,fill=MUTED,anchor='ra')
    os.makedirs(OUT,exist_ok=True);p=os.path.join(OUT,f"{a['symbol'].lower()}_tradinglife.png");im.save(p,'PNG',optimize=True);return p

def send(p,a):
    inst=a['flow_label'] if a['institutional'].get('available') else 'No direct CFTC institutional coverage'
    cap=f"TradingLife | {a['name']} ({a['symbol']}/USD)\nMarket Strength: {a['market_strength']:.0f}/100 — {a['strength_label']}\nInstitutional: {inst}\nAlert: {a['alert']}\nMarket intelligence only — not investment advice."
    with open(p,'rb') as f:
        r=requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto',data={'chat_id':CHAT_ID,'caption':cap},files={'photo':f},timeout=60);r.raise_for_status()

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
        p=render(a);send(p,a);state[a['symbol']]={'strength':round(a['market_strength'],3),'flow_score':round(a['flow_score'],3) if a['flow_score'] is not None else None,'alert':a['alert'],'price':a['price'],'updated':datetime.utcnow().isoformat()+'Z'};time.sleep(.8)
    save(STATE,state)
    if fail:requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',data={'chat_id':CHAT_ID,'text':'TradingLife warnings:\n'+'\n'.join(fail)},timeout=30)
    print('Done',RUN_MODE,ASSET_FILTER,len(results))
if __name__=='__main__':main()
