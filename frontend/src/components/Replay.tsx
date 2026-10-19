import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Replay({ symbol, day }: { symbol: string; day: string }) {
  const [data, setData] = useState<any>(null)
  const [strategy, setStrategy] = useState<string>('')
  const [err, setErr] = useState('')
  useEffect(() => { setStrategy('') }, [symbol, day])
  useEffect(() => { setErr(''); get(`/api/replay/${symbol}?day=${day}${strategy ? `&strategy=${encodeURIComponent(strategy)}` : ''}`).then(d => { setData(d); if (!strategy) setStrategy(d.strategy) }).catch(e => setErr(String(e))) }, [symbol, day, strategy])
  if (err) return <div className="card neg">{err}</div>
  if (!data) return <div className="card">计算 A/B 复盘中…</div>
  const r = data.executable[data.strategy]
  const eq = data.candles.map((c: any, i: number) => ({ time: c.time, value: data.equity_b[i] }))
  return <>
    <div className="card">
      <h2>2/3. {symbol} · {data.day} 完整昨日K线 + A 理论最优买卖点（蓝）+ B 可执行信号点（绿买/红卖） {data.data_is_simulated && <span className="tag">SIMULATED</span>}</h2>
      <div className="row"><span>B 策略：</span><select value={data.strategy} onChange={e => setStrategy(e.target.value)}>{data.strategies.map((s: string) => <option key={s}>{s}</option>)}</select>
        <span className="muted">时间轴为美东时间 · 成本模型 {data.cost_profile} · 本金 {num(data.capital_usd, 0)} USD · <a href={data.chart_url} target="_blank">独立图表页</a></span></div>
      <Chart candles={data.candles} markers={[...data.markers_a, ...data.markers_b]} />
      <div className="muted">B 模式权益曲线（按收盘价）</div>
      <Chart candles={[]} line={eq} height={150} />
    </div>
    <div className="grid2">
      <div className="card"><h2>A/B 对照</h2>
        <table><thead><tr><th>模式</th><th>策略</th><th>净收益</th><th>交易次数</th><th>成本(USD)</th><th>与A差距</th></tr></thead>
          <tbody>{data.summary.map((s: any, i: number) => <tr key={i}><td>{s['模式']}</td><td>{s['策略']}</td><td className={cls(s['净收益'])}>{pct(s['净收益'])}</td><td>{s['交易次数']}</td><td>{num(s['成本(USD)'])}</td><td>{pct(s['与A差距'])}</td></tr>)}</tbody></table>
        <h2 style={{ marginTop: 10 }}>为什么 A 与 B 有差距</h2><ol>{data.gap_explanation.map((g: string, i: number) => <li key={i}>{g}</li>)}</ol></div>
      <div className="card"><h2>4. 每笔模拟交易（{data.strategy}）</h2>
        <div className="scroll"><table><thead><tr><th>#</th><th>信号时刻</th><th>买入</th><th>买价(成交)</th><th>卖出</th><th>卖价(成交)</th><th>成本</th><th>净盈亏</th><th>净收益</th><th>原因</th></tr></thead>
          <tbody>{r.trades.map((t: any, i: number) => <tr key={i}><td>{i + 1}</td><td>{t.signal_ts?.slice(11, 16)}</td><td>{t.buy_ts?.slice(11, 16)}</td><td>{num(t.buy_exec, 3)}</td><td>{t.sell_ts?.slice(11, 16)}</td><td>{num(t.sell_exec, 3)}</td><td>{num(t.cost_usd)}</td><td className={cls(t.net_pnl_usd)}>{num(t.net_pnl_usd)}</td><td className={cls(t.net_ret)}>{pct(t.net_ret)}</td><td>{t.reason}{t.unverifiable ? '（不可验证）' : ''}</td></tr>)}
            {r.trades.length === 0 && <tr><td colSpan={10}>无交易 —— “不交易”是合法输出</td></tr>}</tbody></table></div>
        <h2 style={{ marginTop: 10 }}>A 理论最优交易（不可实现，仅作上限）</h2>
        <div className="scroll"><table><thead><tr><th>#</th><th>买入</th><th>买价</th><th>卖出</th><th>卖价</th><th>成本</th><th>净盈亏</th></tr></thead>
          <tbody>{data.hindsight.trades.map((t: any, i: number) => <tr key={i}><td>{i + 1}</td><td>{t.buy_ts.slice(11, 16)}</td><td>{num(t.buy_exec, 3)}</td><td>{t.sell_ts.slice(11, 16)}</td><td>{num(t.sell_exec, 3)}</td><td>{num(t.cost_usd)}</td><td className={cls(t.net_pnl_usd)}>{num(t.net_pnl_usd)}</td></tr>)}</tbody></table></div></div>
    </div>
  </>
}
