/* Brando Investment Intelligence Dashboard — 只读前端（原生 JS，hash 路由，Lightweight Charts + Plotly） */
const PAGES = [["overview", "投资总览"], ["compare", "策略对比"], ["charts", "K 线与价格行为"], ["opps", "买入机会"], ["positions", "持仓与卖出"], ["analytics", "收益与风险"], ["health", "验收与健康"], ["trades", "交易记录"], ["phase13", "前瞻验证"], ["help", "使用说明"]];
const $ = (s) => document.querySelector(s);
const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : Number(v).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v)) ? "—" : (v * 100).toFixed(d) + "%";
const sgn = (v) => v > 0 ? "up" : v < 0 ? "down" : "";
const t16 = (s) => s ? String(s).slice(0, 16).replace("T", " ") : "—";
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
async function api(p) { const r = await fetch(p, { cache: "no-store" }); if (!r.ok) throw new Error(p + " → " + r.status + " " + (await r.text()).slice(0, 200)); return r.json(); }
function tag(kind) { return { mac: "<span class='tag mac'>原 Mac 账户</span>", fw: "<span class='tag fw'>前瞻模拟</span>", bt: "<span class='tag bt'>历史回测</span>", rl: "<span class='tag rl'>真实账户：无</span>", rs: "<span class='tag rs'>研究性分析</span>" }[kind] || ""; }
function groupTag(g) { return g === "mac_prod" ? tag("mac") : g === "shadow13" ? tag("fw") : tag("bt"); }
function card(k, v, s = "", cls = "") { return `<div class="card"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`; }
function openModal(title, html) { $("#modal-title").textContent = title; $("#modal-body").innerHTML = html; $("#modal").classList.add("open"); }
function closeModal() { $("#modal").classList.remove("open"); }
window.closeModal = closeModal;

/* ---------- 顶栏 ---------- */
async function header() {
  try {
    const o = await api("/api/overview"); const t = o.ticker.symbols; const parts = [];
    for (const s of ["BTCUSDT", "ETHUSDT"]) { const x = t[s] || {}; parts.push(`<span class="tick"><span class="sym">${s.slice(0, 3)}</span><span class="px">${fmt(x.price, s === "BTCUSDT" ? 1 : 2)}</span><span class="chg ${sgn(x.change_24h)}">${x.change_24h == null ? "—" : (x.change_24h >= 0 ? "+" : "") + (x.change_24h * 100).toFixed(2) + "%"}</span></span>`); }
    $("#ticker").innerHTML = parts.join(""); $("#ticker").classList.remove("muted");
    const bt = o.trend.BTCUSDT || {}; $("#trendpill").textContent = "BTC " + (bt.trend || "—").replace("（close > SMA200）", "").replace("（close ≤ SMA200）", "");
    const src = t.BTCUSDT || {}; $("#srcpill").textContent = `${src.live ? "实时 " : ""}${(src.source || "—").split(" ")[0]} · ${t16(src.ts_utc)} UTC · 延迟 ${src.latency_s == null ? "—" : Math.round(src.latency_s) + "s"}`; $("#srcpill").className = "pill " + (src.live ? "ok" : "warn");
    const sv = o.service || {}; const alive = sv.alive_by_heartbeat; $("#svcpill").textContent = "Mac 服务 " + (alive ? "运行中" : (sv.heartbeat_utc ? `心跳 ${Math.round((sv.heartbeat_age_s || 0) / 60)} 分钟前` : "无心跳")); $("#svcpill").className = "pill " + (alive ? "ok" : "bad");
    $("#rtpill").className = "pill " + (o.real_trading.live_trading_disabled ? "ok" : "bad"); $("#rtpill").textContent = o.real_trading.live_trading_disabled ? "真实交易 禁用" : "真实交易 配置异常";
  } catch (e) { $("#ticker").textContent = "行情不可用：" + e.message; }
}

/* ---------- 页面 ---------- */
const P = {};
P.overview = async (m) => {
  const o = await api("/api/overview");
  const accs = (g) => o.accounts.filter((a) => a.group === g);
  const accCards = (a) => `<div class="card" style="grid-column:span 2"><div class="k">${groupTag(a.group)} ${esc(a.label)} <span class="muted">· ${t16(a.as_of_utc)} UTC</span></div>
    ${a.status ? `<div class="note">${esc(a.status)}</div>` : `<div class="grid g5" style="margin-top:6px">
    ${card("账户权益 USDT", fmt(a.equity_usdt), "本金 " + fmt(a.initial_usdt, 0))}${card("今日变化", (a.today_change_usdt >= 0 ? "+" : "") + fmt(a.today_change_usdt), "vs 前一 UTC 日末", sgn(a.today_change_usdt))}
    ${card("持仓市值", fmt(a.position_value_usdt), `BTC ${pct(a.weights.BTC, 0)} · ETH ${pct(a.weights.ETH, 0)}`)}${card("现金 USDT", fmt(a.cash_usdt), pct(a.weights.USDT, 0))}${card("预估清仓净利润", (a.liquidation_net_profit_usdt >= 0 ? "+" : "") + fmt(a.liquidation_net_profit_usdt), "已扣假设卖出成本 " + fmt(a.exit_cost_if_liquidated_usdt), sgn(a.liquidation_net_profit_usdt))}
    ${card("已实现（净）", fmt(a.realized_net_usdt), "含买入手续费扣除")}${card("未实现", fmt(a.unrealized_usdt), "按最新已收盘 1h 标记")}${card("累计手续费+滑点", fmt((a.fees_paid_usdt || 0) + (a.slippage_cost_usdt || 0)), `费 ${fmt(a.fees_paid_usdt)} / 滑 ${fmt(a.slippage_cost_usdt)}`)}${card("最大回撤", pct(a.max_drawdown), `${a.longest_drawdown_days ?? "—"} 日 · 状态 ${a.risk_state || "—"}`, "down")}${card("成交 / 决策", `${a.n_fills}`, `nav ${a.n_nav} · 账本${a.nav_consistent_with_replay ? "一致" : "不一致"}`)}
    </div>`}</div>`;
  m.innerHTML = `<h1>投资总览</h1><div class="note">${esc(o.note)}。${tag("rl")}</div>
  <div class="grid g4">${card("BTC 趋势", (o.trend.BTCUSDT.trend || "—").replace(/（.*/, ""), `收盘 ${fmt(o.trend.BTCUSDT.close, 0)} · SMA200 ${fmt(o.trend.BTCUSDT.sma200, 0)}${o.trend.BTCUSDT.sma200_rising ? " ↑" : ""}`)}${card("ETH 趋势", (o.trend.ETHUSDT.trend || "—").replace(/（.*/, ""), `收盘 ${fmt(o.trend.ETHUSDT.close, 0)} · SMA200 ${fmt(o.trend.ETHUSDT.sma200, 0)}${o.trend.ETHUSDT.sma200_rising ? " ↑" : ""}`)}
  ${card("72 小时验收", esc(o.acceptance.banner), `覆盖率 ${pct(o.acceptance.local.coverage, 1)} · 最长中断 ${o.acceptance.local.longest_gap_hours ?? "—"} h`, o.acceptance.passed ? "up" : "")}${card("行情新鲜度", `${o.data.bar_age_hours.BTCUSDT_1h_h ?? "—"} h`, "最新已收盘 1h 的年龄（服务侧缓存）")}</div>
  <h2>原 Mac 模拟账户 ${tag("mac")}</h2><div class="grid g2">${accs("mac_prod").map(accCards).join("") || "<p class='muted'>无记录</p>"}</div>
  <h2>第十三阶段影子账户 ${tag("fw")}</h2><div class="grid g2">${accs("shadow13").map(accCards).join("") || "<p class='muted'>未启动</p>"}</div>
  <div class="footer">数据：各账户 events.jsonl / state.json 原子读取；实时价来自 OKX ticker（失败回退最新已收盘 1h 并标注）。真实交易 API 禁用，本页只读。</div>`;
};

