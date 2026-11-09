"""第十四阶段只读看板：API 在合成前瞻数据与真实云端副本上正确读取；账户权益 / 费用 / 净利润口径与账本一致；历史与前瞻分区；信号时间顺序；
空数据 / 服务过期提示；只有 GET；CSV 导出；运行前后数据目录哈希不变（无意外写入）；不监听 0.0.0.0；不导入下单代码；页面导航与 iPhone 布局（Playwright，可选）；中文默认界面、状态编码映射为中文、英文切换、两种语言均无横向溢出。"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from brando_pa.config import cfg
from brando_pa.dashboard14.api import create_app
from brando_pa.dashboard14.data import DashData
from brando_pa.forward.accounts import ForwardRunner
from tests.test_brando_pa_causality import _synth_oc, T
from tests.test_brando_pa_forward import _store

ROOT = Path(__file__).resolve().parents[1]; REAL_PROD = ROOT / "reports_out" / "brando_pa" / "phase8_forward"; REAL_SHADOW = ROOT / "reports_out" / "brando_pa" / "phase13_forward"


def _hashes(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()} if d.exists() else {}


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dash14"); bars = _synth_oc(); store = _store(tmp, bars)
    r = ForwardRunner(cfg(), log_dir=tmp / "prod", store=store); r.run_once(now=T("2024-08-01 05:00"))
    for d in ("2024-08-02", "2024-08-03", "2024-08-04", "2024-08-05"):
        r.run_once(now=T(f"{d} 00:30")); r.run_once(now=T(f"{d} 01:10"))
    D = DashData(prod_dir=tmp / "prod", shadow_dir=tmp / "shadow_missing", bars_root=tmp / "bars_root", shadow_bars_root=tmp / "nope", cache_dir=tmp / "cache", online=False)
    return {"tmp": tmp, "D": D, "client": TestClient(create_app(D)), "runner": r}


def test_api_reads_synthetic_accounts_and_is_consistent_with_ledger(synth):
    c = synth["client"]; r = c.get("/api/accounts").json(); assert {a["name"] for a in r["accounts"]} == {"A_BTC_SMA200", "B_modelB_state_machine", "REF_ETH_SMA200"} and all(a["group"] == "mac_prod" for a in r["accounts"])
    for a in r["accounts"]:
        s = c.get(f"/api/accounts/{a['group']}/{a['name']}").json(); assert s["nav_consistent_with_replay"] and s["initial_usdt"] == 10000.0
        assert s["equity_usdt"] == pytest.approx(s["cash_usdt"] + s["position_value_usdt"], abs=1e-6)
        assert s["liquidation_net_profit_usdt"] == pytest.approx(s["equity_usdt"] - s["initial_usdt"] - s["exit_cost_if_liquidated_usdt"], abs=1e-6)
        assert s["profit_usdt"] == pytest.approx(s["realized_net_usdt"] + s["unrealized_usdt"], abs=1e-6)            # 权益 − 本金 = 已实现净额 + 未实现（买入手续费只扣一次）
        nav = c.get(f"/api/accounts/{a['group']}/{a['name']}/nav").json(); assert len(nav["points"]) >= 1 and all(abs(p["drawdown"]) <= 1 for p in nav["points"])
        pos = c.get(f"/api/accounts/{a['group']}/{a['name']}/positions").json(); assert "positions" in pos
    o = c.get("/api/overview").json(); assert "不把不同账户相加" in o["note"] and all(x["group"] in ("mac_prod", "shadow13") for x in o["accounts"])
    cmp_ = c.get("/api/compare").json(); assert cmp_["comparability"]["within_group"]


def test_trades_order_and_csv_and_detail(synth):
    c = synth["client"]; t = c.get("/api/trades?sort=asc&limit=1000").json(); assert t["total"] > 0
    for r in t["rows"]:
        assert pd.Timestamp(r["signal_ts"]) <= pd.Timestamp(r["decision_time"]) <= pd.Timestamp(r["execution_time"])          # 信号 ≤ 决策 ≤ 执行
        if r.get("price_time"):
            assert pd.Timestamp(r["decision_time"]) < pd.Timestamp(r["price_time"]) <= pd.Timestamp(r["execution_time"])
    csv = c.get("/api/trades.csv").text; assert csv.startswith("﻿group,account") and csv.count("\n") >= t["total"]
    r0 = t["rows"][0]; d = c.get(f"/api/trades/{r0['group']}/{r0['account']}/{r0['order_id']}").json(); assert any(e["kind"] == "fill" for e in d["events"]) and d["structure_at_decision"]["as_of_day"] <= str(pd.Timestamp(r0["signal_ts"]).date())
    assert c.get("/api/trades?side=SELL&limit=5").status_code == 200 and c.get("/api/trades?q=BTC&limit=5").json()["total"] <= t["total"]


def test_market_bars_structure_and_opportunities(synth):
    c = synth["client"]
    for tf in ("1h", "4h", "1d", "1w", "1M"):
        b = c.get(f"/api/market/bars?symbol=BTCUSDT&tf={tf}&limit=50").json(); assert b["tf"] == tf and len(b["bars"]) >= 1 and all(x["high"] >= max(x["open"], x["close"]) for x in b["bars"])
    s = c.get("/api/market/structure?symbol=BTCUSDT&as_of=2024-08-03").json(); ct = s["confirmed_at_time"]; hd = s["hindsight"]
    assert ct["as_of_day"] <= "2024-08-03" and hd["as_of_day"] >= ct["as_of_day"] and all(sw["confirmed_time"] <= pd.Timestamp("2024-08-04", tz="UTC").timestamp() for sw in ct["swings"])   # 当时已确认 ≠ 事后
    o = c.get("/api/opportunities").json(); assert set(o["symbols"]) == {"BTCUSDT", "ETHUSDT"}
    for s_ in o["symbols"].values():
        assert all(f["state"] in ("BUY", "WAIT", "HOLD", "REDUCE", "EXIT") for f in s_["frozen_strategies"]) and "研究性分析" in s_["research"]["label"]
    assert c.get("/api/market/bars?symbol=XXX&tf=1d").status_code == 400 and c.get("/api/accounts/mac_prod/NOPE").status_code == 404


def test_opportunities_research_fields_for_lay_users(synth):
    """买入机会页所需的面向普通用户的研究字段齐全；研究性观察绝不冒充可执行信号；未满足条件时给出中文说明。"""
    c = synth["client"]; o = c.get("/api/opportunities").json()
    for sym, s_ in o["symbols"].items():
        r = s_["research"]; assert "研究性分析" in r["label"] and r.get("executable") in (False, None)
        if "error" in r:                                                                                                   # 数据不足时明确标注不可用，而不是编造字段
            assert "不可用" in r["label"]; continue
        for k in ("state", "next_resistance", "second_resistance", "structure_invalidation", "last_confirmed_high", "last_confirmed_low", "atr14", "volume_confirmation", "unmet_conditions", "daily_filter_ok", "plan"):
            assert k in r, (sym, k)
        assert isinstance(r["unmet_conditions"], list) and isinstance(r["volume_confirmation"]["buy_confirmed"], bool)
        if r["state"] in ("WAIT", "证据不足"):
            assert len(r["unmet_conditions"]) >= 1 and all(isinstance(x, str) and x for x in r["unmet_conditions"]), sym      # 非 BUY 时必须说明尚未满足的条件
        if r["next_resistance"] is not None:
            assert s_["trend"].get("close") is None or r["next_resistance"][0] >= s_["trend"]["close"] * 0.999                                                            # 卖出候选区在现价上方
        for f in s_["frozen_strategies"]:
            assert set(f) >= {"account", "state", "signal_ts"} and f["state"] in ("BUY", "WAIT", "HOLD", "REDUCE", "EXIT")   # 状态编码不变（展示层再映射为中文）


def test_health_empty_shadow_and_stale_service_flags(synth):
    c = synth["client"]; h = c.get("/api/health").json()
    assert h["real_trading"]["live_trading_disabled"] and h["acceptance"]["passed"] is False and "验收成功" not in json.dumps(h, ensure_ascii=False)
    assert h["mac_service"]["alive_by_heartbeat"] is False                                              # 合成目录无心跳 → 不得显示运行中
    p13 = c.get("/api/phase13").json(); assert p13["started"] is False and p13["status"] == "未启动"
    a = c.get("/api/accounts/mac_prod/A_BTC_SMA200/analytics").json(); assert a["category"].startswith("forward") and "数据不足" in str(a["regimes"]) and a["evidence"].startswith("统计证据不足")
    r = c.get("/api/research").json(); assert r["category"].startswith("历史回测")


def test_read_only_no_writes_outside_cache_and_get_only(synth):
    c = synth["client"]; tmp = synth["tmp"]; before = _hashes(tmp / "prod") | {("bars/" + k): v for k, v in _hashes(tmp / "bars_root").items()}
    for ep in ("/api/overview", "/api/compare", "/api/opportunities", "/api/health", "/api/trades", "/api/trades.csv", "/api/research", "/api/phase13", "/api/market/structure?symbol=ETHUSDT", "/"):
        assert c.get(ep).status_code == 200, ep
    assert _hashes(tmp / "prod") | {("bars/" + k): v for k, v in _hashes(tmp / "bars_root").items()} == before
    for m in ("post", "put", "delete", "patch"):
        assert getattr(c, m)("/api/meta").status_code == 405
    src = (ROOT / "src" / "brando_pa" / "dashboard14").rglob("*.py")
    for p in src:
        txt = p.read_text(encoding="utf-8"); assert not re.search(r"\b(place_order|create_order|submit_order|ccxt|binance\.client)\b", txt), p.name   # 不导入 / 不调用任何下单接口
    from brando_pa.dashboard14 import api as apimod
    with pytest.raises(SystemExit):
        apimod.main(["--host", "0.0.0.0", "--port", "1"])


def test_real_cloud_copy_if_present():
    if not (REAL_PROD / "A_BTC_SMA200" / "events.jsonl").exists():
        pytest.skip("no real forward copy")
    before = _hashes(REAL_PROD) | _hashes(REAL_SHADOW)
    D = DashData(online=False); c = TestClient(create_app(D)); o = c.get("/api/overview").json()
    assert any(a["group"] == "mac_prod" for a in o["accounts"]) and o["real_trading"]["live_trading_disabled"]
    for a in o["accounts"]:
        if "equity_usdt" in a:
            assert a["nav_consistent_with_replay"], a["name"]
    assert _hashes(REAL_PROD) | _hashes(REAL_SHADOW) == before


@pytest.mark.skipif(shutil.which("node") is None, reason="node/playwright not available")
def test_ui_navigation_and_iphone_layout(synth, tmp_path):
    import socket
    tmp = synth["tmp"]; port = 8599
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), BRANDO_DASH_PROD_DIR=str(tmp / "prod"), BRANDO_DASH_SHADOW_DIR=str(tmp / "shadow_missing"), BRANDO_DASH_BARS=str(tmp / "bars_root"), BRANDO_DASH_CACHE=str(tmp / "cache"), BRANDO_DASH_OFFLINE="1")
    errlog = tmp_path / "server.err"; proc = subprocess.Popen([sys.executable, "-m", "brando_pa.dashboard14.api", "--host", "127.0.0.1", "--port", str(port)], env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=open(errlog, "w"))
    try:
        up = False; deadline = time.monotonic() + 60                                # 用单调时钟判定超时：全套测试中有模块会把 time.sleep 替换为空操作，不能按睡眠次数计时
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError("dashboard server exited early: " + errlog.read_text()[-800:])
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close(); up = True; break
            except OSError:
                time.sleep(0.2); os.sched_yield() if hasattr(os, "sched_yield") else None
        assert up, "dashboard server did not open port %d within 60 s: %s" % (port, errlog.read_text()[-800:])
        js = tmp_path / "ui.js"; js.write_text("""
