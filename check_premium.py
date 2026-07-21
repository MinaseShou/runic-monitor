#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TXO 保險+市場監測 v5——GitHub Actions 版(雙軌之雲端軌;Mac 正本=~/RUNiC_LOCAL/txo/monitor/check_premium.py)
與 Mac 版差異僅三點:①通知=開 GitHub Issue(本機 fallback=osascript)②USDJPY 加 FRED fallback ③每輪重寫 README 儀表。
七面旗:
① G2 壓力開關(VIX p252>=70% 或 VVIX/VIX ratio p252<=20%;翻轉時通知)= 事故前上膛候選(探索性,2026-07-12 radar 相關性分析)
② 保費慢開關(cost_bp 連 20 個交易日 <=10bp)= 年代級可負擔
③ 融資 regime(NORMAL/WARNING/SPIRAL;韓式螺旋判別,margin_regime 案:擇時判死僅監測)
   +上櫃/含櫃雙口徑並列(2026-07-17 翔核准;TPEx 官方源+state 快取增量;regime 判別維持上市口徑,含櫃=同門檻 shadow、翻轉通知)
④ 韓國融資進度條(KOFIA 日頻;回撤跨 -10%/-20% 通知)
⑤ 日本槓桿+USDJPY(JPX 週頻 mtseisan 委託買い残+FRED 匯率;回撤跨 -10%/-20%、円距 63 日高跨 -3%/-6% 通知;純描述未回測)
⑥ 台指 RV 本土壓力旗(20日RV+單日range 百分位;lv1=RV p85 或 range p95、lv2=RV p95 或 range p99;升級通知;2026-07-17 翔核准,關卡未回測)
⑦ VXN 科技 vol(CBOE;p252>=70% 亮一次;G2 美系口徑對亞洲/半導體去槓桿盲區補丁——2026-07-17 N=3 首 miss 案)
⑧ 燃料旗(2026-07-21 翔核准;崩盤研究 Phase1/3:量能 20 日均 p756>=0.90 且融資 20 日 >=+5%=高燃料;
   歷史佔時 ~11%、日層級 60 日內遇快崩率 46% vs base 27%=描述非預測;門檻 in-sample)
⑨ 湍流旗(同案;TAIEX 60 日年化波動 p756>=0.90=顛簸;崩盤首腿峰前湍流 AUC 0.658/0.778 雙對照存活;
   「會崩的頂是顛簸的頂」;2020 COVID 型外生零前兆=兩軸共同盲區)
