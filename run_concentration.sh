#!/bin/zsh
# ⑧b 集中度維度 — 排程 wrapper
#
# 設計:冪等 + 自動補跑。每次執行都重算全期並覆寫 concentration.json,
#   歷史 75 筆都是真月底樣本、只有最後一筆是「進行中的當月」(asof=最新交易日),
#   所以多跑幾次不會污染分位序列,Mac 睡眠 miss 一次也會在下次補上。
# 需 FinLab token → 只能 Mac 跑(GitHub Actions 那軌無 FinLab,只讀 json)。
set -uo pipefail

HERE="$HOME/RUNiC_LOCAL/runic-monitor"
LOG="$HERE/concentration_run.log"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') start ===" >> "$LOG"

# 本機設定(不進 repo):local.env 裡寫 RUNIC_ENV=<放 FinLab 憑證的 env 檔路徑>;沒設就只用 finlab 的快取憑證
[ -f "$HERE/local.env" ] && source "$HERE/local.env"
[ -n "${RUNIC_ENV:-}" ] && { set -a; source "$RUNIC_ENV" 2>/dev/null; set +a; }

# 🔴 必須寫死絕對路徑:launchd 的 PATH 不含 /Library/Frameworks/...,
#    `command -v python3` 會抓到系統 Python 3.9(finlab 版本不同、無有效憑證 → 走互動輸入 → EOFError)
PY=/Library/Frameworks/Python.framework/Versions/3.12/bin/python3
[ -x "$PY" ] || { echo "FATAL: $PY 不存在" >> "$LOG"; \
  osascript -e 'display notification "Python 3.12 路徑失效" with title "⑧b 集中度旗更新失敗" sound name "Basso"' 2>/dev/null; exit 127; }

OUT=$("$PY" "$HERE/monthly_concentration.py" 2>&1)
RC=$?
echo "$OUT" >> "$LOG"

if [ $RC -ne 0 ]; then
  echo "FAIL rc=$RC" >> "$LOG"
  osascript -e 'display notification "monthly_concentration.py 失敗,見 concentration_run.log" with title "⑧b 集中度旗更新失敗" sound name "Basso"' 2>/dev/null
  exit $RC
fi

# 讀回結果:亮燈才通知(與 monitor 的翻轉慣例分開——這裡只是排程成功回報)
"$PY" - <<'PYEOF' >> "$LOG" 2>&1
import json, pathlib, subprocess
p = pathlib.Path.home() / 'RUNiC_LOCAL/runic-monitor/concentration.json'
L = json.loads(p.read_text(encoding='utf-8'))['latest']
msg = f"{L['month']} Q5季漲中位 {L['q5_qtr_median']:+.0f}% p{L['q5_qtr_median_pct']:.0%} / HHI p{L['q5_hhi_pct']:.0%}"
print('OK ' + msg + (' [ON]' if L['on'] else ' [off]'))
if L['on']:
    subprocess.run(['osascript', '-e',
        f'display notification "{msg}" with title "🟠 集中度旗 ON(描述卡,不調曝險)" sound name "Glass"'], check=False)
PYEOF

# 推上 repo(只推 concentration.json;雲端那軌讀的是 repo 裡這個檔)。推不上去只通知,本機結果照留。
(
  cd "$HERE" || exit 1
  git add concentration.json
  git diff --cached --quiet && { echo "push: 無變更"; exit 0; }
  git -c user.name="runic-monitor[bot]" -c user.email="runic-monitor@users.noreply.github.com" \
      commit -q -m "concentration $(date '+%Y-%m-%d')" || exit 1
  git pull -q --rebase || { git rebase --abort 2>/dev/null; exit 1; }
  git push -q && echo "push: ok"
) >> "$LOG" 2>&1 || {
  echo "push FAIL" >> "$LOG"
  osascript -e 'display notification "concentration.json 沒推上 repo,見 concentration_run.log" with title "⑧b 集中度旗推送失敗" sound name "Basso"' 2>/dev/null
}

echo "=== $(date '+%Y-%m-%d %H:%M:%S') done ===" >> "$LOG"