P.compare = async (m) => {
  const c = await api("/api/compare"); const rows = c.rows;
  const row = (a) => a.status ? `<tr><td>${groupTag(a.group)} ${esc(a.name)}</td><td colspan="12" class="muted">${esc(a.status)}</td></tr>` : `<tr><td>${groupTag(a.group)} ${esc(a.name)}</td><td>${t16(a.forward_start_utc)}</td><td>${fmt(a.initial_usdt, 0)}</td><td>${fmt(a.equity_usdt)}</td><td class="${sgn(a.liquidation_net_profit_usdt)}">${fmt(a.liquidation_net_profit_usdt)}</td><td class="${sgn(a.return)}">${pct(a.return)}</td><td class="down">${pct(a.max_drawdown)}</td><td>${a.n_fills}</td><td>${a.avg_hold_days_closed == null ? (a.open_position_days == null ? "—" : fmt(a.open_position_days, 1) + "（在持）") : fmt(a.avg_hold_days_closed, 1)}</td><td>${fmt(a.fees_paid_usdt)} / ${fmt(a.slippage_cost_usdt)}</td><td>${pct(a.weights.BTC, 0)} / ${pct(a.weights.ETH, 0)}</td><td>${pct(a.weights.USDT, 0)}</td><td class="${sgn(a.vs_buyhold.BTCUSDT)}">${fmt(a.vs_buyhold.BTCUSDT)}</td><td class="${sgn(a.vs_buyhold.ETHUSDT)}">${fmt(a.vs_buyhold.ETHUSDT)}</td></tr>`;
  const sec = (g, title) => `<h2>${title}</h2><div class="tbl"><table><thead><tr><th>账户</th><th>起点 UTC</th><th>本金</th><th>当前净资产</th><th>清仓净利润</th><th>收益率</th><th>最大回撤</th><th>成交</th><th>平均持仓(日)</th><th>手续费 / 滑点</th><th>BTC / ETH</th><th>USDT</th><th>vs BTC 持有</th><th>vs ETH 持有</th></tr></thead><tbody>${rows.filter((r) => r.group === g).map(row).join("")}</tbody></table></div>`;
  m.innerHTML = `<h1>策略对比</h1><div class="note">${esc(c.comparability.within_group)}；${esc(c.comparability.across_groups || "")}；${esc(c.comparability.vs_buyhold)}。清仓净利润 = 权益 − 本金 − 假设卖出成本（15 bps 单侧）。</div>${sec("mac_prod", "原 Mac 模拟账户 " + tag("mac"))}${sec("shadow13", "第十三阶段影子账户 " + tag("fw") + " <span class='muted small'>与上表起点不同：不可直接比较</span>")}
  <h2>净资产曲线与回撤</h2><div class="controls"><select id="cmpsel"><option value="mac_prod">原 Mac 账户</option><option value="shadow13">影子账户</option></select></div><div id="cmp-eq" class="plot"></div><div id="cmp-dd" class="plot"></div>`;
  const draw = async (g) => { const tr = [], dd = []; for (const a of rows.filter((r) => r.group === g)) { const n = await api(`/api/accounts/${a.group}/${a.name}/nav`); const x = n.points.map((p) => p.ts); tr.push({ x, y: n.points.map((p) => p.liquidation), name: a.name, mode: "lines" }); dd.push({ x, y: n.points.map((p) => p.drawdown * 100), name: a.name, mode: "lines" }); } Plotly.newPlot("cmp-eq", tr, { title: "清仓口径净值（USDT）", margin: { t: 40, l: 60, r: 20, b: 40 }, paper_bgcolor: "#171e2e", plot_bgcolor: "#171e2e", font: { color: "#e6ebf5" }, legend: { orientation: "h" } }, { responsive: true, displaylogo: false }); Plotly.newPlot("cmp-dd", dd, { title: "回撤（%）", margin: { t: 40, l: 60, r: 20, b: 40 }, paper_bgcolor: "#171e2e", plot_bgcolor: "#171e2e", font: { color: "#e6ebf5" }, legend: { orientation: "h" } }, { responsive: true, displaylogo: false }); };
  $("#cmpsel").onchange = (e) => draw(e.target.value); draw("mac_prod");
};

