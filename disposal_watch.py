#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""處置聽牌日報(P0 工程,2026-09-02 翔令「門檻價計算+每日聽牌清單,掛 git 每晚更新」)。
純官方端點(TWSE/TPEx),無 FinLab。每晚跑:抓當日注意/處置/行情 → 每檔注意股狀態 → 分級 →
門檻價 → 寫 disposal_watch/{latest.md,latest.json,index.html,log.md,state.json}。
規則(校準 2026-09-02,面板 2024+ 第一款 n=6,642):
  - 累積漲跌幅 = 最近六個營業日「日漲跌幅算術加總」(誤差 0.02pp,命中 99-100%);第一款門檻 上市 25% / 上櫃 23%(經驗下限)
  - 三大天條:連 3 日第一款 | 連 5 日注意(1–8 款) | 10 日內 6 天 | 30 日內 12 天(覆蓋 old10 99%、新制 100%)
  - 9–13 款不計入處置累計
自我驗證:每次跑對照昨日「今晚公告」→今日處置名單、昨日「差一次」→今日注意名單,命中率寫 log。
"""
import json, re, sys, datetime as dt
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "disposal_watch"; OUT.mkdir(exist_ok=True)
STATE = OUT / "state.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
THR = {"TWSE": 25.0, "TPEX": 23.0}
LIMIT_UP = 10.0
NOW = dt.datetime.now()  # runner UTC 時要換算;GitHub Actions 用 TZ env 設 Asia/Taipei

def log(msg):
    print(msg, flush=True)
    with (OUT / "log.md").open("a", encoding="utf-8") as f:
        f.write(f"- {NOW.strftime('%Y-%m-%d %H:%M')} {msg}\n")

def get_json(url, params, timeout=60, tries=2):
    last = None
    for k in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout); return r.json()
        except Exception as e:
            last = e
            if k + 1 < tries:
                import time; time.sleep(8)
    raise last

def roc(s):
    s = str(s).strip(); y, m, d = re.split(r"[/.]", s)[:3]; return f"{int(y)+1911:04d}-{int(m):02d}-{int(d):02d}"

def num(x):
    try: return float(str(x).replace(",", "").replace("--", "nan"))
    except Exception: return float("nan")

CN = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10,"十一":11,"十二":12,"十三":13}
def clauses(text):
    return sorted({CN[m] for m in re.findall(r"第(十[一二三]|[一二三四五六七八九十])款", str(text)) if m in CN})

# ---------- state ----------
state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
closes = state.setdefault("closes", {})        # sid -> {date: close}
att = state.setdefault("attention", {})        # f"{sid}|{date}" -> {sid,name,mkt,date,text,clauses,close,cum}
disp = state.setdefault("disposal", {})        # f"{sid}|{start}" -> {sid,name,mkt,start,end,measure}
pred_prev = state.get("pred", {})              # 昨日預測(自我驗證用)

# ---------- 1) 行情(closes 快取;冷啟動回填 12 個交易日) ----------
def fetch_closes(d: dt.date):
    got = {}
    j = get_json("https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX", {"date": d.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"}, 90)
    tabs = [t for t in j.get("tables", []) if "每日收盤行情" in t.get("title", "")]
    if tabs:
        for r in tabs[0]["data"]:
            sid = str(r[0]).strip(); c = num(r[8])
            if re.fullmatch(r"\d{4}", sid) and c == c: got[sid] = c
    j = get_json("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes", {"date": d.strftime("%Y/%m/%d"), "response": "json"}, 60)
    for r in (j.get("tables") or [{}])[0].get("data", []):
        sid = str(r[0]).strip(); c = num(r[2])
        if re.fullmatch(r"\d{4}", sid) and c == c: got[sid] = c
    return got

have_dates = sorted({d for m in closes.values() for d in m})
need = []
d = NOW.date(); tries = 0
while len(need) + len([x for x in have_dates if x >= (NOW.date() - dt.timedelta(days=25)).isoformat()]) < 12 and tries < 25:
    if d.weekday() < 5 and d.isoformat() not in have_dates: need.append(d)
    d -= dt.timedelta(days=1); tries += 1
for d in sorted(need):
    try:
        got = fetch_closes(d)
    except Exception as e:
        log(f"WARN 行情 {d} 抓取失敗 {type(e).__name__}"); continue
    if not got: continue
    for sid, c in got.items(): closes.setdefault(sid, {})[d.isoformat()] = c
    log(f"行情 {d} 入庫 {len(got)} 檔")
# 修剪:每檔只留最近 40 個日期
for sid in list(closes):
    ks = sorted(closes[sid])[-40:]; closes[sid] = {k: closes[sid][k] for k in ks}
tdays = sorted({d for m in closes.values() for d in m})
if not tdays:
    log("FAIL 無任何行情資料"); sys.exit(1)
ASOF = tdays[-1]

# ---------- 2) 注意 / 處置(近 45 曆日,冪等入庫) ----------
start = (NOW.date() - dt.timedelta(days=45)); end = NOW.date()
try:
    j = get_json("https://www.twse.com.tw/rwd/zh/announcement/notice", {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"}, 60)
    for r in j.get("data", []):
        sid = str(r[1]).strip()
        if not re.fullmatch(r"\d{4}", sid): continue
        dd = roc(r[5]); att[f"{sid}|{dd}"] = dict(sid=sid, name=str(r[2]).strip(), mkt="TWSE", date=dd, text=str(r[4]), clauses=clauses(r[4]), close=num(r[6]), cum=num(r[3]))
except Exception as e: log(f"WARN TWSE notice {type(e).__name__}")
try:
    j = get_json("https://www.tpex.org.tw/www/zh-tw/bulletin/attention", {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"}, 60)
    for r in (j.get("tables") or [{}])[0].get("data", []):
        sid = str(r[1]).strip()
        if not re.fullmatch(r"\d{4}", sid): continue
        dd = roc(r[5]); att[f"{sid}|{dd}"] = dict(sid=sid, name=re.sub(r"\(.*?\)", "", str(r[2])).strip(), mkt="TPEX", date=dd, text=str(r[4]), clauses=clauses(r[4]), close=num(r[6]), cum=num(r[3]))
except Exception as e: log(f"WARN TPEx attention {type(e).__name__}")
try:
    j = get_json("https://www.twse.com.tw/rwd/zh/announcement/punish", {"startDate": start.strftime("%Y%m%d"), "endDate": end.strftime("%Y%m%d"), "response": "json"}, 60)
    for r in j.get("data", []):
        sid = str(r[2]).strip()
        if not re.fullmatch(r"\d{4}", sid): continue
        per = re.split(r"[～~]", str(r[6]))
        if len(per) < 2: continue
        disp[f"{sid}|{roc(per[0])}"] = dict(sid=sid, name=str(r[3]).strip(), mkt="TWSE", start=roc(per[0]), end=roc(per[1]), measure=str(r[7]).strip())
except Exception as e: log(f"WARN TWSE punish {type(e).__name__}")
try:
    j = get_json("https://www.tpex.org.tw/www/zh-tw/bulletin/disposal", {"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d"), "response": "json"}, 60)
    for r in (j.get("tables") or [{}])[0].get("data", []):
        sid = str(r[2]).strip()
        if not re.fullmatch(r"\d{4}", sid): continue
        per = re.split(r"[～~]", str(r[5]))
        if len(per) < 2: continue
        disp[f"{sid}|{roc(per[0])}"] = dict(sid=sid, name=re.sub(r"\(.*?\)", "", str(r[3])).strip(), mkt="TPEX", start=roc(per[0]), end=roc(per[1]), measure="處置")
except Exception as e: log(f"WARN TPEx disposal {type(e).__name__}")
# 修剪 90 天
cut = (NOW.date() - dt.timedelta(days=90)).isoformat()
att = {k: v for k, v in att.items() if v["date"] >= cut}; disp = {k: v for k, v in disp.items() if v["start"] >= cut}
state["attention"], state["disposal"] = att, disp

# ---------- 3) 狀態機 ----------
att_days = sorted({v["date"] for v in att.values()})
ATT_ASOF = max(att_days) if att_days else None
tset = tdays  # 交易日曆=行情日期
def tidx(dstr): return tset.index(dstr) if dstr in tset else None
rows = []
by_sid = {}
for v in att.values(): by_sid.setdefault(v["sid"], []).append(v)
for sid, recs in by_sid.items():
    recs = sorted(recs, key=lambda x: x["date"])
    today_rec = next((r for r in recs if r["date"] == ATT_ASOF), None)
    # 規則語意:處置期滿後計數重置 → 只算最近一次處置「結束日之後」的注意日
    # (2026-09-02 首驗誤報 6225/5321 兩例皆為出關後沿用舊注意日累計)
    _spans = [v for v in disp.values() if v["sid"] == sid]
    _reset_after = max((v["end"] for v in _spans if v["end"] < ATT_ASOF), default="0000-00-00")
    eff = {r["date"] for r in recs if any(1 <= c <= 8 for c in r["clauses"]) and r["date"] > _reset_after}
    k1 = {r["date"] for r in recs if 1 in r["clauses"] and r["date"] > _reset_after}
    if not eff and not today_rec: continue
    i = tidx(ATT_ASOF)
    if i is None: continue
    w10 = tset[max(0, i-9): i+1]; w30 = tset[max(0, i-29): i+1]
    n10 = sum(1 for x in w10 if x in eff); n30 = sum(1 for x in w30 if x in eff)
    consec = 0
    for x in reversed(tset[max(0, i-12): i+1]):
        if x in eff: consec += 1
        else: break
    k1c = 0
    for x in reversed(tset[max(0, i-12): i+1]):
        if x in k1: k1c += 1
        else: break
    if consec == 0 and n10 == 0: continue
    mkt = (today_rec or recs[-1])["mkt"]; name = (today_rec or recs[-1])["name"]
    # 處置狀態
    spans = [v for v in disp.values() if v["sid"] == sid]
    in_disp = any(v["start"] <= ATT_ASOF <= v["end"] for v in spans)
    last_end = max((v["end"] for v in spans if v["end"] < ATT_ASOF), default=None)
    days_since = (tidx(ATT_ASOF) - (tidx(last_end) if last_end and tidx(last_end) is not None else -999)) if last_end else None
    repeat_zone = bool(last_end and days_since is not None and 0 < days_since <= 30)
    # 門檻價(第一款):Σ 近五日日漲跌幅 + 明日 ≥ thr
    cl = closes.get(sid, {}); cds = sorted(cl)[-7:]
    sum5 = None; last_close = cl.get(ASOF)
    if len(cds) >= 6 and cds[-1] == ASOF:
        rets = [(cl[cds[j]] / cl[cds[j-1]] - 1) * 100 for j in range(1, len(cds))]
        sum5 = sum(rets[-5:])
    thr = THR[mkt]
    need_up = (thr - sum5) if sum5 is not None else None
    need_dn = (-thr - sum5) if sum5 is not None else None
    px_up = round(last_close * (1 + need_up / 100), 2) if (need_up is not None and last_close) else None
    px_dn = round(last_close * (1 + need_dn / 100), 2) if (need_dn is not None and last_close) else None
    up_ok = need_up is not None and need_up <= LIMIT_UP; dn_ok = need_dn is not None and need_dn >= -LIMIT_UP
    # 分級
    rule_hit = [n for n, ok in [("連3第一款", k1c >= 3), ("連5注意", consec >= 5), ("10中6", n10 >= 6), ("30中12", n30 >= 12)] if ok]
    one_short = [n for n, ok in [("連2第一款→明日再第一款", k1c == 2), ("連4注意→明日任一款", consec == 4), ("10中5→明日任一款", n10 == 5), ("30中11→明日任一款", n30 == 11)] if ok]
    two_short = [n for n, ok in [("連1第一款", k1c == 1), ("連3注意", consec == 3), ("10中4", n10 == 4)] if ok]
    today_eff = bool(today_rec) and any(1 <= c <= 8 for c in today_rec["clauses"])
    if in_disp: grade = "處置中"
    elif rule_hit and today_eff: grade = "A 今晚公告處置"
    elif rule_hit and not today_eff: grade = "D 注意中"; rule_hit = []   # 累計達標但今日未注意=不觸發(規則語意)
    elif one_short: grade = "B 差一次(聽牌)"
    elif two_short: grade = "C 差兩次"
    else: grade = "D 注意中"
    rows.append(dict(sid=sid, name=name, mkt=mkt, grade=grade, rule=rule_hit, one_short=one_short, two_short=two_short,
                     consec=consec, k1c=k1c, n10=n10, n30=n30, cum_official=(today_rec or {}).get("cum"),
                     today_clauses=(today_rec or {}).get("clauses", []), close=last_close, sum5=None if sum5 is None else round(sum5, 2),
                     need_up=None if need_up is None else round(need_up, 2), px_up=px_up if up_ok else None,
                     need_dn=None if need_dn is None else round(need_dn, 2), px_dn=px_dn if dn_ok else None,
                     in_disposal=in_disp, repeat_zone=repeat_zone, last_disposal_end=last_end))
order = {"A 今晚公告處置": 0, "B 差一次(聽牌)": 1, "C 差兩次": 2, "D 注意中": 3, "處置中": 4}
rows.sort(key=lambda r: (order[r["grade"]], -(r["k1c"] * 10 + r["consec"])))

# ---------- 4) 自我驗證(昨日預測 vs 今日實況) ----------
val = {}
if pred_prev and pred_prev.get("asof") and pred_prev["asof"] < (ATT_ASOF or ""):
    pa = set(pred_prev.get("A", [])); pb = set(pred_prev.get("B", []))
    today_starts = {v["sid"] for v in disp.values() if v["start"] > pred_prev["asof"] and v["start"] <= (ATT_ASOF or "")}
    # A:預測今晚公告 → 處置開始日應=次一交易日(或之後 1-2 日)
    hitA = len(pa & today_starts); todays_att = {v["sid"] for v in att.values() if v["date"] == ATT_ASOF}
    hitB = len(pb & (todays_att | today_starts))
    val = dict(prev_asof=pred_prev["asof"], A_n=len(pa), A_hit=hitA, B_n=len(pb), B_hit=hitB)
    log(f"驗證 {pred_prev['asof']}→{ATT_ASOF}: A 今晚公告 {hitA}/{len(pa)} 命中處置 | B 聽牌 {hitB}/{len(pb)} 次日再注意或處置")
state["pred"] = dict(asof=ATT_ASOF, A=[r["sid"] for r in rows if r["grade"].startswith("A")], B=[r["sid"] for r in rows if r["grade"].startswith("B")])

# ---------- 5) 輸出 ----------
state["closes"] = closes
STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
summary = dict(asof_attention=ATT_ASOF, asof_quotes=ASOF, generated=NOW.strftime("%Y-%m-%d %H:%M"), n=len(rows),
               counts={g: sum(1 for r in rows if r["grade"] == g) for g in order}, validation=val, thresholds=THR,
               rule_note="累積漲跌幅=六日日漲跌幅加總;門檻 上市25%/上櫃23%;9–13款不計")
(OUT / "latest.json").write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1), encoding="utf-8")

def fmt_px(r):
    parts = []
    if r["px_up"] and r["need_up"] <= 0: parts.append(f"已達第一款門檻,明日收盤 ≥{r['px_up']} 即續觸(可跌 {abs(r['need_up']):.1f}%)")
    elif r["px_up"]: parts.append(f"漲觸 ≥{r['px_up']}(需 +{r['need_up']:.1f}%)")
    elif r["need_up"] is not None: parts.append(f"漲觸需 +{r['need_up']:.1f}%(超漲停,明日不可能)")
    if r["px_dn"]: parts.append(f"跌觸 ≤{r['px_dn']}(需 {r['need_dn']:.1f}%)")
    return "；".join(parts) or "—"
md = [f"# 處置聽牌日報", f"注意名單 as-of {ATT_ASOF}｜行情 as-of {ASOF}｜產出 {summary['generated']}", "",
      "口徑：累積漲跌幅＝六日日漲跌幅加總；第一款門檻 上市 25%／上櫃 23%（經驗下限）；9–13 款不計入處置累計；三大天條＝連3第一款／連5注意／10中6／30中12。門檻價＝明日收盤需達的價位（依第一款）。", ""]
if val: md.append(f"昨日自驗（{val['prev_asof']}→{ATT_ASOF}）：A 今晚公告 {val['A_hit']}/{val['A_n']} 進處置；B 聽牌 {val['B_hit']}/{val['B_n']} 次日再注意或處置\n")
md.append(f"分級：{' ｜ '.join(f'{g} {c}' for g, c in summary['counts'].items())}\n")
for g in order:
    grp = [r for r in rows if r["grade"] == g]
    if not grp: continue
    md.append(f"## {g}（{len(grp)}）\n")
    md.append("| 代號 | 名稱 | 市場 | 連注意 | 連第一款 | 10日 | 30日 | 官方累計 | 今日款 | 收盤 | 近五日Σ | 明日第一款門檻價 | 備註 |")
    md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in grp:
        note = "、".join(r["rule"] or r["one_short"] or r["two_short"])
        if r["repeat_zone"]: note += "｜30日內再犯→二次(全額預收)"
        if r["in_disposal"]: note = "處置期間"
        md.append(f"| {r['sid']} | {r['name']} | {r['mkt']} | {r['consec']} | {r['k1c']} | {r['n10']} | {r['n30']} | {'' if r['cum_official'] != r['cum_official'] else int(r['cum_official']) if r['cum_official'] is not None else ''} | {','.join(map(str, r['today_clauses']))} | {r['close'] or ''} | {'' if r['sum5'] is None else f'{r['sum5']:+.1f}%'} | {fmt_px(r)} | {note} |")
    md.append("")
(OUT / "latest.md").write_text("\n".join(md), encoding="utf-8")
html = ["<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>處置聽牌日報</title><style>body{font-family:-apple-system,'PingFang TC',sans-serif;margin:16px;background:#f9f9f7;color:#0b0b0b}",
        "table{border-collapse:collapse;font-size:13px;width:100%}th,td{border-bottom:1px solid #e1e0d9;padding:4px 6px;text-align:left;white-space:nowrap}",
        "h2{margin-top:22px;font-size:16px}.A{background:#fdecec}.B{background:#fff6dd}.C{background:#f3f7fd}.small{color:#52514e;font-size:12px}</style></head><body>",
        f"<h1 style='font-size:18px'>處置聽牌日報</h1><div class='small'>注意 as-of {ATT_ASOF}｜行情 as-of {ASOF}｜產出 {summary['generated']}｜門檻 上市25%/上櫃23%｜累積＝六日日漲跌幅加總</div>"]
if val: html.append(f"<div class='small'>昨日自驗 A {val['A_hit']}/{val['A_n']}｜B {val['B_hit']}/{val['B_n']}</div>")
for g in order:
    grp = [r for r in rows if r["grade"] == g]
    if not grp: continue
    html.append(f"<h2>{g}（{len(grp)}）</h2><table><tr><th>代號</th><th>名稱</th><th>市</th><th>連注</th><th>連一</th><th>10日</th><th>30日</th><th>今日款</th><th>收盤</th><th>近五Σ</th><th>明日第一款門檻價</th><th>備註</th></tr>")
    for r in grp:
        note = "、".join(r["rule"] or r["one_short"] or r["two_short"]) + ("｜再犯→二次" if r["repeat_zone"] else "")
        html.append(f"<tr class='{g[0]}'><td>{r['sid']}</td><td>{r['name']}</td><td>{r['mkt'][:2]}</td><td>{r['consec']}</td><td>{r['k1c']}</td><td>{r['n10']}</td><td>{r['n30']}</td><td>{','.join(map(str, r['today_clauses']))}</td><td>{r['close'] or ''}</td><td>{'' if r['sum5'] is None else f'{r['sum5']:+.1f}%'}</td><td>{fmt_px(r)}</td><td>{note}</td></tr>")
    html.append("</table>")
html.append("</body></html>")
(OUT / "index.html").write_text("\n".join(html), encoding="utf-8")
log(f"完成 as-of 注意 {ATT_ASOF}/行情 {ASOF}:{summary['counts']}")
