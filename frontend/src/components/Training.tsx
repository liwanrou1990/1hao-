import { useEffect, useState } from 'react'
import Chart, { Marker } from './Chart'
import { get, post, pct, num, cls } from '../api'

export default function Training({ symbol, day }: { symbol: string; day: string }) {
  const [trader, setTrader] = useState('trader1')
  const [s, setS] = useState<any>(null)
  const [board, setBoard] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const refreshBoard = () => get('/api/training/leaderboard/all').then(d => setBoard(d.rows)).catch(() => {})
  useEffect(() => { refreshBoard() }, [])
  const finishIf = async (v: any) => { if (v.finished && !v.score) { v = await post(`/api/training/${v.session_id}/finish`); refreshBoard() } setS(v) }
  const start = async () => { setBusy(true); try { setS(await post('/api/training/start', { symbol, day, trader })) } finally { setBusy(false) } }
  const act = async (a: string) => { if (!s) return; setBusy(true); try { await post(`/api/training/${s.session_id}/decide`, { action: a }); await finishIf(await post(`/api/training/${s.session_id}/advance`)) } finally { setBusy(false) } }
  const skip = async (n: number) => { if (!s) return; setBusy(true); try { let v = s; for (let i = 0; i < n && !v.finished; i++) v = await post(`/api/training/${s.session_id}/advance`); await finishIf(v) } finally { setBusy(false) } }
  const finish = async () => { if (!s) return; setBusy(true); try { setS(await post(`/api/training/${s.session_id}/finish`)); refreshBoard() } finally { setBusy(false) } }
  const sc = s?.score
  const markers: Marker[] = s ? s.decisions.filter((d: any) => d.exec_price && s.candles[d.idx]).map((d: any) => ({ time: s.candles[d.idx].time, position: d.action === 'buy' ? 'belowBar' : 'aboveBar', color: d.action === 'buy' ? '#00C853' : '#D50000', shape: d.action === 'buy' ? 'arrowUp' : 'arrowDown', text: `${d.action === 'buy' ? '买' : '卖'} ${d.exec_price.toFixed(2)}` })) : []
  return <>
    <div className="card"><h2>7. 交易员训练模式：未来K线隐藏，逐根推进，收盘下单、下根开盘成交</h2>
      <div className="row"><input value={trader} onChange={e => setTrader(e.target.value)} placeholder="交易员名" /><button className="primary" onClick={start} disabled={busy}>开始 {symbol} {day}</button>
        {s && !s.finished && <>
          <button className="primary" style={{ background: '#2e7d32', borderColor: '#2e7d32' }} disabled={busy || s.position === 1} onClick={() => act('buy')}>买入（下根开盘）</button>
          <button className="primary" style={{ background: '#c62828', borderColor: '#c62828' }} disabled={busy || s.position === 0} onClick={() => act('sell')}>卖出（下根开盘）</button>
          <button className="ghost" disabled={busy} onClick={() => act('hold')}>观望 → 下一根</button>
          <button className="ghost" disabled={busy} onClick={() => skip(10)}>快进 10 根</button>
          <button className="ghost" disabled={busy} onClick={finish}>提前结束并计分</button>
          <span className="muted">进度 {s.cursor + 1}/{s.total_bars} · 持仓 {s.position ? '多头' : '空仓'} {s.simulated && <span className="tag">SIMULATED</span>}</span></>}</div>
      {s && <Chart candles={s.candles} fit={false} markers={markers} />}
      {sc && <div className="card" style={{ marginTop: 10 }}><h2>成绩：{sc.grade}</h2>
        <div className="kpi"><div>交易员净收益<b className={cls(sc.comparison.trader.net_return)}>{pct(sc.comparison.trader.net_return)}</b>{sc.comparison.trader.n_trades} 笔，成本 {num(sc.comparison.trader.cost_usd)}</div>
          <div>A 事后最优<b>{pct(sc.comparison.hindsight.net_return)}</b>{sc.comparison.hindsight.n_trades} 笔</div>
          {sc.comparison.ai && <div>AI 策略<b className={cls(sc.comparison.ai.net_return)}>{pct(sc.comparison.ai.net_return)}</b>{sc.comparison.ai.n_trades} 笔</div>}
          <div>捕获率(相对A)<b>{sc.capture_ratio_vs_hindsight == null ? '—' : num(sc.capture_ratio_vs_hindsight)}</b></div></div>
        {sc.comparison.rules && <table style={{ marginTop: 8 }}><thead><tr><th>规则策略</th><th>净收益</th></tr></thead><tbody>{Object.entries(sc.comparison.rules).map(([k, v]: any) => <tr key={k}><td>{k}</td><td className={cls(v)}>{pct(v)}</td></tr>)}</tbody></table>}</div>}
    </div>
    <div className="card"><h2>训练记录</h2><table><thead><tr><th>交易员</th><th>标的</th><th>日期</th><th>净收益</th><th>捕获率</th><th>评级</th></tr></thead>
      <tbody>{board.map(r => <tr key={r.session_id}><td>{r.trader}</td><td>{r.symbol}</td><td>{r.day}</td><td className={cls(r.net_return)}>{pct(r.net_return)}</td><td>{num(r.capture)}</td><td>{r.grade}</td></tr>)}</tbody></table></div>
  </>
}