let chartState = { symbol: "BTCUSDT", tf: "1d", asOf: null, acct: "" };
P.charts = async (m) => {
  m.innerHTML = `<h1>K 线与价格行为分析</h1>
  <div class="controls"><select id="csym"><option>BTCUSDT</option><option>ETHUSDT</option></select><select id="ctf"><option value="1h">1 小时</option><option value="4h">4 小时</option><option value="1d" selected>日线</option><option value="1w">周线</option><option value="1M">月线</option></select>
  <label class="small">结构截至 <input type="date" id="casof"></label><select id="cacct"><option value="">不叠加交易</option></select><label class="small"><input type="checkbox" id="chind"> 显示事后结构</label><button class="primary" id="cgo">刷新</button></div>
  <div class="legend"><span><i style="background:#4c9aff"></i>SMA50</span><span><i style="background:#f5b942"></i>SMA100</span><span><i style="background:#f0556a"></i>SMA200</span><span><i style="background:rgba(39,194,129,.5)"></i>已确认支撑区（当时）</span><span><i style="background:rgba(240,85,106,.5)"></i>已确认阻力区（当时）</span><span><i style="background:rgba(155,123,255,.5)"></i>事后结构（虚）</span><span>▲ 模拟买入 ▼ 模拟卖出（点击行查看依据）</span></div>
  <div id="kchart"></div><div id="cinfo" class="small muted" style="margin-top:6px"></div><h3>该区间模拟成交（当时已确认信号）</h3><div class="tbl" id="ctrades"></div>`;
  const accs = (await api("/api/accounts")).accounts; const sel = $("#cacct"); for (const a of accs) { const o = document.createElement("option"); o.value = a.group + "/" + a.name; o.textContent = a.name; sel.appendChild(o); }
  $("#csym").value = chartState.symbol; $("#ctf").value = chartState.tf; if (chartState.asOf) $("#casof").value = chartState.asOf; sel.value = chartState.acct;
  const render = async () => {
    chartState = { symbol: $("#csym").value, tf: $("#ctf").value, asOf: $("#casof").value || null, acct: sel.value }; const { symbol, tf, asOf } = chartState;
    const b = await api(`/api/market/bars?symbol=${symbol}&tf=${tf}&limit=600`); const st = await api(`/api/market/structure?symbol=${symbol}${asOf ? "&as_of=" + asOf : ""}`);
    const el = $("#kchart"); el.innerHTML = ""; const ch = LightweightCharts.createChart(el, { layout: { background: { color: "#171e2e" }, textColor: "#8b98b5" }, grid: { vertLines: { color: "#222c40" }, horzLines: { color: "#222c40" } }, rightPriceScale: { borderColor: "#2a354d" }, timeScale: { borderColor: "#2a354d", timeVisible: tf === "1h" || tf === "4h" }, autoSize: true });
    const cs = ch.addCandlestickSeries({ upColor: "#27c281", downColor: "#f0556a", borderVisible: false, wickUpColor: "#27c281", wickDownColor: "#f0556a" }); cs.setData(b.bars.map((x) => ({ time: x.time, open: x.open, high: x.high, low: x.low, close: x.close })));
    const vol = ch.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol" }); ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } }); vol.setData(b.bars.map((x) => ({ time: x.time, value: x.volume, color: x.close >= x.open ? "rgba(39,194,129,.35)" : "rgba(240,85,106,.35)" })));
    for (const [k, col] of [["sma50", "#4c9aff"], ["sma100", "#f5b942"], ["sma200", "#f0556a"]]) { const ls = ch.addLineSeries({ color: col, lineWidth: 1, priceLineVisible: false, lastValueVisible: false }); ls.setData(b.bars.filter((x) => x[k] != null).map((x) => ({ time: x.time, value: x[k] }))); }
    const atr = ch.addLineSeries({ color: "rgba(139,152,181,.5)", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }); atr.setData(b.bars.filter((x) => x.atr14 != null).map((x) => ({ time: x.time, value: x.close + 2 * x.atr14 }))); const atr2 = ch.addLineSeries({ color: "rgba(139,152,181,.5)", lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false }); atr2.setData(b.bars.filter((x) => x.atr14 != null).map((x) => ({ time: x.time, value: x.close - 2 * x.atr14 })));
    const t0 = b.bars.length ? b.bars[0].time : 0, t1 = b.bars.length ? b.bars[b.bars.length - 1].time : 0;
    const drawZones = (pack, style) => { const near = pack.zones.filter((z) => z.high > pack.close * 0.7 && z.low < pack.close * 1.35).sort((a, b2) => Math.abs((a.low + a.high) / 2 - pack.close) - Math.abs((b2.low + b2.high) / 2 - pack.close)).slice(0, 8); for (const z of near) { const col = z.kind === "support" ? "rgba(39,194,129" : z.kind === "resistance" ? "rgba(240,85,106" : "rgba(245,185,66"; for (const [p, w] of [[z.low, 1], [z.high, 1]]) { const ls = ch.addLineSeries({ color: style === "hind" ? "rgba(155,123,255,.7)" : col + ",.7)", lineWidth: w, lineStyle: style === "hind" ? 3 : 0, priceLineVisible: false, lastValueVisible: false }); ls.setData([{ time: t0, value: p }, { time: t1, value: p }]); } } for (const sw of pack.swings) { if (sw.time >= t0 && sw.time <= t1) cs.createPriceLine && null; } };
    const packNow = st.confirmed_at_time; drawZones(packNow, "now"); if ($("#chind").checked && st.hindsight) drawZones(st.hindsight, "hind");
    const markers = packNow.swings.filter((s) => s.time >= t0 && s.time <= t1).map((s) => ({ time: s.time, position: s.kind === "H" ? "aboveBar" : "belowBar", color: "#8b98b5", shape: "circle", size: 0.5, text: "" }));
    if (packNow.trend_line) { const tl = ch.addLineSeries({ color: packNow.trend_line.kind === "up" ? "#27c281" : "#f0556a", lineWidth: 1, priceLineVisible: false, lastValueVisible: false }); tl.setData([{ time: packNow.trend_line.from.time, value: packNow.trend_line.from.price }, { time: packNow.trend_line.to.time, value: packNow.trend_line.to.price }]); }
    let trs = []; if (chartState.acct) { const [g, n] = chartState.acct.split("/"); const t = await api(`/api/trades?group=${g}&name=${n}&symbol=${symbol}&limit=2000&sort=asc`); trs = t.rows; for (const r of trs) { const tm = Math.floor(new Date(r.execution_time).getTime() / 1000); markers.push({ time: tf === "1h" ? tm - (tm % 3600) : tf === "4h" ? tm - (tm % 14400) : tm - (tm % 86400), position: r.side === "BUY" ? "belowBar" : "aboveBar", color: r.side === "BUY" ? "#27c281" : "#f0556a", shape: r.side === "BUY" ? "arrowUp" : "arrowDown", text: r.side + " " + fmt(r.fill_price, 0) }); } }
    markers.sort((a, b2) => a.time - b2.time); cs.setMarkers(markers); ch.timeScale().fitContent();
    $("#cinfo").innerHTML = `来源 ${esc(b.source)} · 最新已收盘 ${t16(b.last_closed_utc)} UTC · 结构截至 <b>${packNow.as_of_day}</b>（${esc(packNow.label)}）：收盘 ${fmt(packNow.close, 0)}，SMA200 ${fmt(packNow.sma200, 0)}，ATR14 ${fmt(packNow.atr14, 0)}，HH ${packNow.hh ? "是" : "否"} / HL ${packNow.hl ? "是" : "否"}，趋势线 ${esc(packNow.trend_line ? packNow.trend_line.direction : "无")}；区域 ${packNow.zones.length} 个（图上只画最接近现价的 8 个）${$("#chind").checked ? "；紫色虚线 = 事后观察（截至最新），不是当时可见" : ""}。摆动点与区域来自已确认分形（k=3），绝不提前显示。`;
    $("#ctrades").innerHTML = trs.length ? `<table><thead><tr><th>执行时间 UTC</th><th>方向</th><th>成交价</th><th>数量</th><th>手续费</th><th>状态</th><th>理由</th></tr></thead><tbody>${trs.map((r) => `<tr class="clickable" onclick="tradeDetail('${r.group}','${r.account}','${encodeURIComponent(r.order_id)}')"><td>${t16(r.execution_time)}</td><td class="${r.side === "BUY" ? "up" : "down"}">${r.side}</td><td>${fmt(r.fill_price)}</td><td>${fmt(r.qty, 6)}</td><td>${fmt(r.fee)}</td><td>${esc(r.status)}</td><td>${esc(r.signal_reason || r.reason)}</td></tr>`).join("")}</tbody></table>` : "<p class='muted small'>选择账户后显示该账户在此标的的模拟成交。</p>";
  };
  $("#cgo").onclick = render; $("#csym").onchange = render; $("#ctf").onchange = render; sel.onchange = render; $("#chind").onchange = render; render();
};