const { chromium, devices } = require('playwright');
const RAW = [/\\bWAIT\\b/, /\\bHOLD\\b/, /\\bREDUCE\\b/, /\\bEXIT\\b/, /\\bNORMAL\\b/, /\\bRISK_REDUCED\\b/, /filled_late/, /filled_estimated/, /live_quote_(ask|bid)/, /estimated_closed_bar_close/, /cancelled_invalid/, /\\bmac_prod\\b/, /\\bshadow13\\b/];
(async () => { const b = await chromium.launch(); const out = {};
  for (const [name, ctxOpts] of [['desktop', { viewport: { width: 1440, height: 900 } }], ['iphone', devices['iPhone 13']]]) {
    const ctx = await b.newContext(ctxOpts); const p = await ctx.newPage(); const errors = []; p.on('pageerror', e => errors.push(String(e)));
    await p.goto('http://127.0.0.1:%d/#overview', { waitUntil: 'load', timeout: 60000 }); await p.evaluate(() => localStorage.removeItem('brando_lang'));
    for (const h of ['overview','compare','charts','opps','positions','analytics','health','trades','phase13','help']) {
      await p.goto('http://127.0.0.1:%d/#' + h, { waitUntil: 'load', timeout: 60000 }); await p.waitForTimeout(h === 'charts' ? 2500 : 1200);
      const txt = await p.evaluate(() => document.body.innerText); const raw = RAW.filter(r => r.test(txt)).map(String);
      out[name + ':' + h] = { len: txt.length, fail: txt.includes('页面加载失败') || txt.includes('failed to load'), ok: txt.includes('Brando'), raw, lang: await p.evaluate(() => document.documentElement.lang),
        zh: ['买入信号','等待买入条件','继续持有','减少仓位','卖出退出','买入机会','投资总览','使用说明'].filter(k => txt.includes(k)), sw: await p.evaluate(() => document.documentElement.scrollWidth) };
      if (h === 'overview' || h === 'charts' || h === 'opps') await p.screenshot({ path: process.argv[2] + '/' + name + '_' + h + '.png' });
    }
    out[name + ':scrollWidth'] = await p.evaluate(() => document.documentElement.scrollWidth); out[name + ':viewport'] = (await p.viewportSize()).width;
    // 英文切换：只影响展示层
    await p.goto('http://127.0.0.1:%d/#overview', { waitUntil: 'load', timeout: 60000 }); await p.waitForTimeout(800); await p.selectOption('#langsel', 'en'); await p.waitForTimeout(1200);
    const en = await p.evaluate(() => document.body.innerText); out[name + ':en'] = { overview: en.includes('Overview'), help: en.includes('Help'), zh_leak: en.includes('投资总览'), stored: await p.evaluate(() => localStorage.getItem('brando_lang')), sw: await p.evaluate(() => document.documentElement.scrollWidth) };
    await p.goto('http://127.0.0.1:%d/#opps', { waitUntil: 'load', timeout: 60000 }); await p.waitForTimeout(1200); const enOpps = await p.evaluate(() => document.body.innerText); out[name + ':en_opps'] = { ok: enOpps.includes('Buy Opportunity Monitor') && (enOpps.includes('Waiting for buy conditions') || enOpps.includes('Keep holding') || enOpps.includes('Buy signal') || enOpps.includes('No signal yet')), sw: await p.evaluate(() => document.documentElement.scrollWidth) };
    await p.selectOption('#langsel', 'zh'); await p.waitForTimeout(800); out[name + ':back_zh'] = (await p.evaluate(() => document.body.innerText)).includes('买入机会监控');
    out[name + ':errors'] = errors; await ctx.close(); }
  await b.close(); console.log(JSON.stringify(out)); })().catch(e => { console.error(e); process.exit(1); });""" % (port, port, port, port))
        r = subprocess.run(["node", str(js), str(tmp_path)], capture_output=True, text=True, timeout=600); assert r.returncode == 0, r.stderr[-800:]
        res = json.loads(r.stdout.strip().splitlines()[-1])
        for dev in ("desktop", "iphone"):
            vw = res[dev + ":viewport"]
            for h in ("overview", "compare", "charts", "opps", "positions", "analytics", "health", "trades", "phase13", "help"):
                v = res[dev + ":" + h]; assert v["ok"] and not v["fail"], (dev, h, v)
                assert v["lang"] == "zh-CN" and v["raw"] == [], ("中文模式下出现原始状态编码", dev, h, v["raw"])          # WAIT/HOLD/NORMAL/filled_late… 不得直接露出
                assert v["sw"] <= vw + 2, ("横向溢出", dev, h, v["sw"], vw)
            assert set(res[dev + ":overview"]["zh"]) >= {"投资总览", "使用说明"} and "买入机会" in res[dev + ":overview"]["zh"], res[dev + ":overview"]["zh"]
            assert any(k in res[dev + ":opps"]["zh"] for k in ("买入信号", "等待买入条件", "继续持有", "减少仓位", "卖出退出")), res[dev + ":opps"]["zh"]   # 状态已映射为中文
            en = res[dev + ":en"]; assert en["overview"] and en["help"] and not en["zh_leak"] and en["stored"] == "en" and en["sw"] <= vw + 2, en
            assert res[dev + ":en_opps"]["ok"] and res[dev + ":en_opps"]["sw"] <= vw + 2 and res[dev + ":back_zh"], res[dev + ":en_opps"]
            assert res[dev + ":scrollWidth"] <= vw + 2 and res[dev + ":errors"] == [], (dev, res[dev + ":errors"])
    finally:
        proc.terminate(); proc.wait(timeout=10)
