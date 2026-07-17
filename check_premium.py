#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TXO 保險+市場監測 v5——GitHub Actions 版(雙軌之雲端軌;Mac 正本=~/RUNiC_LOCAL/txo/monitor/check_premium.py)
與 Mac 版差異僅三點:①通知=開 GitHub Issue(本機 fallback=osascript)②USDJPY 加 FRED fallback ③每輪重寫 README 儀表。
五面旗:
① G2 壓力開關(VIX p252>=70% 或 VVIX/VIX ratio p252<=20%;翻轉時通知)= 事故前上膛候選(探索性,2026-07-12 radar 相關性分析)
② 保費慢開關(cost_bp 連 20 個交易日 <=10bp)= 年代級可負擔
③ 融資 regime(NORMAL/WARNING/SPIRAL;韓式螺旋判別,margin_regime 案:擇時判死僅監測)
④ 韓國融資進度條(KOFIA 日頻;回撤跨 -10%/-20% 通知)
⑤ 日本槓桿+USDJPY(JPX 週頻 mtseisan 委託買い残+FRED 匯率;回撤跨 -10%/-20%、円距 63 日高跨 -3%/-6% 通知;純描述未回測)
口徑(2026-07-14 翔核定統一):「回撤」=距 252 觀測日(週頻=52 週)內高點,逐日僅用當日已知資訊、無前視;
「水位」=現值在近 3 年(756 觀測)分佈的百分位——回撤答「跌多少」、水位答「堆多高」,兩面並列。
資料:FinMind 免 token(TXO+TX)+CBOE 官方 CSV(VIX/VVIX)+KOFIA+JPX+FRED/Yahoo;fail=靜默 skip 留 log"""
import json, os, re, subprocess, sys, time, io
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
STATE = HERE / 'state.json'
LOG = HERE / 'log.md'
ARM_BP, ARM_DAYS = 10.0, 20
UA = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
ON_GITHUB = bool(os.environ.get('GITHUB_ACTIONS'))

def fetch_finmind(dataset, data_id, start, end):
    for _ in range(3):
        try:
            r = requests.get('https://api.finmindtrade.com/api/v4/data', params={
                'dataset': dataset, 'data_id': data_id,
                'start_date': start, 'end_date': end}, timeout=120)
            return pd.DataFrame(r.json().get('data', []))
        except Exception:
            time.sleep(10)
    return pd.DataFrame()

def fetch_cboe(name):
    for _ in range(3):
        try:
            r = requests.get(f'https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv', timeout=60)
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip().upper() for c in df.columns]
            df['DATE'] = pd.to_datetime(df['DATE'])
            col = 'CLOSE' if 'CLOSE' in df.columns else df.columns[-1]
            return df.set_index('DATE')[col]
        except Exception:
            time.sleep(10)
    return pd.Series(dtype=float)

def fetch_fx_fred():
    r = requests.get('https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXJPUS', headers=UA, timeout=60)
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ['date', 'v']
    df['v'] = pd.to_numeric(df['v'], errors='coerce')
    return df.dropna().set_index(pd.to_datetime(df.dropna()['date']))['v']

def fetch_fx_yahoo():
    r = requests.get('https://query1.finance.yahoo.com/v8/finance/chart/JPY=X',
                     params={'range': '6mo', 'interval': '1d'}, headers=UA, timeout=60)
    q = r.json()['chart']['result'][0]
    return pd.Series(q['indicators']['quote'][0]['close'],
                     index=pd.to_datetime(q['timestamp'], unit='s')).dropna()

def nth_wed(y, m, n):
    d = pd.Timestamp(y, m, 1)
    return d + pd.Timedelta(days=(2 - d.dayofweek) % 7) + pd.Timedelta(weeks=n - 1)

def expiry_of(contract):
    if 'W' in contract:
        ym, wn = contract.split('W')
        return nth_wed(int(ym[:4]), int(ym[4:6]), int(wn))
    return nth_wed(int(contract[:4]), int(contract[4:6]), 3)

def log_line(msg):
    with open(LOG, 'a') as f:
        f.write(f'- {datetime.now().strftime("%Y-%m-%d %H:%M")} (UTC) {msg}\n' if ON_GITHUB
                else f'- {datetime.now().strftime("%Y-%m-%d %H:%M")} {msg}\n')

def notify(title, msg):
    if ON_GITHUB:
        try:
            requests.post(f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/issues",
                          headers={'Authorization': f"Bearer {os.environ['GH_TOKEN']}",
                                   'Accept': 'application/vnd.github+json'},
                          json={'title': title, 'body': msg}, timeout=30)
        except Exception as e:
            log_line(f'WARN:Issue 通知失敗 {type(e).__name__}:{title}')
    else:
        subprocess.run(['osascript', '-e',
                        f'display notification "{msg}" with title "{title}" sound name "Glass"'],
                       check=False)

state = json.loads(STATE.read_text()) if STATE.exists() else {}
hist = state.get('history', state if isinstance(state, list) else [])
prev = state.get('last', {}) if isinstance(state, dict) else {}

# ---------- ① G2/G3 壓力開關(CBOE) ----------
vix = fetch_cboe('VIX')
vvix = fetch_cboe('VVIX')
g2 = g3 = None
vix_p = ratio_p = float('nan')
if len(vix) and len(vvix):
    df = pd.concat([vix.rename('VIX'), vvix.rename('VVIX')], axis=1).dropna().tail(400)
    ratio = df.VVIX / df.VIX
    w = df.VIX.tail(252)
    vix_p = float((w <= w.iloc[-1]).mean())
    wr = ratio.tail(252)
    ratio_p = float((wr <= wr.iloc[-1]).mean())
    g1 = vix_p >= 0.70
    g2 = bool(g1 or ratio_p <= 0.20)
    g3 = bool(g1 and ratio_p <= 0.30)
    vix_date = str(df.index[-1].date())
else:
    log_line('WARN:CBOE 抓取失敗,G2 本輪 UNKNOWN(不默認 OFF)')
    vix_date = None

# ---------- ② 保費(FinMind) ----------
today = pd.Timestamp.today().normalize()
op = fetch_finmind('TaiwanOptionDaily', 'TXO', (today - pd.Timedelta(days=14)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
fu = fetch_finmind('TaiwanFuturesDaily', 'TX', (today - pd.Timedelta(days=14)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
cost = cost_bp = None
T = contract = None
if not op.empty and not fu.empty:
    op['date'] = pd.to_datetime(op['date'])
    fu['date'] = pd.to_datetime(fu['date'])
    for c in ('strike_price', 'settlement_price'):
        op[c] = pd.to_numeric(op[c], errors='coerce')
    fu['close'] = pd.to_numeric(fu['close'], errors='coerce')
    T = op.loc[op.trading_session == 'position', 'date'].max()
    fud = fu[(fu.date == T) & (fu.trading_session == 'position')]
    fud = fud[~fud.contract_date.astype(str).str.contains('/')]
    if not fud.empty:
        S = float(fud.sort_values('contract_date').iloc[0]['close'])
        puts = op[(op.date == T) & (op.call_put == 'put') & (op.trading_session == 'position')
                  & (~op.contract_date.str.contains('F'))].copy()
        puts['T_days'] = (puts['contract_date'].map(expiry_of) - T).dt.days
        cand = puts[puts.T_days.between(5, 9)]
        if not cand.empty:
            c0 = cand[cand.T_days == cand.T_days.min()]
            snap = c0[c0.settlement_price > 0].set_index('strike_price')
            if not snap.empty:
                K_hi = min(snap.index, key=lambda k: abs(k - S * 0.95))
                K_lo = min(snap.index, key=lambda k: abs(k - S * 0.90))
                # 腳位 guard(2026-07-17):大跌後週選鏈深 OTM 履約價未掛時,就近抓會變成窄價差假報價
                # (實例:7/17 W4 只剩 40,200 → 40,600/40,200=400 點寬、11.7bp 假便宜)。
                # 兩腳偏離口徑 >1.2% → 記 N/A 寧缺勿假;ARM 計數器本就跳過 None。
                if abs(K_hi / S - 0.95) <= 0.012 and abs(K_lo / S - 0.90) <= 0.012:
                    cost = float(snap.settlement_price.loc[K_hi] - snap.settlement_price.loc[K_lo])
                    cost_bp = round(cost / S * 1e4, 2)
                    contract = str(c0.contract_date.iloc[0])

# ---------- ③ 融資 regime(韓式螺旋判別;margin_regime 案 2026-07-12:擇時判死、僅監測) ----------
margin_state = None
margin_detail = 'N/A'
margin_bal_dd = margin_chg63 = margin_px_dd = margin_lvl_pct = None
margin_date = None  # 融資資料日(TWSE 約 21:00 公布,下午班次拿到的是 T-1;22:30 晚班補當日)
try:
    m = fetch_finmind('TaiwanStockTotalMarginPurchaseShortSale', '',
                      (today - pd.Timedelta(days=1150)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    tj = fetch_finmind('TaiwanStockPrice', 'TAIEX',
                       (today - pd.Timedelta(days=1150)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
    if not m.empty and not tj.empty:
        bal_s = m[m['name'] == 'MarginPurchaseMoney'].set_index('date')['TodayBalance'].astype(float)
        bal_s.index = pd.to_datetime(bal_s.index)
        bal_s = bal_s.sort_index()
        px_s = tj.set_index('date')['close'].astype(float)
        px_s.index = pd.to_datetime(px_s.index)
        px_s = px_s.sort_index()
        bal_dd = bal_s.iloc[-1] / bal_s.rolling(252, min_periods=200).max().iloc[-1] - 1
        chg63 = bal_s.iloc[-1] / bal_s.iloc[-64] - 1 if len(bal_s) > 64 else 0.0
        px_dd = px_s.iloc[-1] / px_s.rolling(252, min_periods=200).max().iloc[-1] - 1
        delev = (bal_dd < -0.15) or (chg63 < -0.10)
        if delev and px_dd < -0.10:
            margin_state = 'SPIRAL'
        elif delev:
            margin_state = 'WARNING'
        else:
            margin_state = 'NORMAL'
        margin_lvl_pct = round(float((bal_s.tail(756) <= bal_s.iloc[-1]).mean()), 3)
        margin_detail = f'餘額回撤 {bal_dd:+.1%} 水位 p{margin_lvl_pct:.0%} 63日 {chg63:+.1%} 大盤回撤 {px_dd:+.1%}'
        margin_bal_dd, margin_chg63, margin_px_dd = round(float(bal_dd), 4), round(float(chg63), 4), round(float(px_dd), 4)
        margin_date = str(bal_s.index[-1].date())
except Exception as e:
    margin_detail = f'融資段失敗 {type(e).__name__}'
    log_line(f'WARN:融資 regime 段失敗 {type(e).__name__}(本輪 UNKNOWN)')

# ---------- ④ 韓國融資進度條(KOFIA FreeSIS 日頻;codex 摸通 2026-07-13) ----------
kr_dd = None
kr_level = None
kr_lvl_pct = None
kr_detail = 'N/A'
try:
    payload = {"dmSearch": {"tmpV1": "D", "tmpV40": "01",
                            "tmpV45": (today - pd.Timedelta(days=1150)).strftime('%Y%m%d'),
                            "tmpV46": today.strftime('%Y%m%d'), "OBJ_NM": "STATSCU0100000070BO"}}
    kh = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
          "Content-Type": "application/json; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
          "Origin": "https://freesis.kofia.or.kr",
          "Referer": "https://freesis.kofia.or.kr/stat/FreeSIS.do?parentDivId=MSIS10000000000000&serviceId=STATSCU0100000070"}
    rk = requests.post('https://freesis.kofia.or.kr/meta/getMetaDataList.do', json=payload, headers=kh, timeout=60)
    ds = rk.json().get('ds1', [])
    kr = pd.Series({pd.Timestamp(str(r['TMPV1'])): float(str(r['TMPV2']).replace(',', '')) for r in ds if r.get('TMPV2')}).sort_index()
    if len(kr) > 100:
        kr_dd = float(kr.iloc[-1] / kr.tail(252).max() - 1)
        kr_lvl_pct = round(float((kr.tail(756) <= kr.iloc[-1]).mean()), 3)
        kr_kosdaq = pd.Series({pd.Timestamp(str(r['TMPV1'])): float(str(r['TMPV4']).replace(',', '')) for r in ds if r.get('TMPV4')}).sort_index()
        kq_dd = float(kr_kosdaq.iloc[-1] / kr_kosdaq.tail(252).max() - 1)
        kr_detail = f'合計回撤 {kr_dd:+.1%} 水位 p{kr_lvl_pct:.0%}(KOSDAQ 段 {kq_dd:+.1%})最新 {kr.iloc[-1]/1e6/1e6:.1f} 兆'
        prev_lv = (state.get('last', {}) or {}).get('kr_level', 0)
        lv = 2 if kr_dd < -0.20 else (1 if kr_dd < -0.10 else 0)
        if lv > prev_lv:
            notify('🇰🇷 韓國融資去槓桿跨關卡', f'{"回撤破 -20%" if lv==2 else "回撤破 -10%"}:{kr_detail}')
        kr_level = lv
except Exception as e:
    kr_detail = f'KOFIA 段失敗 {type(e).__name__}'
    log_line(f'WARN:KOFIA 段失敗 {type(e).__name__}(本輪 UNKNOWN)')

# ---------- ⑤ 日本槓桿+USDJPY(JPX 週頻+FRED/Yahoo;2026-07-13 翔核准;context 非訊號、關卡未回測) ----------
jp_hist = dict(state.get('jp_hist', {})) if isinstance(state, dict) else {}
jp_dd = fx_dd = None
jp_level = fx_level = None
jp_lvl_pct = None
jp_detail = fx_detail = 'N/A'
try:
    hp = requests.get('https://www.jpx.co.jp/markets/statistics-equities/margin/04.html',
                      headers=UA, timeout=60)
    for path, d8 in re.findall(r'href="(/markets/[^"]+?mtseisan(\d{8})00\.xls)"', hp.text):
        key = f'{d8[:4]}-{d8[4:6]}-{d8[6:]}'
        if key in jp_hist:
            continue
        rx = requests.get(f'https://www.jpx.co.jp{path}', headers=UA, timeout=60)
        dfj = pd.read_excel(io.BytesIO(rx.content), sheet_name=0, header=None)
        idx = dfj.index[dfj.apply(lambda r: r.astype(str).str.contains('二市場計').any(), axis=1)][0]
        vals = pd.to_numeric(dfj.iloc[idx + 1], errors='coerce').dropna().tolist()
        buy = float(vals[2])  # 金額列:委託売残,前週比,委託買残(百万円),...
        if buy < 1e6:
            raise ValueError(f'JPX 委託買残解析異常 {key}={buy}')
        jp_hist[key] = buy
    jp_hist = dict(sorted(jp_hist.items())[-400:])
    if jp_hist:
        ks = sorted(jp_hist)
        jp_last = jp_hist[ks[-1]]
        jp_dd = float(jp_last / max(jp_hist[k] for k in ks[-52:]) - 1)
        # 水位分位:JPX 過去推移表(制度+一般合計,近 156 週)+ live 尾端;rank 對 ~0.1% 口徑縫不敏感
        jp_lvl_pct = None
        try:
            hp6 = requests.get('https://www.jpx.co.jp/markets/statistics-equities/margin/06.html',
                               headers=UA, timeout=60)
            arch = []
            for path6 in re.findall(r'href="(/markets/[^"]+?\.xls)"', hp6.text):
                rx6 = requests.get(f'https://www.jpx.co.jp{path6}', headers=UA, timeout=60)
                dfa = pd.read_excel(io.BytesIO(rx6.content), sheet_name=0, header=None)[[0, 4]]
                dfa.columns = ['d', 'buy']
                dfa['d'] = pd.to_datetime(dfa['d'], errors='coerce')
                dfa['buy'] = pd.to_numeric(dfa['buy'], errors='coerce')
                arch.append(dfa.dropna())
            jpa = pd.concat(arch).drop_duplicates('d').set_index('d').sort_index()['buy']
            w = list(jpa.tail(156)) + [jp_last]
            jp_lvl_pct = round(float((pd.Series(w) <= jp_last).mean()), 3)
        except Exception as e6:
            log_line(f'WARN:JPX 推移表水位失敗 {type(e6).__name__}(jp 水位本輪 UNKNOWN)')
        jp_detail = (f'買い残 {jp_last/1e6:.2f} 兆円({ks[-1]})回撤 {jp_dd:+.1%}'
                     + (f' 水位 p{jp_lvl_pct:.0%}' if jp_lvl_pct is not None else ''))
        jp_level = 2 if jp_dd < -0.20 else (1 if jp_dd < -0.10 else 0)
        if jp_level > (prev.get('jp_level') or 0):
            notify('🇯🇵 日本信用買い残跨關卡', f'{"回撤破 -20%" if jp_level==2 else "回撤破 -10%"}:{jp_detail}')
except Exception as e:
    log_line(f'WARN:JPX 段失敗 {type(e).__name__}(本輪 UNKNOWN)')
try:
    fx = None
    for fn in (fetch_fx_yahoo, fetch_fx_fred):  # Yahoo 即時為主,FRED(lag 數日)fallback;2026-07-13 Actions 實測 Yahoo 通
        try:
            fx = fn()
            if len(fx) > 63:
                break
        except Exception:
            fx = None
    if fx is None or len(fx) <= 63:
        raise ValueError('兩源皆失敗')
    fx_dd = float(fx.iloc[-1] / fx.tail(63).max() - 1)
    fx_detail = f'USDJPY {fx.iloc[-1]:.1f}({fx.index[-1].date()})距63日高 {fx_dd:+.1%}'
    fx_level = 2 if fx_dd < -0.06 else (1 if fx_dd < -0.03 else 0)
    if fx_level > (prev.get('fx_level') or 0):
        notify('🇯🇵 円 carry unwind 警訊', f'{"破 -6%" if fx_level==2 else "破 -3%"}:{fx_detail}(描述性關卡未回測)')
except Exception as e:
    log_line(f'WARN:USDJPY 段失敗 {type(e).__name__}(本輪 UNKNOWN)')

# ---------- 狀態更新與通知(翻轉才響) ----------
rec = dict(date=str((T or today).date()), cost=cost and round(cost, 1), cost_bp=cost_bp,
           contract=contract, vix_p=round(vix_p, 3) if vix_p == vix_p else None,
           ratio_p=round(ratio_p, 3) if ratio_p == ratio_p else None, G2=g2, G3=g3, vix_date=vix_date,
           margin=margin_state, margin_bal_dd=margin_bal_dd, margin_chg63=margin_chg63,
           margin_px_dd=margin_px_dd, margin_lvl_pct=margin_lvl_pct, margin_date=margin_date,
           kr_dd=round(kr_dd, 4) if kr_dd is not None else None,
           kr_level=kr_level, kr_lvl_pct=kr_lvl_pct,
           jp_dd=round(jp_dd, 4) if jp_dd is not None else None, jp_level=jp_level,
           jp_lvl_pct=jp_lvl_pct,
           fx_dd=round(fx_dd, 4) if fx_dd is not None else None, fx_level=fx_level)
hist = [h for h in hist if h.get('date') != rec['date']]
hist = sorted(hist + [rec], key=lambda h: h['date'])[-120:]

prev_g2, prev_g3 = prev.get('G2'), prev.get('G3')
cost_str = f'{cost:.0f} 點({cost_bp}bp)' if cost is not None else 'N/A'
if g2 is not None:
    if g3 and not prev_g3:
        notify('🔴 G3 確認壓力(TXO 保險)', f'VIX p{vix_p:.0%}/ratio p{ratio_p:.0%}。保費 {cost_str}。')
    elif g2 and not prev_g2:
        notify('🟡 G2 壓力開關亮(TXO 保險)', f'VIX p{vix_p:.0%}/ratio p{ratio_p:.0%}。保險窗口開啟(探索性),保費 {cost_str}。')
    elif prev_g2 and not g2:
        notify('G2 熄燈', f'壓力解除。保費 {cost_str}。')

prev_m = prev.get('margin')
if margin_state and prev_m and margin_state != prev_m:
    icon = {'SPIRAL': '🔴', 'WARNING': '🟠', 'NORMAL': '🟢'}[margin_state]
    notify(f'{icon} 融資 regime:{prev_m}→{margin_state}', margin_detail)

recent = [h['cost_bp'] for h in hist[-ARM_DAYS:] if h.get('cost_bp') is not None]
slow_armed = len(recent) >= ARM_DAYS and all(b <= ARM_BP for b in recent)
if slow_armed and not prev.get('slow_armed'):
    notify('🔔 保費慢開關上膛', f'連 {ARM_DAYS} 交易日 <= {ARM_BP}bp。見登記簿 spike-exit 案。')

STATE.write_text(json.dumps(dict(history=hist, last=dict(rec, slow_armed=slow_armed), jp_hist=jp_hist),
                            ensure_ascii=False, indent=1))
log_line(f"{rec['date']} {contract or '-'} cost={cost_str} | G2={g2} G3={g3} "
         f"(vixp={vix_p:.2f} ratiop={ratio_p:.2f}) slow_armed={slow_armed} | margin={margin_state}({margin_detail}) | KR={kr_detail} | JP={jp_detail} {fx_detail}")

# ---------- README 儀表(GitHub 首頁即儀表) ----------
def lamp(cond_bad, cond_warn):
    return '🔴' if cond_bad else ('🟡' if cond_warn else '🟢')

readme = f"""# RUNiC Monitor(五面旗,每交易日 ~15:00 台北自動更新)