window.tradeDetail = async (g, n, oid) => {
  const d = await api(`/api/trades/${g}/${n}/${oid}`); const f = d.events.find((e) => e.kind === "fill") || {}; const o = d.events.find((e) => e.kind === "order") || {}; const sig = d.decision_context.find((e) => e.kind === "signal" || e.kind === "missed_signal") || {}; const risk = d.decision_context.find((e) => e.kind === "risk") || {}; const s = d.structure_at_decision || {};
  openModal("交易决策依据 · " + decodeURIComponent(oid), `<div class="grid g3">${card("信号日线收盘", t16(sig.signal_ts), "决策 " + t16(sig.decision_time) + " UTC")}${card("决策", esc(sig.signal || "—"), esc(sig.reason || ""))}${card("风险状态", esc(risk.state || "—"), `上限 ${pct(risk.max_exposure, 0)} · 回撤 ${pct(risk.dd_ref)}`)}${card("exec_after", t16(o.exec_after), "执行时隙（决策后首个整点之后）")}${card("成交", `${esc(f.side || "")} ${fmt(f.execution_price)}`, `${esc(f.price_kind || "closed-bar")} · ${t16(f.execution_time)} · ${f.estimated ? "估算价" : "报价"}`)}${card("费用", fmt(f.fee), `状态 ${esc(f.order_status || "")}`)}</div>
  <h3>当时已确认的结构（截至 ${esc(s.as_of_day || "—")}）${tag("rs")}</h3><p class="small">收盘 ${fmt(s.close, 0)} · SMA200 ${fmt(s.sma200, 0)} · ATR14 ${fmt(s.atr14, 0)} · 最近已确认高点 ${fmt(s.last_high, 0)} / 低点 ${fmt(s.last_low, 0)} · 区域：${(s.zones || []).map((z) => `[${fmt(z.low, 0)}, ${fmt(z.high, 0)}] ${z.kind} 强度 ${z.strength}`).join("；") || "无"}</p>
  <p class="small muted">事后观察（截至最新）的结构不在此显示为决策依据；可在 K 线页勾选“显示事后结构”对比。</p><h3>关联事件（事件链原文，含哈希）</h3><pre>${esc(JSON.stringify(d.events.concat(d.decision_context, d.corrections), null, 1))}</pre>`);
};

