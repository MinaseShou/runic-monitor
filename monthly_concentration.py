#!/usr/bin/env python3
"""⑧燃料旗 — 集中度維度(月頻預算器,2026-07-28 翔核准)

【為什麼要這個】
燃料旗原本兩維(量能 p756、融資 20 日變化)描述的是「有多少錢在燒」,
但 2026 這波的特徵是「錢集中燒在哪」——融資分位 5 月就滿了(1.000),
真正異常的是動能最強組的季漲幅中位堆到 +101.78%、前三大產業佔比 63.2%(皆史上 100 分位)。
本檔補上這塊,讓燃料卡從「量」擴到「量 × 集中度」。

【🔴 定位:描述卡,不是預測器,不可用於調整曝險】
- 實測(2026-07-28,75 個月):11 項過熱指標對「次月動能反轉」全部不顯著;
  且就算準確預知反轉,該資訊不可交易(反轉 L/S 毛報酬 -1.70%/月、七年虧六年)。
- 受「市場層風險訊號永不疊加本書」鐵律約束(0 勝 5 敗:vol gate/涅濾網/塔門/融資 regime/崩盤 P2)。
- 唯一正當用途:**事後歸因**——分辨一段回撤是「投機部位清算」還是「外生衝擊」。
  2026-07(Q5 季漲 +101.78%)與 2020-03(零前兆外生)是完全不同的東西。

【口徑】
- 母體:近 60 日均成交額 >= 3,000 萬(剔雞蛋水餃股噪音)
- 動能:月底往前 3 個月的還原報酬(etl:adj_close),分 5 分位,Q5 = 最強組
- 指標:Q5 季漲中位、Q5 產業 HHI、Q5 前三產業佔比;分位 = 在全部歷史月份中的百分位
- 亮燈:季漲中位分位 >= 0.90 **且** 產業 HHI 分位 >= 0.90(2026-05/06/07 皆亮)

【執行】月頻即可(每月底跑一次),輸出 concentration.json 供 check_premium.py 讀取。
       Mac 跑(需 FinLab);monitor 端只讀 json,無 FinLab 依賴。
"""
import json, os, pathlib, sys
import pandas as pd, numpy as np
import finlab
from finlab import data

OUT = pathlib.Path(__file__).parent / 'concentration.json'
LIQ_MIN, NQ = 3e7, 5
# 口徑版本:任何會改變讀值語意的變更(門檻/分位數/動能窗/母體定義)都要 +1,
# 讀取端(check_premium.py ⑧b)只認 SPEC_VERSION_OK 清單,不認得就標 UNKNOWN 而非照舊解讀。
# 目的:擋掉「口徑改了但顯示端沒跟著改」的靜默失敗(= 2026-07-28 涅 FAST bug 的同型風險)。
SPEC_VERSION = 1

# 登入:優先用新系統的快取憑證(`python -m finlab login` 寫在 ~/.finlab/credentials.json)。
# 🔴 launchd 陷阱:無憑證時 finlab.login() 會退回 input() → 無 TTY 下 EOFError,所以要 catch。
# 🔴 2026-08-01 後 finlab.login(token) 將被移除(DeprecationWarning),故 token 只當 fallback;
#    屆時若快取憑證也失效,排程會 fail 並發 Basso 通知,需人工跑一次 `python -m finlab login`。
try:
    finlab.login()
except Exception as _e:
    _tok = os.environ.get('FINLAB_API_TOKEN', '').strip()
    if not _tok:
        raise RuntimeError(f'finlab 登入失敗且無 FINLAB_API_TOKEN fallback:{type(_e).__name__}') from _e
    print(f'[WARN] 快取憑證登入失敗({type(_e).__name__}),改用 token fallback(2026-08-01 後將失效)')
    finlab.login(_tok)
adj = pd.DataFrame(data.get('etl:adj_close')); adj.index = pd.to_datetime(adj.index)
amt = pd.DataFrame(data.get('price:成交金額')); amt.index = pd.to_datetime(amt.index)
cat = data.get('security_categories').set_index('stock_id')['category']

me = [adj.index[adj.index <= d][-1] for d in adj.resample('ME').last().index if (adj.index <= d).any()]
me = [d for d in me if d >= pd.Timestamp('2020-01-01')]

rows = []
for i in range(3, len(me)):
    d0, dq = me[i], me[i - 3]
    liq = amt.loc[:d0].tail(60).mean()
    univ = liq[liq >= LIQ_MIN].index
    mom = (adj.loc[d0] / adj.loc[dq] - 1).reindex(univ).dropna()
    if len(mom) < 200:
        continue
    q = pd.qcut(mom, NQ, labels=range(1, NQ + 1), duplicates='drop')
    q5 = mom.index[q == NQ]
    share = pd.Series([cat.get(s, '?') for s in q5]).value_counts(normalize=True)
    rows.append(dict(
        month=d0.strftime('%Y-%m'), asof=str(d0.date()), n=len(mom),
        q5_qtr_median=round(float(mom[q == NQ].median() * 100), 2),
        q5_hhi=round(float((share ** 2).sum()), 4),
        q5_top3_share=round(float(share.head(3).sum() * 100), 1),
        q5_top_industry=str(share.index[0]),
    ))
H = pd.DataFrame(rows)

# 歷史分位(含當期本身,與燃料/湍流旗的 p756 慣例一致:自己也算在母體內)
for c in ['q5_qtr_median', 'q5_hhi', 'q5_top3_share']:
    H[c + '_pct'] = [round(float((H[c] <= v).mean()), 3) for v in H[c]]

H['on'] = (H.q5_qtr_median_pct >= 0.90) & (H.q5_hhi_pct >= 0.90)
last = H.iloc[-1]

payload = {
    'spec_version': SPEC_VERSION,
    'spec': f'liq>={LIQ_MIN:.0f} / {NQ}分位 / 動能窗=3個月 / 產業=security_categories(as-of 今日快照,歷史為回溯貼標)',
    'updated': str(pd.Timestamp(adj.index[-1]).date()),
    'note': '描述卡非預測器;受「市場層風險訊號永不疊加本書」鐵律約束,不可用於調整曝險',
    'latest': {k: (bool(v) if isinstance(v, (bool, np.bool_)) else v)
               for k, v in last.to_dict().items()},
    'history': H.to_dict('records'),
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')

print(f"寫入 {OUT.name}  {len(H)} 個月  {H.month.iloc[0]} → {H.month.iloc[-1]}")
# 注意:last.asof 會撞到 pandas 的 .asof 方法,欄位存取一律用 last['xxx']
L = last.to_dict()
print(f"\n最新 {L['month']}(asof {L['asof']}, n={L['n']}):")
print(f"  Q5 季漲中位   {L['q5_qtr_median']:+7.2f}%  p{L['q5_qtr_median_pct']:.0%}")
print(f"  Q5 產業 HHI   {L['q5_hhi']:7.4f}  p{L['q5_hhi_pct']:.0%}   (最大產業 {L['q5_top_industry']})")
print(f"  Q5 前三產業   {L['q5_top3_share']:7.1f}%  p{L['q5_top3_share_pct']:.0%}")
print(f"  集中度旗      {'🔴 ON' if L['on'] else '⚪ off'}")
print("\n近 10 個月:")
print(H.tail(10)[['month','q5_qtr_median','q5_qtr_median_pct','q5_hhi','q5_hhi_pct','q5_top3_share','on']].to_string(index=False))
print(f"\n史上亮燈月份: {', '.join(H[H.on].month.tolist()) or '(無)'}")