**📊 儀表板:https://minaseshou.github.io/runic-monitor/**

**最後更新:{datetime.now().strftime('%Y-%m-%d %H:%M')} {'UTC' if ON_GITHUB else '台北'}|資料日 {rec['date']}**

| 旗 | 狀態 | 讀值 |
|---|---|---|
| ① G2 壓力開關 | {lamp(g3, g2)} {'G3' if g3 else ('G2 ON' if g2 else 'OFF') if g2 is not None else 'UNKNOWN'} | VIX p{vix_p:.0%} / ratio p{ratio_p:.0%}({vix_date or 'N/A'}) |
| ② TXO 保費慢開關 | {'🔔 上膛' if slow_armed else '🟢 未上膛'} | {cost_str},門檻=連 {ARM_DAYS} 日 ≤{ARM_BP:.0f}bp |
| ③ 台灣融資 regime | {lamp(margin_state == 'SPIRAL', margin_state == 'WARNING')} {margin_state or 'UNKNOWN'} | {margin_detail} |
| ④ 韓國融資進度條 | {lamp(kr_level == 2, kr_level == 1) if kr_level is not None else '⚪'} lv{kr_level if kr_level is not None else '?'} | {kr_detail} |
| ⑤ 日本槓桿+円 | {lamp((jp_level or 0) == 2 or (fx_level or 0) == 2, (jp_level or 0) == 1 or (fx_level or 0) == 1)} lv{jp_level if jp_level is not None else '?'}/{fx_level if fx_level is not None else '?'} | {jp_detail};{fx_detail} |

口徑:回撤=距 252 觀測日(週頻=52 週)內高點(無前視);水位=近 3 年分佈百分位。
跨關卡/翻轉時自動開 Issue(=手機推播)。全歷史見 [log.md](log.md) 與 git 歷史。
定位=context 非訊號(「市場層風險訊號永不疊加本書」鐵律);雙軌之雲端軌,Mac launchd 為備援。
"""
(HERE / 'README.md').write_text(readme)