P.opps = async (m) => {
  const o = await api("/api/opportunities");
  const sym = (s, x) => { const fr = x.frozen_strategies.map((f) => `<tr><td>${groupTag(f.group)} ${esc(f.account)}</td><td class="state ${f.state}">${f.state}</td><td>${esc(f.signal || "—")}</td><td>${t16(f.signal_ts)}</td><td>${t16(f.decision_time)}</td><td>${t16(f.valid_until_utc)}</td><td>${pct(f.target_weight, 0)}</td><td>${esc(f.risk_state || "—")}</td><td style="white-space:normal;text-align:left">${esc(f.reason || "")}</td></tr>`).join(""); const r = x.research || {}; const p = r.plan;
    return `<h2>${s} · 现价 ${fmt(x.price, s === "BTCUSDT" ? 1 : 2)} · ${esc(x.trend.trend || "")}${x.trend.sma200_rising ? "，SMA200 上行" : ""}</h2>
    <h3>冻结策略的最新决策（唯一可执行口径）</h3><div class="tbl"><table><thead><tr><th>账户</th><th>状态</th><th>信号</th><th>信号日线收盘</th><th>决策时间</th><th>有效期至</th><th>目标权重</th><th>风险状态</th><th>理由</th></tr></thead><tbody>${fr}</tbody></table></div>
    <h3>${tag("rs")} ${esc(r.label || "")}</h3><div class="grid g4">${card("状态", esc(r.state || "—"), "截至 " + esc(r.as_of_day || "—"))}${card("主要支撑区", r.support_zone ? `${fmt(r.support_zone[0], 0)}–${fmt(r.support_zone[1], 0)}` : "无", r.support_zone ? "强度 " + r.support_zone[2] : "")}${card("结构失效价（研究）", fmt(r.structure_invalidation, 0), "最近已确认摆动低点 − 0.25 ATR")}${card("量能 / 收盘位置", `${fmt(r.volume_confirmation && r.volume_confirmation.vol_rel20, 2)} / ${fmt(r.volume_confirmation && r.volume_confirmation.close_pos, 2)}`, "量比 20 日均量 / 收盘在振幅中的位置")}
    ${card("回调确认", r.pullback && r.pullback.signal ? "触发" : "未触发", esc(r.pullback && r.pullback.reason))}${card("突破回踩", r.breakout_retest && r.breakout_retest.signal ? "触发" : "未触发", esc(r.breakout_retest && r.breakout_retest.reason))}${card("候选持仓周期", esc(r.candidate_holding_period || "—"), `预计往返成本 ${r.est_cost_round_trip_bps || "—"} bps`)}${p ? card("事前计划", p.ok ? "可形成" : "证据不足", `入场 ${fmt(p.entry_ref, 0)} · 失效 ${fmt(p.invalidation, 0)} · T1 ${fmt(p.target_1, 0)} · T2 ${fmt(p.target_2, 0)} · 净盈亏比 ${fmt(p.expected_net_reward_risk, 2)}`) : card("事前计划", "WAIT", "无候选触发 → 不构造计划")}</div><p class="small muted">${esc(r.evidence_note || "")}</p>`; };
  m.innerHTML = `<h1>买入机会监控</h1><div class="note">${esc(o.note)}。生成 ${t16(o.generated_at_utc)} UTC。没有证据时显示 WAIT / 证据不足，不制造信号。</div>${sym("BTCUSDT", o.symbols.BTCUSDT)}${sym("ETHUSDT", o.symbols.ETHUSDT)}`;
};

P.positions = async (m) => {
  const accs = (await api("/api/accounts")).accounts; let html = `<h1>卖出目标与持仓管理</h1><div class="note">目标价只来自已确认阻力区（研究性分析）；策略本身的退出规则是 SMA200 / 熔断状态机 / 波动跟踪。无可靠目标时显示“尚无可信目标”。不提供保证盈利的目标价。</div>`;
  for (const a of accs) { const p = await api(`/api/accounts/${a.group}/${a.name}/positions`); html += `<h2>${groupTag(a.group)} ${esc(a.label)} <span class="muted small">· 现金 ${fmt(p.cash_usdt)} · 权益 ${fmt(p.equity_usdt)} · 已实现 ${fmt(p.realized_net_usdt)} · 未实现 ${fmt(p.unrealized_usdt)} · ${t16(p.as_of_utc)} UTC</span></h2>`;
    if (!p.positions.length) { html += "<p class='muted small'>空仓（100% USDT）</p>"; continue; }
    html += `<div class="tbl"><table><thead><tr><th>标的</th><th>建仓时间</th><th>建仓价</th><th>数量</th><th>标记价 / 实时</th><th>市值</th><th>未实现</th><th>持仓天数</th><th>策略退出条件</th><th>结构失效价（研究）</th><th>第一 / 第二卖出区（研究）</th><th>跟踪退出</th><th>扣卖出成本估算净值</th></tr></thead><tbody>${p.positions.map((x) => `<tr><td>${x.symbol}</td><td>${t16(x.entry_ts)}</td><td>${fmt(x.entry_price)}</td><td>${fmt(x.qty, 6)}</td><td>${fmt(x.mark_price)} / ${fmt(x.live_price)}</td><td>${fmt(x.market_value_usdt)}</td><td class="${sgn(x.unrealized_usdt)}">${fmt(x.unrealized_usdt)}（${pct(x.unrealized_pct)}）</td><td>${fmt(x.holding_days, 1)}</td><td style="white-space:normal;text-align:left">${esc(x.exit_conditions.strategy_exit)}${x.exit_conditions.state_machine && x.exit_conditions.state_machine !== "无（此账户无熔断）" ? "；熔断 " + esc(x.exit_conditions.state_machine) : ""}${x.exit_conditions.buyhold ? "；" + esc(x.exit_conditions.buyhold) : ""}</td><td>${fmt(x.structure_invalidation_research.last_confirmed_swing_low, 0)}</td><td>${x.candidate_sell_zones_research ? x.candidate_sell_zones_research.map((z) => `${fmt(z.low, 0)}–${fmt(z.high, 0)}（强度 ${z.strength}）`).join(" / ") : "尚无可信目标"}</td><td>${esc(x.exit_conditions.vol_trail || "—")}</td><td>${fmt(x.est_net_if_sold_usdt)}</td></tr>`).join("")}</tbody></table></div>`; }
  m.innerHTML = html;
};

