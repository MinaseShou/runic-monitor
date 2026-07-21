#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 backfill.json:儀表板 sparkline 的歷史序列回填(一次性/可重跑;display 用,不進 state.json)
序列與口徑:
- vix_p/ratio_p:CBOE 全史 → 每日 p252 分位,回填 252 交易日
- cost_bp:FinMind TXO 逐日回算(監測同邏輯:5-9DTE 週約 95/90 put spread),回填 ~120 交易日(API 重)
- margin_bal_dd/chg63/px_dd:FinMind 上市融資+TAIEX,回填 252 交易日
- fuel_turn_pct/vol60_pct:⑧⑨ 崩盤雙軸(量能 20 日均 p756、60 日年化波動 p756),回填 252 交易日(2026-07-21 新增)
- kr_dd:KOFIA 日頻 3 年,回撤=距 252 觀測日高(與監測同口徑)
- jp_dd:JPX 過去推移表(2013+ 檔,東名兩市場制度+一般合計買残金額;⚠️與 live jp_hist 的 mtseisan 委託口徑差 ~0.1%,僅供顯示),距 52 週峰,回填 ~104 週
- fx_dd:Yahoo JPY=X 2 年,距 63 日高
用法:python3 build_backfill.py → 寫 backfill.json;commit 後 Pages 生效"""
import io, json, re, time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
OUT = {}

def emit(key, ser, digits=4):
    ser = ser.dropna()
    OUT[key] = [[d.strftime('%Y-%m-%d'), round(float(v), digits)] for d, v in ser.items()]
    print(f'{key}: {len(ser)} 筆 {ser.index[0].date()}→{ser.index[-1].date()}')

def fetch_cboe(name):
    r = requests.get(f'https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv', timeout=60)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip().upper() for c in df.columns]
    df['DATE'] = pd.to_datetime(df['DATE'])
    col = 'CLOSE' if 'CLOSE' in df.columns else df.columns[-1]
    return df.set_index('DATE')[col]

def fetch_finmind(dataset, data_id, start, end):
    r = requests.get('https://api.finmindtrade.com/api/v4/data', params={
        'dataset': dataset, 'data_id': data_id, 'start_date': start, 'end_date': end}, timeout=120)
    return pd.DataFrame(r.json().get('data', []))

def nth_wed(y, m, n):
    d = pd.Timestamp(y, m, 1)
    return d + pd.Timedelta(days=(2 - d.dayofweek) % 7) + pd.Timedelta(weeks=n - 1)

def expiry_of(contract):
    if 'W' in contract:
        ym, wn = contract.split('W')
        return nth_wed(int(ym[:4]), int(ym[4:6]), int(wn))
    return nth_wed(int(contract[:4]), int(contract[4:6]), 3)

today = pd.Timestamp.today().normalize()

# ---------- ① VIX/ratio p252 ----------
vix, vvix = fetch_cboe('VIX'), fetch_cboe('VVIX')
df = pd.concat([vix.rename('VIX'), vvix.rename('VVIX')], axis=1).dropna()
ratio = df.VVIX / df.VIX
vix_p = df.VIX.rolling(252).apply(lambda w: (w <= w.iloc[-1]).mean())
ratio_p = ratio.rolling(252).apply(lambda w: (w <= w.iloc[-1]).mean())
emit('vix_p', vix_p.tail(252)); emit('ratio_p', ratio_p.tail(252))
vxn = fetch_cboe('VXN')   # ⑦(2026-07-18 曾一次性補進 json 未入生成器→2026-07-21 重生時被蓋,今納入真源)
emit('vxn_p', vxn.rolling(252).apply(lambda w: (w <= w.iloc[-1]).mean()).tail(252), digits=3)

# ---------- ② 台灣融資 ----------
m = fetch_finmind('TaiwanStockTotalMarginPurchaseShortSale', '',
                  (today - pd.Timedelta(days=800)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
tj = fetch_finmind('TaiwanStockPrice', 'TAIEX',
                   (today - pd.Timedelta(days=1900)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))  # 1900=供雙軸 252 筆回填各自帶足 756 分位窗
bal = m[m['name'] == 'MarginPurchaseMoney'].set_index('date')['TodayBalance'].astype(float)
bal.index = pd.to_datetime(bal.index); bal = bal.sort_index()
px = tj.set_index('date')['close'].astype(float)
px.index = pd.to_datetime(px.index); px = px.sort_index()
emit('margin_bal_dd', (bal / bal.rolling(252, min_periods=200).max() - 1).tail(252))
emit('margin_chg63', (bal / bal.shift(63) - 1).tail(252))
emit('margin_px_dd', (px / px.rolling(252, min_periods=200).max() - 1).tail(252))

# ---------- ②a ⑥RV+③b 上櫃(同 2026-07-18 部署口徑;上櫃源=state.json tpex_hist 快取) ----------
emit('rv20', (px.pct_change().rolling(20).std() * (252 ** 0.5)).tail(252))
try:
    st = json.loads((HERE / 'state.json').read_text())
    tp = pd.Series(st.get('tpex_hist', {}), dtype=float)
    tp.index = pd.to_datetime(tp.index)
    tp = tp.sort_index()
    emit('tp_bal_dd', (tp / tp.rolling(252, min_periods=200).max() - 1).tail(252))
except Exception as e:
    print(f'tp_bal_dd skip {type(e).__name__}')

# ---------- ②b 崩盤雙軸(⑧⑨;與監測同口徑:trailing 756 分位) ----------
mny = tj.set_index('date')['Trading_money'].astype(float)
mny.index = pd.to_datetime(mny.index); mny = mny.sort_index()
t20 = mny.rolling(20).mean()
emit('fuel_turn_pct', t20.rolling(756, min_periods=250).rank(pct=True).tail(252), digits=3)
v60 = px.pct_change().rolling(60).std() * (252 ** 0.5)
emit('vol60_pct', v60.rolling(756, min_periods=250).rank(pct=True).tail(252), digits=3)

# ---------- ③ 韓國 KOFIA ----------
payload = {"dmSearch": {"tmpV1": "D", "tmpV40": "01",
                        "tmpV45": (today - pd.Timedelta(days=1150)).strftime('%Y%m%d'),
                        "tmpV46": today.strftime('%Y%m%d'), "OBJ_NM": "STATSCU0100000070BO"}}
kh = dict(UA, **{"Content-Type": "application/json; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
      "Origin": "https://freesis.kofia.or.kr",
      "Referer": "https://freesis.kofia.or.kr/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId=STATSCU0100000070"})
rk = requests.post('https://freesis.kofia.or.kr/meta/getMetaDataList.do', json=payload, headers=kh, timeout=60)
kr = pd.Series({pd.Timestamp(str(r['TMPV1'])): float(str(r['TMPV2']).replace(',', ''))
                for r in rk.json().get('ds1', []) if r.get('TMPV2')}).sort_index()
emit('kr_dd', (kr / kr.rolling(252, min_periods=60).max() - 1).tail(270))

# ---------- ④ 日本 JPX 過去推移表 ----------
hp = requests.get('https://www.jpx.co.jp/markets/statistics-equities/margin/06.html', headers=UA, timeout=60)
xls = re.findall(r'href="(/markets/[^"]+?\.xls)"', hp.text)
frames = []
for path in xls:
    rx = requests.get(f'https://www.jpx.co.jp{path}', headers=UA, timeout=60)
    dfj = pd.read_excel(io.BytesIO(rx.content), sheet_name=0, header=None)
    dfj = dfj[[0, 4]].copy()          # c0=申込日, c4=合計買残金額(百万円)
    dfj.columns = ['d', 'buy']
    dfj['d'] = pd.to_datetime(dfj['d'], errors='coerce')
    dfj['buy'] = pd.to_numeric(dfj['buy'], errors='coerce')
    frames.append(dfj.dropna())
jp = pd.concat(frames).drop_duplicates('d').set_index('d').sort_index()['buy']
assert 5e6 < jp.iloc[-1] < 9e6, f'JPX 買残金額尾值 sanity fail: {jp.iloc[-1]}'
jp_dd = (jp / jp.rolling(52, min_periods=26).max() - 1)
emit('jp_dd', jp_dd.tail(104))
emit('jp_buy_tn', (jp / 1e6).tail(104), digits=3)   # 兆円水位(參考)

# ---------- ⑤ USDJPY ----------
ry = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/JPY=X',
                  params={'range': '2y', 'interval': '1d'}, headers=UA, timeout=60)
q = ry.json()['chart']['result'][0]
fx = pd.Series(q['indicators']['quote'][0]['close'],
               index=pd.to_datetime(q['timestamp'], unit='s').normalize()).dropna()
fx = fx[~fx.index.duplicated(keep='last')]
emit('fx_dd', (fx / fx.rolling(63).max() - 1).tail(252))

# ---------- ⑥ 保費 cost_bp(FinMind TXO 逐日,重) ----------
fu = fetch_finmind('TaiwanFuturesDaily', 'TX',
                   (today - pd.Timedelta(days=200)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
fu['date'] = pd.to_datetime(fu['date'])
fu['close'] = pd.to_numeric(fu['close'], errors='coerce')
fu = fu[(fu.trading_session == 'position') & (~fu.contract_date.astype(str).str.contains('/'))]
days = sorted(fu.date.unique())[-120:]
cost_rows = {}
for i, D in enumerate(days):
    D = pd.Timestamp(D)
    try:
        op = fetch_finmind('TaiwanOptionDaily', 'TXO', D.strftime('%Y-%m-%d'), D.strftime('%Y-%m-%d'))
        if op.empty:
            continue
        op['date'] = pd.to_datetime(op['date'])
        for c in ('strike_price', 'settlement_price'):
            op[c] = pd.to_numeric(op[c], errors='coerce')
        fud = fu[fu.date == D]
        if fud.empty:
            continue
        S = float(fud.sort_values('contract_date').iloc[0]['close'])
        puts = op[(op.call_put == 'put') & (op.trading_session == 'position')
                  & (~op.contract_date.str.contains('F'))].copy()
        puts['T_days'] = (puts['contract_date'].map(expiry_of) - D).dt.days
        cand = puts[puts.T_days.between(5, 9)]
        if cand.empty:
            continue
        c0 = cand[cand.T_days == cand.T_days.min()]
        snap = c0[c0.settlement_price > 0].set_index('strike_price')
        if snap.empty:
            continue
        K_hi = min(snap.index, key=lambda k: abs(k - S * 0.95))
        K_lo = min(snap.index, key=lambda k: abs(k - S * 0.90))
        cost_rows[D] = round((float(snap.settlement_price.loc[K_hi]) - float(snap.settlement_price.loc[K_lo])) / S * 1e4, 2)
    except Exception as e:
        print(f'  cost_bp {D.date()} skip {type(e).__name__}')
    if i % 20 == 0:
        print(f'  cost_bp 進度 {i}/{len(days)}', flush=True)
    time.sleep(0.4)
emit('cost_bp', pd.Series(cost_rows).sort_index(), digits=2)

(HERE / 'backfill.json').write_text(json.dumps(
    dict(generated_at=datetime.now().isoformat(timespec='seconds'), series=OUT), ensure_ascii=False))
print('DONE → backfill.json')
