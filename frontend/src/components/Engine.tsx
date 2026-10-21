import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Engine({ symbol, day }: { symbol: string; day: string }) {
  const [d, setD] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<'run' | 'theo'>('run')
  const [sum, setSum] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setD(null); setErr(''); get(`/api/engine/${symbol}?day=${day}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day])
  useEffect(() => { setSum(null); get(`/api/engine-summary?day=${day}`).then(setSum).catch(() => {}) }, [day])
  if (err) return <div className="card neg">{err}</div>
  if (!d) return <div className="card">决策引擎：七档滚动优化 + 昨日因果复盘（约 2–3 分钟）…</div>
  const t = d.tiers[Math.min(sel, d.tiers.length - 1)]
  const r = t.run, c = t.classification, o = t.oos, cap = d.capability
  return <>
    <div className="banner"><b>统一收益决策引擎（因果）</b>：每根K线收盘决策、下一根起分批成交，不使用未来信息；参数来自“昨日之前”的滚动优化。方向只在 API/人工核实支持的集合内选择（当前 {symbol}：{cap.directions.join('/') || '无'}，{cap.verified_by_api ? 'API 已核实' : '未经 API 核实'}）。目标收益不是保证收益。{d.simulated && <b> 当前为 SIMULATED 数据，非正式验证。</b>}</div>
    <div className="card">
      <h2>{symbol} · {d.day} · 决策引擎 <span className="tag">{d.source}</span></h2>
      <div className="row">档位 <select value={sel} onChange={e => setSel(Number(e.target.value))}>{d.tiers.map((x: any, i: number) => <option key={i} value={i}>{x.tier} · {num(x.capital_usd, 0)} USD · 目标 {num(x.target_usd, 0)}</option>)}</select>
        买卖点 <select value={mode} onChange={e => setMode(e.target.value as any)}><option value="run">引擎（因果）</option><option value="theo">A 理论最优（参照）</option></select>
        <span className="muted"><a href={d.chart_url} target="_blank">独立图表页</a></span></div>
      <div className="kpi" style={{ margin: '8px 0' }}>
        <div>目标收益<b>{num(t.target_usd)}</b>本金 {num(t.capital_usd, 0)}</div>
        <div>实际模拟净收益<b className={cls(r.realized_net_usd)}>{num(r.realized_net_usd)}</b>{pct(r.realized_net_return, 3)}</div>
        <div>交易次数<b>{r.n_trades}</b>多 {r.n_long} / 空 {r.n_short}</div>
        <div>胜率<b>{r.win_rate == null ? '—' : pct(r.win_rate, 0)}</b></div>
        <div>最大回撤<b className="neg">{num(r.max_drawdown_usd)}</b></div>
        <div>资金利用率<b>{pct(r.avg_deployed_usd / t.capital_usd, 0)}</b>峰值 {pct(r.peak_deployed_usd / t.capital_usd, 0)}</div>
        <div>执行成本 / 资金成本<b>{num(r.execution_cost_usd)}</b>{num(r.funding_cost_usd)}</div>
        <div>是否达标<b className={r.target_met ? 'pos' : 'neg'}>{r.target_met ? '达标' : '未达标'}</b>{r.target_hit_ts ? `@ ${r.target_hit_ts.slice(11, 16)}` : ''}</div>
        <div>A 理论参照<b>{num(t.theoretical.profit)}</b>{t.theoretical.n_trades} 笔</div>
        <div>样本外<b className={(o?.attainment_rate ?? 0) >= 0.5 ? 'pos' : 'neg'}>{o ? pct(o.attainment_rate, 0) : '—'}</b>{o ? `${o.n_days} 天 · 日均 ${pct(o.mean_daily_return, 3)} · 回撤 ${pct(o.max_drawdown_pct, 2)}` : '历史不足'}</div>
      </div>
      <div className="muted">参数 {JSON.stringify(r.params)} · 模式计数 {JSON.stringify(r.mode_counts)} · 暂停原因 {JSON.stringify(r.pause_reasons)}{r.stop_reason ? ` · ${r.stop_reason}` : ''}</div>
      {c.tags.length > 0 && <div className="banner"><b>原因归类：{c.tags.join('、')}</b>{c.details.map((n: string, i: number) => <div key={i}>• {n}</div>)}</div>}
      <Chart candles={d.candles} markers={mode === 'run' ? r.markers : t.theoretical.markers} />
      <div className="muted">累计已实现净利润（USD）</div>
      <Chart candles={[]} line={r.cum} height={150} />
      <div className="scroll"><table><thead><tr><th>#</th><th>方向</th><th>模式</th><th>开仓</th><th>均价</th><th>平仓</th><th>均价</th><th>投入</th><th>执行成本</th><th>资金成本</th><th>净盈亏</th><th>净收益</th><th>累计</th><th>持有</th><th>出场原因</th></tr></thead>
        <tbody>{r.trades.map((x: any) => <tr key={x.seq}><td>{x.seq}</td><td>{x.direction === 'long' ? '多' : '空'}</td><td>{x.mode}</td><td>{x.open_ts.slice(11, 16)}</td><td>{num(x.avg_open, 3)}</td><td>{x.close_ts.slice(11, 16)}</td><td>{num(x.avg_close, 3)}</td><td>{num(x.size_usd, 0)}</td><td>{num(x.trading_cost_usd)}</td><td>{num(x.funding_cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td>{num(x.cum_net_pnl_usd)}</td><td>{x.hold_bars}</td><td style={{ textAlign: 'left' }}>{x.reason_exit}</td></tr>)}
          {r.trades.length === 0 && <tr><td colSpan={15}>无交易（引擎暂停，见暂停原因）</td></tr>}</tbody></table></div>
      {o && <div className="scroll" style={{ marginTop: 8 }}><table><thead><tr><th>样本外日期</th><th>净利润</th><th>收益率</th><th>达标</th><th>笔数</th><th>多/空</th><th>胜率</th><th>回撤</th><th>利用率</th><th>停止原因</th></tr></thead>
        <tbody>{o.days.map((x: any) => <tr key={x.day}><td>{x.day}</td><td className={cls(x.net_usd)}>{num(x.net_usd)}</td><td>{pct(x.net_return, 3)}</td><td className={x.target_met ? 'pos' : 'neg'}>{x.target_met ? '达标' : '未达标'}</td><td>{x.n_trades}</td><td>{x.n_long}/{x.n_short}</td><td>{x.win_rate == null ? '—' : pct(x.win_rate, 0)}</td><td>{num(x.max_dd_usd, 0)}</td><td>{pct(x.util, 0)}</td><td style={{ textAlign: 'left' }}>{x.stop ?? ''}</td></tr>)}</tbody></table></div>}
    </div>
    <div className="card"><h2>档位总览（NVDA/SPCX）· {day}</h2>
      {!sum ? <div>计算中…</div> : <div className="scroll"><table><thead><tr><th>标的</th><th>档位</th><th>本金</th><th>方向</th><th>目标</th><th>A 理论</th><th>实际净收益</th><th>收益率</th><th>笔数</th><th>多/空</th><th>胜率</th><th>回撤</th><th>利用率</th><th>执行成本</th><th>达标</th><th>OOS 达标率</th><th>OOS 日均</th><th>原因</th></tr></thead>
        <tbody>{sum.rows.map((x: any, i: number) => <tr key={i}><td>{x.symbol}</td><td>{x.tier}</td><td>{num(x.capital_usd, 0)}</td><td>{x.directions}</td><td>{num(x.target_usd, 0)}</td><td>{num(x.theoretical_usd, 0)}</td><td className={cls(x.net_usd)}>{num(x.net_usd)}</td><td>{x.net_return_pct.toFixed(3)}%</td><td>{x.n_trades}</td><td>{x.n_long}/{x.n_short}</td><td>{x.win_rate == null ? '—' : pct(x.win_rate, 0)}</td><td>{num(x.max_dd_usd, 0)}</td><td>{pct(x.avg_utilization, 0)}</td><td>{num(x.exec_cost_usd)}</td><td className={x.target_met ? 'pos' : 'neg'}><b>{x.target_met ? '达标' : '未达标'}</b></td><td>{x.oos_attainment == null ? '—' : pct(x.oos_attainment, 0)}</td><td>{x.oos_mean_daily_return == null ? '—' : pct(x.oos_mean_daily_return, 3)}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{x.tags}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