口徑(2026-07-14 翔核定統一):「回撤」=距 252 觀測日(週頻=52 週)內高點,逐日僅用當日已知資訊、無前視;
「水位」=現值在近 3 年(756 觀測)分佈的百分位——回撤答「跌多少」、水位答「堆多高」,兩面並列。
資料:FinMind 免 token(TXO+TX+TAIEX)+CBOE 官方 CSV(VIX/VVIX/VXN)+TPEx 官方+KOFIA+JPX+FRED/Yahoo;fail=靜默 skip 留 log"""
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

# ---------- ① G2/G3 壓力開關(CBOE)+⑦ VXN ----------
vix = fetch_cboe('VIX')
vvix = fetch_cboe('VVIX')
vxn = fetch_cboe('VXN')
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

vxn_p = float('nan')
vxn_hi = None
vxn_detail = 'N/A'
if len(vxn):
    wv = vxn.tail(252)
    vxn_p = float((wv <= wv.iloc[-1]).mean())
    vxn_hi = bool(vxn_p >= 0.70)
    vxn_detail = f'VXN {float(vxn.iloc[-1]):.1f} p{vxn_p:.0%}({vxn.index[-1].date()})'
else:
    log_line('WARN:VXN 抓取失敗(本輪 UNKNOWN)')

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
tj = fetch_finmind('TaiwanStockPrice', 'TAIEX',
                   (today - pd.Timedelta(days=1400)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))  # 1400=供 ⑨ vol60 的 756 日分位
try:
    m = fetch_finmind('TaiwanStockTotalMarginPurchaseShortSale', '',
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

# ---------- ③b 上櫃/含櫃雙口徑(TPEx 官方;FinMind 無上櫃 total;2026-07-17 翔核准並列) ----------
tpex_hist = dict(state.get('tpex_hist', {})) if isinstance(state, dict) else {}
tp_bal_dd = tp_chg63 = tp_lvl_pct = tp_px_dd = None
margin_all = None
all_bal_dd = all_chg63 = None
tp_detail = 'N/A'
try:
    need = [d for d in pd.bdate_range(end=today, periods=10)
            if d.strftime('%Y-%m-%d') not in tpex_hist]
    for d in need:
        rt = requests.get('https://www.tpex.org.tw/www/zh-tw/margin/balance',
                          params={'date': d.strftime('%Y/%m/%d'), 'response': 'json'},
                          headers=UA, timeout=30)
        ts_ = rt.json().get('tables', [])
        summ = ts_[0].get('summary', []) if ts_ else []
        fin = [row for row in summ if any('融資金' in str(c) for c in row)]
        if fin and str(fin[0][6]).strip():  # 非交易日無融資金列,自然 skip(假日每輪重試,10 次×1s 可忽略)
            tpex_hist[d.strftime('%Y-%m-%d')] = int(str(fin[0][6]).replace(',', '')) * 1000  # 仟元→元
        time.sleep(1)
    tpex_hist = dict(sorted(tpex_hist.items())[-1200:])
    tp_s = pd.Series(tpex_hist, dtype=float)
    tp_s.index = pd.to_datetime(tp_s.index)
    tp_s = tp_s.sort_index()
    if len(tp_s) > 260:
        tp_bal_dd = round(float(tp_s.iloc[-1] / tp_s.rolling(252, min_periods=200).max().iloc[-1] - 1), 4)
        tp_chg63 = round(float(tp_s.iloc[-1] / tp_s.iloc[-64] - 1), 4)
        tp_lvl_pct = round(float((tp_s.tail(756) <= tp_s.iloc[-1]).mean()), 3)
        tp_detail = f'上櫃回撤 {tp_bal_dd:+.1%} 水位 p{tp_lvl_pct:.0%} 63日 {tp_chg63:+.1%}'
        try:  # 櫃買指數回撤(與 ③ 大盤回撤同口徑;顯示用,不入判別——含櫃 SPIRAL 價格條件維持大盤)
            tpi = fetch_finmind('TaiwanStockPrice', 'TPEx',
                                (today - pd.Timedelta(days=1150)).strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d'))
            tpi_s = tpi.set_index('date')['close'].astype(float)
            tpi_s.index = pd.to_datetime(tpi_s.index)
            tpi_s = tpi_s.sort_index()
            tp_px_dd = round(float(tpi_s.iloc[-1] / tpi_s.rolling(252, min_periods=200).max().iloc[-1] - 1), 4)
            tp_detail += f' 櫃買回撤 {tp_px_dd:+.1%}'
        except Exception as e2:
            log_line(f'WARN:櫃買指數段失敗 {type(e2).__name__}(本輪 UNKNOWN)')
        if margin_state:  # 上市段成功才有 bal_s/margin_px_dd;含櫃 px 條件沿用大盤(加權為主體)
            allb = (bal_s + tp_s).dropna()
            if len(allb) > 260:
                all_bal_dd = round(float(allb.iloc[-1] / allb.rolling(252, min_periods=200).max().iloc[-1] - 1), 4)
                all_chg63 = round(float(allb.iloc[-1] / allb.iloc[-64] - 1), 4)
                delev_a = (all_bal_dd < -0.15) or (all_chg63 < -0.10)
                margin_all = 'SPIRAL' if (delev_a and margin_px_dd < -0.10) else ('WARNING' if delev_a else 'NORMAL')
                tp_detail += f';含櫃回撤 {all_bal_dd:+.1%} 63日 {all_chg63:+.1%}={margin_all}'
    else:
        tp_detail = f'快取 {len(tp_s)} 日未達 260,待回補'
except Exception as e:
    tp_detail = f'TPEx 段失敗 {type(e).__name__}'
    log_line(f'WARN:TPEx 上櫃融資段失敗 {type(e).__name__}(本輪 UNKNOWN)')

# ---------- ③c 全市場融資維持率 proxy(2026-07-21 翔核准;抄底條件統計案:雷瓦汀 reports 同日) ----------
# 口徑=Σ(個股融資餘額張×1000×收盤價)/(上市+上櫃融資金額);官方四端點;與 FinLab 研究口徑逐位對帳通過。
# 統計線(認知資產非訊號):下穿160 無資訊/150 有肉/140 肥區(+120日 +20.0%/勝率91%,n=11);
# 回升上穿150(曾<145)=斷頭潮尾聲確認式(+20日勝率83%);載體=距 130% 制度追繳線的絕對緩衝,非歷史分位。
maint_hist = dict(state.get('maint_hist', {})) if isinstance(state, dict) else {}
maint = None
maint_detail = 'N/A'
try:
    if margin_date:
        D = pd.Timestamp(margin_date)
        key = D.strftime('%Y-%m-%d')
        if key not in maint_hist:
            def _num(s):
                s = str(s).replace(',', '').strip()
                try:
                    return float(s)
                except ValueError:
                    return None
            jm = requests.get('https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN',
                              params={'date': D.strftime('%Y%m%d'), 'selectType': 'ALL', 'response': 'json'},
                              headers=UA, timeout=60).json()
            agg_ = {r[0]: r for r in jm['tables'][0]['data']}
            dsii = _num(agg_['融資金額(仟元)'][5]) * 1000
            mrows = {r[0].strip(): _num(r[6]) for r in jm['tables'][1]['data']}
            ji = requests.get('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX',
                              params={'date': D.strftime('%Y%m%d'), 'type': 'ALLBUT0999', 'response': 'json'},
                              headers=UA, timeout=120).json()
            ctab = [t for t in ji['tables'] if '每日收盤行情' in t.get('title', '')][0]
            csii = {r[0].strip(): _num(r[8]) for r in ctab['data']}
            jt = requests.get('https://www.tpex.org.tw/www/zh-tw/margin/balance',
                              params={'date': D.strftime('%Y/%m/%d'), 'response': 'json'},
                              headers=UA, timeout=30).json()
            t3_ = jt['tables'][0]
            summ3 = [row for row in t3_['summary'] if any('融資金' in str(c) for c in row)]
            dotc = _num(summ3[0][6]) * 1000
            orow = {r[0].strip(): _num(r[6]) for r in t3_['data']}
            jq = requests.get('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes',
                              params={'date': D.strftime('%Y/%m/%d'), 'response': 'json'},
                              headers=UA, timeout=60).json()
            cotc = {r[0].strip(): _num(r[2]) for r in jq['tables'][0]['data']}
            mv_ = (sum(sh * 1000 * csii[c] for c, sh in mrows.items() if sh and csii.get(c))
                   + sum(sh * 1000 * cotc[c] for c, sh in orow.items() if sh and cotc.get(c)))
            maint_hist[key] = round(mv_ / (dsii + dotc) * 100, 2)
        maint_hist = dict(sorted(maint_hist.items())[-1200:])
        maint = maint_hist.get(D.strftime('%Y-%m-%d'))
        if maint is not None:
            maint_detail = f'全市場維持率 {maint:.1f}%({D.strftime("%Y-%m-%d")})'
except Exception as e:
    log_line(f'WARN:維持率段失敗 {type(e).__name__}(本輪 UNKNOWN)')

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

# ---------- ⑥ 台指 RV 本土壓力旗(TAIEX 日線;2026-07-17 翔核准;G2 亞洲盲區補丁,關卡未回測、context 非訊號) ----------
rv20 = rv_pct = range_pct = None
tw_stress = None
rv_detail = 'N/A'
try:
    if not tj.empty:
        pxd = tj.set_index('date')[['max', 'min', 'close']].astype(float)
        pxd.index = pd.to_datetime(pxd.index)
        pxd = pxd.sort_index()
        rv_s = pxd['close'].pct_change().rolling(20).std() * (252 ** 0.5)
        rng_s = (pxd['max'] - pxd['min']) / pxd['close'].shift(1)
        rv20 = round(float(rv_s.iloc[-1]), 4)
        rv_pct = round(float((rv_s.dropna().tail(756) <= rv_s.iloc[-1]).mean()), 3)
        range_pct = round(float((rng_s.dropna().tail(756) <= rng_s.iloc[-1]).mean()), 3)
        tw_stress = 2 if (rv_pct >= 0.95 or range_pct >= 0.99) else (1 if (rv_pct >= 0.85 or range_pct >= 0.95) else 0)
        rv_detail = f'20日RV {rv20:.0%} p{rv_pct:.0%};單日range p{range_pct:.0%}({pxd.index[-1].date()})'
except Exception as e:
    rv_detail = f'RV 段失敗 {type(e).__name__}'
    log_line(f'WARN:台指 RV 段失敗 {type(e).__name__}(本輪 UNKNOWN)')

# ---------- ⑧⑨ 崩盤雙軸:燃料+湍流(2026-07-21 翔核准;崩盤研究 Phase1/3 蛛絲馬跡;描述卡非預測器) ----------
fuel_turn_pct = fuel_mg20 = fuel_lag = None
fuel_on = None
turb_vol60 = turb_vol60_pct = turb_count60 = None
turb_on = None
fuel_detail = turb_detail = 'N/A'
try:
    if not tj.empty:
        pxc = tj.set_index('date')['close'].astype(float)
        pxc.index = pd.to_datetime(pxc.index)
        pxc = pxc.sort_index()
        mny = tj.set_index('date')['Trading_money'].astype(float)
        mny.index = pd.to_datetime(mny.index)
        mny = mny.sort_index()
        t20 = mny.rolling(20).mean().dropna()
        fuel_turn_pct = round(float((t20.tail(756) <= t20.iloc[-1]).mean()), 3)
        r_s = pxc.pct_change()
        v60 = (r_s.rolling(60).std() * (252 ** 0.5)).dropna()
        turb_vol60 = round(float(v60.iloc[-1]), 4)
        turb_vol60_pct = round(float((v60.tail(756) <= v60.iloc[-1]).mean()), 3)
        turb_count60 = int((r_s.tail(60) <= -0.02).sum())
        turb_on = bool(turb_vol60_pct >= 0.90)
        turb_detail = f'vol60 {turb_vol60:.0%} p{turb_vol60_pct:.0%};60日內≥2%下跳 {turb_count60} 次'
        if margin_state and len(bal_s) > 121:  # ③ 段成功才有 bal_s
            fuel_mg20 = round(float(bal_s.iloc[-1] / bal_s.iloc[-21] - 1), 4)
            w121 = bal_s.tail(121).reset_index(drop=True)
            fuel_lag = int(len(w121) - 1 - int(w121.idxmax()))
            fuel_on = bool(fuel_turn_pct >= 0.90 and fuel_mg20 >= 0.05)
            fuel_detail = f'量能 p{fuel_turn_pct:.0%};融資20日 {fuel_mg20:+.1%};融資峰距今 {fuel_lag} 日'
        else:
            fuel_detail = f'量能 p{fuel_turn_pct:.0%};融資段 UNKNOWN'
except Exception as e:
    log_line(f'WARN:崩盤雙軸段失敗 {type(e).__name__}(本輪 UNKNOWN)')

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
           fx_dd=round(fx_dd, 4) if fx_dd is not None else None, fx_level=fx_level,
           vxn_p=round(vxn_p, 3) if vxn_p == vxn_p else None, vxn_hi=vxn_hi,
           vxn=round(float(vxn.iloc[-1]), 2) if len(vxn) else None,
           tp_bal_dd=tp_bal_dd, tp_chg63=tp_chg63, tp_lvl_pct=tp_lvl_pct, tp_px_dd=tp_px_dd,
           margin_all=margin_all, all_bal_dd=all_bal_dd, all_chg63=all_chg63,
           rv20=rv20, rv_pct=rv_pct, range_pct=range_pct, tw_stress=tw_stress,
           fuel_turn_pct=fuel_turn_pct, fuel_mg20=fuel_mg20, fuel_lag=fuel_lag, fuel_on=fuel_on,
           vol60=turb_vol60, vol60_pct=turb_vol60_pct, count60=turb_count60, turb_on=turb_on,
           maint=maint)
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

prev_ma = prev.get('margin_all')
if margin_all and prev_ma and margin_all != prev_ma:
    icon = {'SPIRAL': '🔴', 'WARNING': '🟠', 'NORMAL': '🟢'}[margin_all]
    notify(f'{icon} 含櫃融資 regime:{prev_ma}→{margin_all}', tp_detail)

if vxn_hi and not prev.get('vxn_hi'):
    notify('🟡 VXN 科技 vol 亮(G2 美系盲區補丁)', f'{vxn_detail};G2={g2}。亞洲/半導體系壓力可能先現於此(N=3 首 miss 案)。')

if tw_stress is not None and tw_stress > (prev.get('tw_stress') or 0):
    notify('🟠 台指 RV 本土壓力旗升級', f'lv{tw_stress}:{rv_detail}(G2 亞洲盲區補丁;關卡未回測)')

if fuel_on is not None and prev.get('fuel_on') is not None and fuel_on != prev.get('fuel_on'):
    notify('🟠 燃料旗亮(崩盤雙軸)' if fuel_on else '🟢 燃料旗熄(崩盤雙軸)',
           (f'進入高燃料狀態:{fuel_detail}。歷史佔時 ~11%、60日內遇快崩率 46% vs base 27%——脆弱度描述非訊號。'
            if fuel_on else f'退出高燃料狀態:{fuel_detail}。'))

if turb_on is not None and prev.get('turb_on') is not None and turb_on != prev.get('turb_on'):
    notify('🟠 湍流旗亮(崩盤雙軸)' if turb_on else '🟢 湍流旗熄(崩盤雙軸)',
           f'{turb_detail}。「會崩的頂是顛簸的頂」(vol60 p≥90;描述卡非訊號)。')

prev_maint = prev.get('maint')
if maint is not None and prev_maint is not None:
    _stats = {160: '+120日 +4.1%/勝率68%(淺,無資訊)', 150: '+120日 +10.5%/勝率73%', 140: '+120日 +20.0%/勝率91%(n=11,肥區)'}
    for th_, icon_ in ((160, '🟡'), (150, '🟠'), (140, '🔴')):
        if maint < th_ <= prev_maint:
            notify(f'{icon_} 維持率下穿 {th_}(抄底統計線)',
                   f'{maint_detail};歷史同觸發 TAIEX {_stats[th_]}——認知資產非訊號,配崩型讀(陰跌型平庸)。')
    _ms = pd.Series(maint_hist).astype(float)
    if maint > 150 >= prev_maint and len(_ms) > 21 and (_ms.tail(21).iloc[:-1] < 145).any():
        notify('🟢 維持率回升上穿 150(斷頭潮尾聲確認式)',
               f'{maint_detail};歷史此式 +20日 +4.1%/勝率83%——認知資產非訊號。')

recent = [h['cost_bp'] for h in hist[-ARM_DAYS:] if h.get('cost_bp') is not None]
slow_armed = len(recent) >= ARM_DAYS and all(b <= ARM_BP for b in recent)
if slow_armed and not prev.get('slow_armed'):
    notify('🔔 保費慢開關上膛', f'連 {ARM_DAYS} 交易日 <= {ARM_BP}bp。見登記簿 spike-exit 案。')

STATE.write_text(json.dumps(dict(history=hist, last=dict(rec, slow_armed=slow_armed), jp_hist=jp_hist,
                                 tpex_hist=tpex_hist, maint_hist=maint_hist),
                            ensure_ascii=False, indent=1))
log_line(f"{rec['date']} {contract or '-'} cost={cost_str} | G2={g2} G3={g3} "
         f"(vixp={vix_p:.2f} ratiop={ratio_p:.2f}) slow_armed={slow_armed} | margin={margin_state}({margin_detail}) "
         f"| TPEX={tp_detail} | KR={kr_detail} | JP={jp_detail} {fx_detail} | RV={rv_detail} lv{tw_stress} | {vxn_detail} hi={vxn_hi} "
         f"| 燃料={fuel_on}({fuel_detail}) 湍流={turb_on}({turb_detail})")

# ---------- README 儀表(GitHub 首頁即儀表) ----------
def lamp(cond_bad, cond_warn):
    return '🔴' if cond_bad else ('🟡' if cond_warn else '🟢')

readme = f"""# RUNiC Monitor(九面旗,每交易日 ~15:00 台北自動更新)

