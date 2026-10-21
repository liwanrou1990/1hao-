import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Compare({ symbol, day }: { symbol: string; day: string }) {
  const [d, setD] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [mode, setMode] = useState<'exec' | 'theo'>('exec')
  const [sum, setSum] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setD(null); setErr(''); get(`/api/compare/${symbol}?day=${day}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day])
  useEffect(() => { setSum(null); get(`/api/compare-summary?day=${day}`).then(setSum).catch(() => {}) }, [day])
  if (err) return <div className="card neg">{err}</div>
  if (!d) return <div className="card">计算七档 A 理论最优 / B 可执行模拟（含参数网格）…</div>
  const t = d.tiers[Math.min(sel, d.tiers.length - 1)]
  const e = t.executable, a = t.analysis
  return <>
    <div className="banner"><b>A/B 为已知昨日完整走势的事后教学分析，不是实盘预测。</b> A 理论最优：单次全仓、不限次数、仅做多、不计流动性与冲击（纯上限）；B 可执行：多笔分批并行、共享流动性预算、含冲击/Gas/资金成本、止损 0.5%，参数事后在小网格挑选。{d.simulated && <b> 当前为 SIMULATED 数据，非正式验证。</b>}</div>
    <div className="card">
      <h2>{symbol} · {d.day} · 档位对比 <span className="tag">{d.source}</span>{d.fallback_native && <span className="tag neg">无代币K线，使用原生价格</span>}</h2>
      <div className="row">档位 <select value={sel} onChange={e2 => setSel(Number(e2.target.value))}>{d.tiers.map((x: any, i: number) => <option key={i} value={i}>{x.tier} · {num(x.capital_usd, 0)} USD · 目标 {num(x.target_usd, 0)}</option>)}</select>
        显示 <select value={mode} onChange={e2 => setMode(e2.target.value as any)}><option value="exec">B 可执行买卖点</option><option value="theo">A 理论最优买卖点</option></select>
        <span className="muted"><a href={d.chart_url} target="_blank">独立图表页</a></span></div>
      <div className="kpi" style={{ margin: '8px 0' }}>
        <div>目标利润<b>{num(t.target_usd)}</b>本金 {num(t.capital_usd, 0)}</div>
        <div>A 理论最优<b className={cls(t.theoretical.profit)}>{num(t.theoretical.profit)}</b>{t.theoretical.n_trades} 笔 · {t.theoretical.target_met ? '达标' : '未达标'}</div>
        <div>B 可执行净利润<b className={cls(e.realized_net_usd)}>{num(e.realized_net_usd)}</b>{pct(e.realized_net_return, 3)}</div>
        <div>B 笔数 多/空<b>{e.n_trades}</b>{e.n_long} / {e.n_short}{e.shortable ? '' : '（不可做空）'}</div>
        <div>并行仓位 / 单笔计划<b>{e.params.max_open}</b>{num(a.tranche_usd, 0)} USD · 最低净收益 {e.params.min_trade_net_pct}%</div>
        <div>资金利用率 峰/均<b>{pct(e.capital_utilization, 0)}</b>{pct(e.avg_utilization, 0)}</div>
        <div>流动性已用<b>{e.liquidity_used_ratio == null ? '未知' : pct(e.liquidity_used_ratio, 0)}</b>参与率预算</div>
        <div>往返成本 / 60根波动p90<b>{a.round_trip_cost_pct.toFixed(2)}%</b>{a.move_p90_pct.toFixed(2)}%（单笔 {num(a.size_ref_usd, 0)}）</div>
        <div>B 是否达标<b className={e.target_met ? 'pos' : 'neg'}>{e.target_met ? '达标' : '未达标'}</b>{e.target_hit_ts ? `@ ${e.target_hit_ts.slice(11, 16)}` : `缺口 ${num(a.gap_to_target_usd, 0)}`}</div>
      </div>
      {a.reasons.length > 0 && <div className="banner"><b>未达标原因：</b>{a.reasons.map((n: string, i: number) => <div key={i}>• {n}</div>)}</div>}
      <Chart candles={d.candles} markers={mode === 'exec' ? e.markers : t.theoretical.markers} />
      <div className="muted">B 累计已实现净利润（USD）</div>
      <Chart candles={[]} line={e.cum} height={150} />
      <div className="scroll"><table><thead><tr><th>#</th><th>方向</th><th>开仓</th><th>批</th><th>均价</th><th>平仓</th><th>批</th><th>均价</th><th>投入</th><th>数量</th><th>交易成本</th><th>资金成本</th><th>净盈亏</th><th>净收益</th><th>累计</th><th>最大不利</th><th>持有</th></tr></thead>
        <tbody>{e.trades.map((x: any) => <tr key={x.seq}><td>{x.seq}</td><td>{x.direction === 'long' ? '多' : '空'}</td><td>{x.open_ts.slice(11, 16)}</td><td>{x.open_fill.n_children}</td><td>{num(x.avg_open, 3)}</td><td>{x.close_ts.slice(11, 16)}</td><td>{x.close_fill.n_children}</td><td>{num(x.avg_close, 3)}</td><td>{num(x.size_usd, 0)}</td><td>{num(x.qty, 3)}</td><td>{num(x.trading_cost_usd)}</td><td>{num(x.capital_cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td>{num(x.cum_net_pnl_usd)}</td><td>{x.mae_pct.toFixed(2)}%</td><td>{x.hold_bars}</td></tr>)}
          {e.trades.length === 0 && <tr><td colSpan={17}>无可执行交易</td></tr>}</tbody></table></div>
      <div className="muted">参数网格（事后挑选）：{t.tried.map((x: any) => `并行${x.max_open}/最低${x.min_trade_net_pct}% → ${num(x.realized_net_usd, 0)} (${x.n_trades}笔${x.target_met ? '，达标' : ''})`).join('；')}</div>
    </div>
    <div className="card"><h2>全标的 × 七档 · A 理论 vs B 可执行 · {day}</h2>
      {!sum ? <div>计算中（含参数网格，约 1–3 分钟）…</div> : <div className="scroll"><table><thead><tr><th>标的</th><th>档位</th><th>本金</th><th>目标</th><th>A 理论</th><th>A 笔</th><th>B 净利润</th><th>B 收益率</th><th>B 笔(多/空)</th><th>并行</th><th>利用率峰/均</th><th>往返成本</th><th>波动p90</th><th>B 达标</th><th>缺口</th><th>原因</th></tr></thead>
        <tbody>{sum.rows.map((x: any, i: number) => <tr key={i}><td>{x.symbol}{!x.tradable && <span className="tag">待验证</span>}</td><td>{x.tier}</td><td>{num(x.capital_usd, 0)}</td><td>{num(x.target_usd, 0)}</td><td>{num(x.theoretical_usd, 0)}</td><td>{x.theoretical_trades}</td><td className={cls(x.executable_usd)}>{num(x.executable_usd)}</td><td>{x.executable_return_pct.toFixed(3)}%</td><td>{x.executable_trades} ({x.n_long}/{x.n_short})</td><td>{x.max_open}</td><td>{pct(x.peak_utilization, 0)}/{pct(x.avg_utilization, 0)}</td><td>{x.round_trip_cost_pct.toFixed(2)}%</td><td>{x.move_p90_pct.toFixed(2)}%</td><td className={x.target_met ? 'pos' : 'neg'}><b>{x.target_met ? '达标' : '未达标'}</b></td><td>{num(x.gap_to_target_usd, 0)}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{x.reasons}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