P.analytics = async (m) => {
  const accs = (await api("/api/accounts")).accounts;
  m.innerHTML = `<h1>收益与风险分析中心</h1><div class="controls"><select id="ansel">${accs.map((a) => `<option value="${a.group}/${a.name}">${a.group === "mac_prod" ? "[Mac] " : "[影子] "}${a.name}</option>`).join("")}</select><input type="date" id="an0"><input type="date" id="an1"><button class="primary" id="ango">分析</button></div><div id="anbody"></div>
  <h2>${tag("bt")} 历史研究区间（第十阶段 / 第十二阶段，只读引用，与前瞻严格分开）</h2><div id="anres" class="small muted">加载中…</div>`;
  const run = async () => { const [g, n] = $("#ansel").value.split("/"); const q = new URLSearchParams(); if ($("#an0").value) q.set("start", $("#an0").value); if ($("#an1").value) q.set("end", $("#an1").value); const a = await api(`/api/accounts/${g}/${n}/analytics?` + q);
    if (a.status) { $("#anbody").innerHTML = `<p class="muted">${esc(a.status)}</p>`; return; }
    $("#anbody").innerHTML = `<div class="note">${tag("fw")} ${esc(a.category)} · 区间 ${t16(a.window.start)} → ${t16(a.window.end)}（${a.window.days} 日）· ${esc(a.evidence)}</div>
    <div class="grid g5">${card("累计净利润", fmt(a.cum_profit_usdt), "权益口径", sgn(a.cum_profit_usdt))}${card("最大回撤", pct(a.max_drawdown), `${a.longest_drawdown_days ?? "—"} 日`, "down")}${card("资金使用率", pct(a.avg_exposure), `空仓时间 ${pct(a.time_flat)}`)}${card("手续费 / 滑点", `${fmt(a.costs.fees_usdt)} / ${fmt(a.costs.slippage_usdt)}`, `假设清仓再付 ${fmt(a.costs.exit_cost_if_liquidated_usdt)}`)}${card("闭合交易", `${a.n_sells}`, `平均持仓 ${a.hold_days_closed_avg == null ? "—" : fmt(a.hold_days_closed_avg, 1) + " 日"} · 最大 5 笔占比 ${pct(a.top5_share, 0)}`)}</div>
    <div class="grid g2"><div id="an-period" class="plot"></div><div id="an-dist" class="plot"></div></div>
    <h3>与买入持有比较（同起点、同本金、同成本估算）</h3><div class="grid g2">${["BTCUSDT", "ETHUSDT"].map((s) => { const v = a.vs_buyhold[s]; return v.comparable ? card("vs " + s.slice(0, 3) + " 买入持有", (v.excess_usdt >= 0 ? "+" : "") + fmt(v.excess_usdt), `账户清仓净利润 ${fmt(v.account_liq_net)} · 持有 ${fmt(v.bh_net)}`, sgn(v.excess_usdt)) : card("vs " + s.slice(0, 3), "不可直接比较", esc(v.note)); }).join("")}</div>
    <h3>市场环境</h3><p class="small">${typeof a.regimes === "string" ? esc(a.regimes) : Object.entries(a.regimes).map(([k, v]) => `${k}: ${v.months} 月，盈亏 ${fmt(v.pnl_usdt)}`).join(" · ")}</p>`;
    const lay = (t) => ({ title: t, margin: { t: 36, l: 60, r: 20, b: 40 }, paper_bgcolor: "#171e2e", plot_bgcolor: "#171e2e", font: { color: "#e6ebf5" }, legend: { orientation: "h" } });
    Plotly.newPlot("an-period", [{ x: a.daily.map((d) => d.period), y: a.daily.map((d) => d.change), type: "bar", name: "日变化" }, { x: a.weekly.map((d) => d.period), y: a.weekly.map((d) => d.change), type: "bar", name: "周变化", visible: "legendonly" }, { x: a.monthly.map((d) => d.period), y: a.monthly.map((d) => d.change), type: "bar", name: "月变化", visible: "legendonly" }], lay("净资产变化（USDT）"), { responsive: true, displaylogo: false });
    Plotly.newPlot("an-dist", [{ x: a.trade_pnl_distribution, type: "histogram", name: "单笔卖出净利润" }], lay("单笔交易收益分布（USDT）" + (a.n_sells < 10 ? " · 样本不足" : "")), { responsive: true, displaylogo: false }); };
  $("#ango").onclick = run; $("#ansel").onchange = run; run();
  try { const r = await api("/api/research"); const rows = (r.phase12 && r.phase12.rows) || []; $("#anres").innerHTML = `<p>${esc(r.category)}。第十二阶段全样本（${esc(r.phase12 ? r.phase12.window.join(" → ") : "")}，单侧 25 bps）：</p><div class="tbl"><table><thead><tr><th>组</th><th>期末净值</th><th>清仓净收益</th><th>最大回撤</th><th>最长回撤(日)</th><th>成交</th><th>总成本</th><th>核心暴露</th><th>空仓</th></tr></thead><tbody>${rows.map((x) => `<tr><td>${esc(x.group)}</td><td>${fmt(x.final_equity, 0)}</td><td>${fmt(x.liquidation_net_profit_usdt, 0)}</td><td>${pct(x.max_drawdown, 1)}</td><td>${x.longest_drawdown_days}</td><td>${x.n_fills}</td><td>${fmt(x.total_cost_usdt, 0)}</td><td>${pct(x.avg_core_exposure, 0)}</td><td>${pct(x.time_flat, 0)}</td></tr>`).join("")}</tbody></table></div><p class="small muted">历史回测结果不用于填充任何前瞻业绩。</p>`; } catch (e) { $("#anres").textContent = "历史研究结果不可用：" + e.message; }
};

