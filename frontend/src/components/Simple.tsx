import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Simple({ symbol, day }: { symbol: string; day: string }) {
  const [d, setD] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [sum, setSum] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { setD(null); setErr(''); get(`/api/simple/${symbol}?day=${day}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day])
  useEffect(() => { setSum(null); get(`/api/simple-summary?day=${day}`).then(setSum).catch(() => {}) }, [day])
  if (err) return <div className="card neg">{err}</div>
  if (!d) return <div className="card">模拟中（七档本金）…</div>
  const r = d.results[Math.min(sel, d.results.length - 1)]
  return <>
    <div className="banner"><b>已知昨日完整走势的事后教学分析</b>：所有买卖点由程序在看过全天K线后反向选出，不是实盘预测，暂不执行真实资金交易。{d.simulated && <b> 当前为 SIMULATED 模拟数据，非正式验证。</b>}</div>
    <div className="card">
      <h2>{symbol} · {d.day} · 多空多次交易 · 累计净利润达标 <span className="tag">{d.source}</span>{d.fallback_native && <span className="tag neg">无代币K线，使用原生价格</span>}</h2>
      <div className="row">本金档位 <select value={sel} onChange={e => setSel(Number(e.target.value))}>{d.results.map((x: any, i: number) => <option key={i} value={i}>{x.tier} · {num(x.capital_usd, 0)} USD · 目标 {num(x.target_usd, 0)}</option>)}</select>
        <span className="muted"><a href={d.chart_url} target="_blank">独立图表页</a> · 时间轴为美东时间</span></div>
      <div className="kpi" style={{ margin: '8px 0' }}>
        <div>本金<b>{num(r.capital_usd, 0)}</b>{r.tier}</div>
        <div>目标利润<b>{num(r.target_usd)}</b>{pct(r.target_return, 2)}</div>
        <div>实际净利润<b className={cls(r.realized_net_usd)}>{num(r.realized_net_usd)}</b>{pct(r.realized_net_return, 3)}</div>
        <div>买入 / 卖出<b>{r.n_buys} / {r.n_sells}</b></div>
        <div>做多 / 做空<b>{r.n_long} / {r.n_short}</b>{r.shortable ? '可做空' : '不可做空'}</div>
        <div>资金利用率<b>{pct(r.capital_utilization, 0)}</b>平均 {pct(r.avg_utilization, 0)}</div>
        <div>交易成本 / 资金成本<b>{num(r.trading_cost_usd)}</b>{num(r.capital_cost_usd)}</div>
        <div>是否达标<b className={r.target_met ? 'pos' : 'neg'}>{r.target_met ? '达标' : '未达标'}</b>{r.target_hit_ts ? `@ ${r.target_hit_ts.slice(11, 16)}` : ''}</div>
      </div>
      {r.notes.length > 0 && <div className="banner">{r.notes.map((n: string, i: number) => <div key={i}>• {n}</div>)}</div>}
      <Chart candles={d.candles} markers={r.markers} />
      <div className="muted">累计净利润（USD）</div>
      <Chart candles={[]} line={r.cum} height={150} />
      <div className="scroll"><table><thead><tr><th>#</th><th>方向</th><th>开仓</th><th>批</th><th>开仓均价</th><th>平仓</th><th>批</th><th>平仓均价</th><th>投入</th><th>数量</th><th>交易成本</th><th>资金成本</th><th>净盈亏</th><th>净收益</th><th>累计</th><th>最大不利</th><th>持有</th></tr></thead>
        <tbody>{r.trades.map((x: any) => <tr key={x.seq}><td>{x.seq}</td><td>{x.direction === 'long' ? '做多' : '做空'}</td><td>{x.open_ts.slice(11, 16)}</td><td>{x.open_fill.n_children}</td><td>{num(x.avg_open, 3)}</td><td>{x.close_ts.slice(11, 16)}</td><td>{x.close_fill.n_children}</td><td>{num(x.avg_close, 3)}</td><td>{num(x.size_usd)}</td><td>{num(x.qty, 4)}</td><td>{num(x.trading_cost_usd)}</td><td>{num(x.capital_cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td>{num(x.cum_net_pnl_usd)}</td><td>{x.mae_pct.toFixed(2)}%</td><td>{x.hold_bars}</td></tr>)}
          {r.trades.length === 0 && <tr><td colSpan={17}>无交易</td></tr>}</tbody></table></div>
    </div>
    <div className="card"><h2>全标的 × 七档 · {day}</h2>
      {d.tier_issues && <div className="banner">{d.tier_issues.map((s: string, i: number) => <div key={i}>• {s}</div>)}</div>}
      {!sum ? <div>计算中…</div> : <div className="scroll"><table><thead><tr><th>标的</th><th>档位</th><th>本金</th><th>目标利润</th><th>实际净利润</th><th>收益率</th><th>买</th><th>卖</th><th>多</th><th>空</th><th>资金利用率</th><th>达标</th><th>可验证</th><th>说明</th></tr></thead>
        <tbody>{sum.rows.map((x: any, i: number) => <tr key={i}><td>{x.symbol}</td><td>{x.tier}</td><td>{num(x.capital_usd, 0)}</td><td>{num(x.target_usd)}</td><td className={cls(x.realized_net_usd)}>{num(x.realized_net_usd)}</td><td>{pct(x.realized_net_return, 3)}</td><td>{x.n_buys}</td><td>{x.n_sells}</td><td>{x.n_long}</td><td>{x.n_short}</td><td>{pct(x.capital_utilization, 0)}</td><td className={x.target_met ? 'pos' : 'neg'}><b>{x.target_met ? '达标' : '未达标'}</b></td><td className={x.unverifiable ? 'neg' : 'pos'}>{x.unverifiable ? '不可验证' : '可验证'}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{x.notes.join('；')}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
