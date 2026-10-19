import { useEffect, useState } from 'react'
import { get, pct, num, cls } from '../api'

export default function Backtest({ symbol }: { symbol: string }) {
  const [bt, setBt] = useState<any>(null)
  const [att, setAtt] = useState<any>(null)
  const [cap, setCap] = useState<any>(null)
  const [stress, setStress] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    setBt(null); setAtt(null); setStress(null); setErr('')
    get(`/api/backtest/${symbol}`).then(setBt).catch(e => setErr(String(e)))
    get(`/api/attainment/${symbol}`).then(setAtt).catch(() => {})
    get(`/api/capacity?symbol=${symbol}`).then(setCap).catch(() => {})
    get(`/api/stress/${symbol}`).then(setStress).catch(() => {})
  }, [symbol])
  if (err) return <div className="card neg">{err}</div>
  return <>
    <div className="card"><h2>5a. 严格回测（walk-forward 样本外，{symbol}，{bt?.n_days ?? '…'} 个交易日） {bt?.data_is_simulated && <span className="tag">SIMULATED</span>}</h2>
      {!bt ? <div>计算中（9 个策略 × 多折）…</div> : <div className="scroll"><table><thead><tr><th>策略</th><th>日均净收益(OOS)</th><th>样本内日均</th><th>累计</th><th>年化</th><th>Sharpe</th><th>Sortino</th><th>最大回撤</th><th>日胜率</th><th>笔/日</th><th>成本/日</th><th>相对A捕获</th><th>不可验证天</th></tr></thead>
        <tbody>{Object.entries(bt.strategies).map(([k, v]: any) => { const m = v.oos_metrics; return <tr key={k} style={k === bt.best_oos_strategy ? { background: '#f0f7ff' } : {}}><td>{k}{k === bt.best_oos_strategy && <span className="tag">最佳OOS</span>}</td><td className={cls(m.mean_daily_net_return)}>{pct(m.mean_daily_net_return, 3)}</td><td>{pct(v.in_sample_vs_oos.in_sample_mean_daily, 3)}</td><td className={cls(m.total_net_return)}>{pct(m.total_net_return)}</td><td>{pct(m.annualized_return)}</td><td>{num(m.sharpe)}</td><td>{num(m.sortino)}</td><td>{pct(m.max_drawdown)}</td><td>{pct(m.daily_win_rate, 0)}</td><td>{num(m.trades_per_day, 1)}</td><td>{pct(m.cost_pct_of_capital_per_day, 3)}</td><td>{num(m.capture_ratio_vs_hindsight)}</td><td>{m.unverifiable_days}</td></tr> })}
          {Object.entries(bt.benchmarks).map(([k, m]: any) => <tr key={k}><td><i>基准 {k}</i></td><td className={cls(m.mean_daily_net_return)}>{pct(m.mean_daily_net_return, 3)}</td><td>—</td><td>{pct(m.total_net_return)}</td><td>{pct(m.annualized_return)}</td><td>{num(m.sharpe)}</td><td>{num(m.sortino)}</td><td>{pct(m.max_drawdown)}</td><td>{pct(m.daily_win_rate, 0)}</td><td colSpan={4}></td></tr>)}</tbody></table></div>}
      <div className="muted">样本内日均仅供对照，严禁当作样本外绩效；参数在训练窗选择，只在随后测试窗评估。</div></div>
    <div className="card"><h2>5b. 七档本金目标达成率（最佳 OOS 策略每日净收益 vs 目标下限）</h2>
      {att?.tier_issues && <div className="banner">{att.tier_issues.map((s: string, i: number) => <div key={i}>• {s}</div>)}<div>• 0.8%–2.2% 为研究目标，不是保证收益。</div></div>}
      {!att ? <div>计算中…</div> : <table><thead><tr><th>档位</th><th>目标/日</th><th>周期(天)</th><th>样本天</th><th>达标天</th><th>达标率</th><th>日均净收益</th><th>周期单利</th><th>周期复利</th><th>结论</th></tr></thead>
        <tbody>{att.rows.map((a: any) => <tr key={a.tier}><td>{a.tier}</td><td>{a.target_daily_pct?.[0]}–{a.target_daily_pct?.[1]}%</td><td>{a.period_days}</td><td>{a.n_days}</td><td>{a.attained_days}</td><td>{pct(a.attainment_rate, 0)}</td><td className={cls(a.mean_daily_ret_pct)}>{a.mean_daily_ret_pct?.toFixed(3)}%</td><td>{a.period_simple_sum_pct?.toFixed(2) ?? '—'}%</td><td>{a.period_compound_pct?.toFixed(2) ?? '—'}%</td><td className={a.met ? 'pos' : 'neg'}><b>{a.status}</b></td></tr>)}</tbody></table>}</div>
    <div className="card"><h2>5c. 七档本金容量模拟（成本 {cap?.cost_profile}）</h2>
      {!cap ? <div>…</div> : <div className="scroll"><table><thead><tr><th>档位</th><th>点</th><th>本金</th><th>拆单</th><th>单边成本</th><th>冲击</th><th>往返成本</th><th>Gas往返</th><th>盈亏平衡</th><th>占深度</th><th>占原生ADV</th><th>目标/日</th><th>单笔可达标</th><th>可验证</th><th>备注</th></tr></thead>
        <tbody>{cap.rows.map((c: any, i: number) => <tr key={i}><td>{c.tier_id}</td><td>{c.point}</td><td>{num(c.capital_usd, 0)}</td><td>{c.child_orders}</td><td>{c.side_cost_pct.toFixed(3)}%</td><td>{c.impact_pct.toFixed(3)}%</td><td>{c.round_trip_pct.toFixed(3)}%</td><td>{num(c.gas_usd_round_trip)}</td><td>{c.break_even_move_pct.toFixed(3)}%</td><td>{pct(c.pct_of_depth, 1)}</td><td>{pct(c.pct_of_adv, 3)}</td><td>{c.target_low_pct}–{c.target_high_pct}%</td><td className={c.feasible_vs_breakeven ? 'pos' : 'neg'}>{c.feasible_vs_breakeven ? '可能' : '否'}</td><td className={c.unverifiable ? 'neg' : 'pos'}>{c.unverifiable ? '不可验证' : '可验证'}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{c.notes.join('；')}</td></tr>)}</tbody></table></div>}</div>
    <div className="card"><h2>5d. 压力测试（VWAP，近 10 日）</h2>
      {!stress ? <div>…</div> : <table><thead><tr><th>情景</th><th>日均净收益</th><th>累计</th><th>最差日</th><th>交易数</th><th>成本</th><th>不可验证天</th></tr></thead>
        <tbody>{stress.rows.map((s: any) => <tr key={s.scenario}><td>{s.scenario}</td><td className={cls(s.mean_daily_net)}>{pct(s.mean_daily_net, 3)}</td><td className={cls(s.total_net)}>{pct(s.total_net)}</td><td className="neg">{pct(s.worst_day)}</td><td>{s.n_trades}</td><td>{num(s.cost_usd)}</td><td>{s.unverifiable_days}</td></tr>)}</tbody></table>}</div>
  </>
}