P.health = async (m) => {
  const h = await api("/api/health"); const s = h.mac_service, a = h.acceptance, l = a.local || {};
  m.innerHTML = `<h1>72 小时验收与服务健康</h1><div class="note">本页只读取 health.json / runner_events / acceptance 规则结果，不重置计时、不改日志、不补轮询。${h.mac_service.host_is_cloud_only ? "<b>注意：当前读取的是云端副本（主机 vm），不是 Mac 的权威记录。</b>" : ""}</div>
  <div class="grid g4">${card("Mac 后台服务", s.alive_by_heartbeat ? "运行中" : "无新鲜心跳", `最后心跳 ${t16(s.heartbeat_utc)} UTC（${s.heartbeat_age_s == null ? "—" : Math.round(s.heartbeat_age_s / 60) + " 分钟前"}）`, s.alive_by_heartbeat ? "up" : "down")}${card("本进程轮询", `${s.n_cycles_since_start ?? "—"}`, `连续失败 ${s.consecutive_failures ?? "—"} · 周期 ${s.interval_s ?? "—"} s · 主机 ${(s.hosts_seen || []).join(",") || "—"}`)}${card("72 小时验收", esc(a.banner), `起点 ${esc(a.local_start)} UTC`, a.passed ? "up" : "")}${card("真实交易 API", h.real_trading.live_trading_disabled ? "禁用" : "异常", esc(h.real_trading.detail), h.real_trading.live_trading_disabled ? "up" : "down")}
  ${card("本地段时长", `${l.elapsed_hours ?? "—"} h`, `${t16(l.start_utc)} → ${t16(l.end_utc)}`)}${card("实际 / 预计轮询", `${l.cycles_observed ?? "—"} / ${l.cycles_expected ?? "—"}`, "")}${card("覆盖率", pct(l.coverage, 1), "门槛 ≥ 95%", l.coverage >= 0.95 ? "up" : "down")}${card("最长中断", `${l.longest_gap_hours ?? "—"} h`, "门槛 ≤ 2 h", l.longest_gap_hours <= 2 ? "up" : "down")}
  ${card("数据完整率", pct(l.data_ok_share, 1), `问题 ${l.n_data_issues ?? "—"} · 异常 ${l.n_anomalies ?? "—"}`)}${card("数据延迟", `1h ${h.data.bar_age_hours.BTCUSDT_1h_h ?? "—"} h · 1d ${h.data.bar_age_hours.BTCUSDT_1d_h ?? "—"} h`, "最新已收盘 K 线年龄")}${card("错过 / 过期 / 暂停", `${h.signals.missed_signals} / ${h.signals.expired_orders} / ${h.signals.fill_paused}`, "原 Mac 三账户合计")}${card("告警（按类型）", esc(JSON.stringify(l.alerts_by_kind || {})), `最近 ${h.alerts_recent.length} 条见下`)}</div>
  <p class="small muted">${esc(a.rules)}；${esc(l.verdict_note || "")}</p><h3>云端历史段（不计入本地验收）</h3><pre>${esc(JSON.stringify(a.cloud_legacy, null, 1))}</pre><h3>最近告警</h3><pre>${esc(JSON.stringify(h.alerts_recent.slice(-10), null, 1))}</pre><h3>启动自检 / 数据状态</h3><pre>${esc(JSON.stringify({ startup_check: h.data.startup_check, data_status: h.data.data_status, shadow13_service: h.shadow13_service }, null, 1))}</pre>`;
};

P.trades = async (m) => {
  const accs = (await api("/api/accounts")).accounts;
  m.innerHTML = `<h1>交易记录与审计</h1><div class="note">全部来自真实记录的模拟事件链（哈希链），不生成任何交易。点击行查看关联事件与当时决策依据。</div>
  <div class="controls"><select id="tacc"><option value="">全部账户</option>${accs.map((a) => `<option value="${a.group}/${a.name}">${a.name}</option>`).join("")}</select><select id="tsym"><option value="">BTC+ETH</option><option>BTCUSDT</option><option>ETHUSDT</option></select><select id="tside"><option value="">买卖</option><option>BUY</option><option>SELL</option></select><input type="date" id="t0"><input type="date" id="t1"><input id="tq" placeholder="搜索（订单号/理由/状态）"><select id="tsort"><option value="desc">时间降序</option><option value="asc">时间升序</option></select><button class="primary" id="tgo">查询</button><button id="tcsv">导出 CSV</button></div><div id="tsum" class="small muted"></div><div class="tbl" id="ttbl"></div><div class="controls"><button id="tprev">上一页</button><span id="tpage" class="small"></span><button id="tnext">下一页</button></div>`;
  let offset = 0; const limit = 100;
  const qs = () => { const q = new URLSearchParams(); const [g, n] = ($("#tacc").value || "/").split("/"); if (g) q.set("group", g); if (n) q.set("name", n); if ($("#tsym").value) q.set("symbol", $("#tsym").value); if ($("#tside").value) q.set("side", $("#tside").value); if ($("#t0").value) q.set("start", $("#t0").value); if ($("#t1").value) q.set("end", $("#t1").value); if ($("#tq").value) q.set("q", $("#tq").value); q.set("sort", $("#tsort").value); return q; };
  const load = async () => { const q = qs(); q.set("limit", limit); q.set("offset", offset); const t = await api("/api/trades?" + q); $("#tsum").textContent = `共 ${t.total} 笔（cost 账本）`; $("#tpage").textContent = `${offset + 1}–${Math.min(offset + limit, t.total)} / ${t.total}`;
    $("#ttbl").innerHTML = `<table><thead><tr><th>账户</th><th>标的</th><th>方向</th><th>信号日线收盘</th><th>决策</th><th>模拟执行</th><th>成交价</th><th>数量</th><th>手续费</th><th>滑点</th><th>本笔利润</th><th>状态</th><th>价格类型</th><th>理由</th></tr></thead><tbody>${t.rows.map((r) => `<tr class="clickable" onclick="tradeDetail('${r.group}','${r.account}','${encodeURIComponent(r.order_id)}')"><td>${groupTag(r.group)}${esc(r.account)}</td><td>${r.symbol}</td><td class="${r.side === "BUY" ? "up" : "down"}">${r.side}</td><td>${t16(r.signal_ts)}</td><td>${t16(r.decision_time)}</td><td>${t16(r.execution_time)}</td><td>${fmt(r.fill_price)}</td><td>${fmt(r.qty, 6)}</td><td>${fmt(r.fee)}</td><td>${fmt(r.slippage_usdt)}</td><td class="${sgn(r.net_pnl)}">${r.net_pnl == null ? "—" : fmt(r.net_pnl)}</td><td>${esc(r.status)}</td><td>${esc(r.price_kind || "closed-bar")}${r.estimated ? "（估算）" : ""}</td><td style="white-space:normal;text-align:left;max-width:360px">${esc(r.signal_reason || r.reason)}</td></tr>`).join("")}</tbody></table>`; };
  $("#tgo").onclick = () => { offset = 0; load(); }; $("#tprev").onclick = () => { offset = Math.max(0, offset - limit); load(); }; $("#tnext").onclick = () => { offset += limit; load(); }; $("#tcsv").onclick = () => { window.location = "/api/trades.csv?" + qs(); }; load();
};