**📊 儀表板:https://minaseshou.github.io/runic-monitor/**

**最後更新:{datetime.now().strftime('%Y-%m-%d %H:%M')} {'UTC' if ON_GITHUB else '台北'}|資料日 {rec['date']}**

| 旗 | 狀態 | 讀值 |
|---|---|---|
| ① G2 壓力開關 | {lamp(g3, g2)} {'G3' if g3 else ('G2 ON' if g2 else 'OFF') if g2 is not None else 'UNKNOWN'} | VIX p{vix_p:.0%} / ratio p{ratio_p:.0%}({vix_date or 'N/A'}) |
| ② TXO 保費慢開關 | {'🔔 上膛' if slow_armed else '🟢 未上膛'} | {cost_str},門檻=連 {ARM_DAYS} 日 ≤{ARM_BP:.0f}bp |
| ③ 台灣融資 regime | {lamp(margin_state == 'SPIRAL', margin_state == 'WARNING')} {margin_state or 'UNKNOWN'} | 上市:{margin_detail} |
| ③b 上櫃/含櫃並列 | {lamp(margin_all == 'SPIRAL', margin_all == 'WARNING') if margin_all else '⚪'} {margin_all or 'N/A'} | {tp_detail} |
| ③c 全市場維持率 | {('🔴' if maint < 140 else '🟠' if maint < 150 else '🟡' if maint < 160 else '🟢') if maint is not None else '⚪'} {f'{maint:.1f}%' if maint is not None else 'UNKNOWN'} | {maint_detail},統計線 160/150/140、回升上穿150=尾聲確認 |
| ④ 韓國融資進度條 | {lamp(kr_level == 2, kr_level == 1) if kr_level is not None else '⚪'} lv{kr_level if kr_level is not None else '?'} | {kr_detail} |
| ⑤ 日本槓桿+円 | {lamp((jp_level or 0) == 2 or (fx_level or 0) == 2, (jp_level or 0) == 1 or (fx_level or 0) == 1)} lv{jp_level if jp_level is not None else '?'}/{fx_level if fx_level is not None else '?'} | {jp_detail};{fx_detail} |
| ⑥ 台指 RV 壓力旗 | {lamp(tw_stress == 2, tw_stress == 1) if tw_stress is not None else '⚪'} lv{tw_stress if tw_stress is not None else '?'} | {rv_detail} |
| ⑦ VXN 科技 vol | {'🟡 ON' if vxn_hi else ('🟢 OFF' if vxn_hi is not None else '⚪ UNKNOWN')} | {vxn_detail},門檻 p252≥70% |
| ⑧ 燃料旗(崩盤雙軸) | {'🟠 ON' if fuel_on else ('🟢 OFF' if fuel_on is not None else '⚪ UNKNOWN')} | {fuel_detail},門檻=量能 p≥90 且融資20日≥+5% |
| ⑨ 湍流旗(崩盤雙軸) | {'🟠 ON' if turb_on else ('🟢 OFF' if turb_on is not None else '⚪ UNKNOWN')} | {turb_detail},門檻=vol60 p≥90 |

口徑:回撤=距 252 觀測日(週頻=52 週)內高點(無前視);水位=近 3 年分佈百分位。
③b/⑥/⑦ 為 2026-07-17 新增(G2 美系口徑盲區補丁);regime 判別維持上市口徑,含櫃=同門檻 shadow;⑥⑦ 關卡未回測。
⑧⑨ 為 2026-07-21 新增(崩盤研究 Phase 1/3 雙軸蛛絲馬跡):燃料=投機槓桿加到頂(高燃料時 60 日內遇快崩率 46% vs base 27%)、湍流=顛簸的頂才崩;皆為描述非預測,外生零前兆型(2020 COVID 式)是共同盲區,解讀詳儀表頁。
跨關卡/翻轉時自動開 Issue(=手機推播)。全歷史見 [log.md](log.md) 與 git 歷史。
定位=context 非訊號(「市場層風險訊號永不疊加本書」鐵律);雙軌之雲端軌,Mac launchd 為備援。
"""
(HERE / 'README.md').write_text(readme)
