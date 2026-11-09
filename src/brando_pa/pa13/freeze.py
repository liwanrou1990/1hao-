"""策略冻结清单：Git 提交、6 个影子策略的规则 / 参数 / 成本 / 数据与成交规则，以及“历史研究 B2 vs Mac 运行 B 账户 vs 本阶段影子 A”的逐项比对。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pandas as pd

from spot90.atomic import atomic_write_json, atomic_write_text

from ..config import cfg
from .shadow import shadow_specs


def _git(*a):
    try:
        return subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def freeze_manifest(out_dir: Path) -> dict:
    c = cfg(); specs = shadow_specs(c); cost = c["usdt"]["costs"]["round2"]; risk = c["trendsys"]["risk"]["state_machine"]; fwd = c.get("forward", {})
    m = {"frozen_at_utc": str(pd.Timestamp.now(tz="UTC")), "git_commit": _git("rev-parse", "HEAD"), "git_branch": _git("branch", "--show-current"), "initial_usdt": float(c["usdt"]["initial_cash_usdt"]),
         "strategies": {n: {"label": s["label"], "kind": s["kind"], "weights_by_trend_state": s["weights"], "risk_mode": s["risk_mode"], "alloc_params_override": s.get("alloc_params"), "vol_trail": s.get("vol_trail")} for n, s in specs.items()},
         "trend_rule": {"signal": "日线收盘 > SMA200 → ON（confirm_days=1）", "sma": int(c["usdt"]["trend"]["sma"]), "data": "已收盘 UTC 日线；日线收盘后下一轮服务（600 s）决策"},
         "risk_state_machine_params": risk, "allocator_params": c["usdt"]["allocator"],
         "costs": {"cost_ledger_one_side_bps": {"fee": cost["fee_bps"], "slippage": cost["slippage_bps"], "total": cost["fee_bps"] + cost["slippage_bps"]}, "ideal_ledger": "0 费用（仅对照）", "convention": "单侧；买入按 Ask×(1+滑点)、卖出按 Bid×(1−滑点)，手续费按成交额；清仓估值再扣一次卖出成本"},
         "fill_rule": {"decision": "日线收盘后首轮运行时刻 = decision_time", "exec_after": "决策所在小时 + 1 h", "price": "处理时刻实时 OKX/Coinbase Ask(买)/Bid(卖)，年龄 ≤ %s s；否则最新已收盘 1h 收盘价并标 estimated（≤ %s s）；否则 fill_paused" % (fwd.get("quotes", {}).get("max_age_s"), fwd.get("quotes", {}).get("estimated_max_age_s")),
                       "late": "> exec_after + 1h → filled_late", "expired": "> exec_after + %s h → expired" % fwd.get("missed_signal_grace_hours"), "missed": "日线收盘后 > 宽限期 + 1h 才首次处理 → missed_signal（不回填）"},
         "data_rule": {"sources_priority": fwd.get("source_priority"), "closed_bars_only": True, "cross_source_max_rel_diff": fwd.get("cross_source_max_rel_diff"), "store": "data_store/brando_pa/phase13_bars（独立；首次从 forward_bars 只读复制预热）"},
         "isolation": {"log_dir": "reports_out/brando_pa/phase13_forward", "lock": "reports_out/brando_pa/phase13_forward/service.lock", "never_touch": ["reports_out/brando_pa/phase8_forward/**", "config/brando_pa.yaml", "launchd com.brando-pa.forward"], "real_trading": "disabled (no implementation)"},
         "forward_start_rule": "各账户 forward_start_utc = 影子服务首次成功运行时刻（≥ 2026-11-09）；此前历史只用于 SMA200/ATR 预热，不产生任何信号或成交",
         "comparison_historical_B2_vs_mac_B_vs_shadow_A": [
             {"item": "分配规则", "historical_B2(Phase10/12 daily engine)": "ON 资产等权（both 50/50，单一 100%）", "mac_B_modelB_state_machine": "B_full_single：both 50/50，单一 100%", "shadow_A": "同 Mac B", "same": "规则相同"},
             {"item": "再平衡", "historical_B2(Phase10/12 daily engine)": "仅持有集合 / 限仓变化时", "mac_B_modelB_state_machine": "目标变化 或 权重漂移 ≥ 10 个百分点", "shadow_A": "同 Mac B", "same": "**不同**（Mac 多了漂移再平衡）"},
             {"item": "风险状态机", "historical_B2(Phase10/12 daily engine)": str(risk), "mac_B_modelB_state_machine": "同 trendsys.risk.state_machine", "shadow_A": "同", "same": "相同"},
             {"item": "执行时点与价格", "historical_B2(Phase10/12 daily engine)": "次日开盘价", "mac_B_modelB_state_machine": "决策后下一整点之后实时 Ask/Bid（或估算的已收盘 1h 收盘）", "shadow_A": "同 Mac B", "same": "**不同**"},
             {"item": "成本（单侧）", "historical_B2(Phase10/12 daily engine)": "研究报告主口径 25 bps（亦有 15 / 0）", "mac_B_modelB_state_machine": "cost 账本 15 bps（10 费 + 5 滑）+ ideal 0", "shadow_A": "同 Mac B（15 bps）", "same": "**不同**（主口径 25 vs 15）"},
             {"item": "行情数据", "historical_B2(Phase10/12 daily engine)": "Binance 现货日线镜像 2017-08 起", "mac_B_modelB_state_machine": "OKX 现货实时（预热自同一镜像）", "shadow_A": "同 Mac B（独立仓）", "same": "来源不同（交叉比对偏差 ≈ 0.5 bps）"},
             {"item": "信号", "historical_B2(Phase10/12 daily engine)": "close > SMA200，confirm 1", "mac_B_modelB_state_machine": "同", "shadow_A": "同", "same": "相同"},
         ],
         "conclusion": "历史研究 B2 与 Mac 运行 B 账户：信号与风险规则相同，但再平衡、执行时点/价格、成本主口径不同 → 报告中分别标记为“历史 B2（日线研究引擎）”与“前瞻 B2（Mac/影子前瞻引擎）”，不得混为一谈。影子 A 与 Mac B 规则与成本完全相同（同一引擎代码），差别只在起点时刻与独立数据仓。"}
    out_dir.mkdir(parents=True, exist_ok=True); atomic_write_json(out_dir / "freeze_manifest.json", m, indent=1, default=str)
    L = [f"# 第十三阶段策略冻结清单 · {m['frozen_at_utc'][:19]} UTC · commit `{m['git_commit']}` · 分支 `{m['git_branch']}`", "", "| 影子账户 | 类型 | 规则 | 风险 | 备注 |", "|---|---|---|---|---|"]
    for n, s in m["strategies"].items():
        L.append(f"| {n} | {s['kind']} | {s['label']} | {s['risk_mode']} | {('vol_trail ' + json.dumps(s['vol_trail'])) if s['vol_trail'] else ''}{('alloc ' + json.dumps(s['alloc_params_override'])) if s['alloc_params_override'] else ''} |")
    L += ["", f"- 趋势规则：{m['trend_rule']}", f"- 状态机参数：{m['risk_state_machine_params']}", f"- 分配器：{m['allocator_params']}", f"- 成本：{m['costs']}", f"- 成交规则：{m['fill_rule']}", f"- 数据规则：{m['data_rule']}", f"- 隔离：{m['isolation']}", f"- 起点规则：{m['forward_start_rule']}", "",
          "## 历史 B2 vs Mac B vs 影子 A", "", "| 项 | 历史 B2（Phase 10/12 日线引擎） | Mac B_modelB_state_machine | 影子 A | 是否相同 |", "|---|---|---|---|---|"] + [f"| {r['item']} | {r['historical_B2(Phase10/12 daily engine)']} | {r['mac_B_modelB_state_machine']} | {r['shadow_A']} | {r['same']} |" for r in m["comparison_historical_B2_vs_mac_B_vs_shadow_A"]] + ["", f"**结论**：{m['conclusion']}"]
    atomic_write_text(out_dir / "freeze_manifest.md", "\n".join(L)); return m