P.phase13 = async (m) => {
  const p = await api("/api/phase13"); const a = p.audit || {}; const f = p.freeze || {};
  const strat = f.strategies ? Object.entries(f.strategies).map(([k, v]) => `<tr><td>${k}</td><td style="white-space:normal;text-align:left">${esc(v.label)}</td><td>${esc(v.risk_mode)}</td><td>${v.vol_trail ? esc(JSON.stringify(v.vol_trail)) : "—"}</td></tr>`).join("") : "";
  const rows = a.accounts ? Object.entries(a.accounts).map(([n, x]) => `<tr><td>${n}</td><td>${t16(x.forward_start_utc)}</td><td>${x.elapsed_days}</td><td>${x.n_signals} / ${x.n_missed}</td><td>${x.n_fills}</td><td>${fmt(x.latest.liquidation_usdt)}</td><td>${pct(x.drawdown.max_drawdown)}</td><td>${["30d", "90d", "180d", "365d"].map((h) => esc(x.horizons[h].status)).join(" / ")}</td></tr>`).join("") : "";
  const cont = (a.service && a.service.continuity) || {};
  m.innerHTML = `<h1>前瞻验证中心（第十三阶段）${tag("fw")}</h1><div class="note">${esc(p.note)}</div>
  <div class="grid g4">${card("状态", esc(p.status), `主机 ${(p.hosts || []).join(",") || "—"}`, p.status === "运行中" ? "up" : "")}${card("冻结版本", f.git_commit ? f.git_commit.slice(0, 7) : "—", `冻结于 ${t16(f.frozen_at_utc)} UTC`)}${card("成本口径", f.costs ? `单侧 ${f.costs.cost_ledger_one_side_bps.total} bps` : "—", f.costs ? `费 ${f.costs.cost_ledger_one_side_bps.fee} + 滑 ${f.costs.cost_ledger_one_side_bps.slippage}` : "")}${card("数据覆盖率", pct(cont.coverage, 1), `最长中断 ${cont.longest_gap_hours ?? "—"} h · 轮询 ${cont.cycles_observed ?? "—"}/${cont.cycles_expected ?? "—"}`)}</div>
  <h2>六套冻结策略</h2><div class="tbl"><table><thead><tr><th>账户</th><th>规则</th><th>风险</th><th>附加</th></tr></thead><tbody>${strat}</tbody></table></div>
  <h2>前瞻进度</h2>${rows ? `<div class="tbl"><table><thead><tr><th>账户</th><th>真实起点 UTC</th><th>已运行(日)</th><th>信号 / 错过</th><th>模拟成交</th><th>当前清仓净值</th><th>当前回撤</th><th>30/90/180/365 天</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">${esc(a.status || "未启动")}</p>`}
  <p class="small muted">评价规则：30 天质量项；90 天描述性（闭合交易 < 10 笔 → 统计证据不足）；180 天风险收益；365 天跨环境。</p>`;
};

P.help = async (m) => {
  const meta = await api("/api/meta");
  m.innerHTML = `<h1>使用说明</h1><div class="card"><p><b>定位</b>：BTC/ETH 中长期投资研究、模拟交易监控、价格行为分析、收益审计与前瞻验证的<b>只读</b>平台。没有下单、提币、改参数、删记录、改本金、重置账户功能；服务端拒绝所有非 GET 请求；真实交易 API 禁用。</p>
  <p><b>三类数据严格分区</b>：${tag("mac")} 原三账户前瞻记录；${tag("fw")} 第十三阶段影子账户；${tag("bt")} 历史回测（只在“收益与风险”页底部引用）。${tag("rl")}</p>
  <p><b>启动 / 关闭</b>（Mac 本机，默认 127.0.0.1:8514）：<code>bash scripts/brando_dashboard14.sh start</code> / <code>stop</code> / <code>status</code> / <code>open</code>。iPhone 查看：同一 Wi‑Fi 下本阶段<b>不</b>开放，需后续认证方案；可先在 Mac 上用 Safari 响应式模式预览。</p>
  <p><b>数据路径</b>：<span class="mono">${esc(JSON.stringify(meta.paths))}</span>；缓存目录独立，不写任何账户文件。提交 <span class="mono">${esc((meta.git_commit || "").slice(0, 12))}</span>。</p>
  <p><b>口径</b>：权益 = 现金 + 持仓 × 最新已收盘 1h 收盘；清仓净利润 = 权益 − 本金 − 假设卖出成本（15 bps 单侧）；买入成本在成交时已扣一次，不重复；今日变化 = 相对前一 UTC 日末。</p>
  <p><b>K 线页</b>：摆动点 / 支撑阻力 / 趋势线只用“截至所选日期已确认”的分形（k=3），勾选“显示事后结构”才叠加最新视角（紫色虚线）。点击成交行查看当时的 signal / risk / market 事件原文与哈希。</p></div>`;
};

/* ---------- 路由 ---------- */
function route() {
  const h = (location.hash || "#overview").slice(1); const p = P[h] ? h : "overview";
  for (const nav of ["#sidenav", "#bottomnav"]) $(nav).innerHTML = PAGES.map(([k, t]) => `<a href="#${k}" class="${k === p ? "active" : ""}">${t}</a>`).join("");
  const m = $("#main"); m.innerHTML = "<p class='muted'>加载中…</p>"; P[p](m).catch((e) => { m.innerHTML = `<div class="note">页面加载失败：${esc(e.message)}</div>`; });
}
window.addEventListener("hashchange", route); route(); header(); setInterval(header, 60000); setInterval(() => { if ((location.hash || "#overview") === "#overview") route(); }, 300000);
