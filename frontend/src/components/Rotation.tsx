import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, post, pct, num, cls } from '../api'

export default function Rotation({ day }: { day: string }) {
  const [specs, setSpecs] = useState<any[]>([])
  const [strategy, setStrategy] = useState('E:cross_rotation')
  const [d, setD] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [sum, setSum] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { get('/api/rotation/specs').then(setSpecs).catch(() => {}); get('/api/rotation/summary').then(setSum).catch(() => {}) }, [])
  useEffect(() => { setD(null); setErr(''); get(`/api/rotation/day?strategy=${encodeURIComponent(strategy)}&day=${day}`).then(setD).catch(e => setErr(String(e))) }, [strategy, day])
  const run = async () => { setBusy(true); try { await post('/api/rotation/report'); setSum(await get('/api/rotation/summary')) } catch (e) { setErr(String(e)) } finally { setBusy(false) } }
  const r = d?.results?.[Math.min(sel, (d?.results?.length ?? 1) - 1)]
  return <>
    <div className="banner"><b>三套模型分开：</b>A 事后理论最优 / B 事后流动性约束上限 / C 无未来信息因果轮动。本页 C 为每分钟只用已产生数据的模拟，参数来自默认值（多日回测中由之前交易日选择）；<b>不是保证收益</b>。{d?.banner && <span> {d.banner}</span>}</div>
    <div className="card">
      <h2>单日因果轮动（C） · {day}</h2>
      <div className="row">策略 <select value={strategy} onChange={e => setStrategy(e.target.value)}>{specs.map(s => <option key={s.key} value={s.key}>{s.family} · {s.name}</option>)}</select>
        {d && <>档位 <select value={sel} onChange={e => setSel(Number(e.target.value))}>{d.results.map((x: any, i: number) => <option key={i} value={i}>{x.tier} · {num(x.capital_usd, 0)} · 目标 {num(x.target_usd, 0)}</option>)}</select></>}</div>
      {err && <div className="neg">{err}</div>}
      {!d && !err && <div>计算七档…</div>}
      {r && <>
        <div className="kpi" style={{ margin: '8px 0' }}>
          <div>C 净收益<b className={cls(r.realized_net_usd)}>{num(r.realized_net_usd, 0)}</b>{pct(r.realized_net_return, 3)}</div>
          <div>达标<b className={r.target_met ? 'pos' : 'neg'}>{r.target_met ? '是' : '否'}</b>{r.target_hit_ts ? `@ ${r.target_hit_ts.slice(11, 16)}` : ''}</div>
          <div>笔数 多/空<b>{r.n_trades}</b>{r.n_long}/{r.n_short}</div>
          <div>切换次数<b>{r.n_switches}</b></div>
          <div>空仓时间<b>{pct(r.flat_ratio, 0)}</b></div>
          <div>日内回撤<b>{num(r.max_drawdown_usd, 0)}</b></div>
          <div>峰值占用<b>{pct(r.peak_deployed_usd / r.capital_usd, 0)}</b></div>
          <div>执行成本<b>{num(r.execution_cost_usd, 0)}</b></div>
          <div>达标后交易/盈亏<b>{r.post_target_trades}</b>{num(r.post_target_net_usd, 0)}</div>
          <div>停止原因<b style={{ fontSize: 12 }}>{r.stop_reason || '—'}</b></div>
        </div>
        <div className="muted">路径：{(r.path || []).join(' → ') || '空仓'}</div>
        {r.switches?.length > 0 && <div className="scroll"><table><thead><tr><th>时间</th><th>从</th><th>到</th><th>依据</th></tr></thead><tbody>{r.switches.map((s: any, i: number) => <tr key={i}><td>{s.ts.slice(11, 16)}</td><td>{s.from_symbol}</td><td>{s.to_symbol}</td><td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{s.reason}</td></tr>)}</tbody></table></div>}
        <div className="scroll"><table><thead><tr><th>#</th><th>资产</th><th>方向</th><th>模式</th><th>开仓</th><th>均价</th><th>平仓</th><th>均价</th><th>投入</th><th>成本</th><th>净盈亏</th><th>净收益</th><th>出场原因</th><th>累计</th></tr></thead>
          <tbody>{r.trades.map((x: any) => <tr key={x.seq}><td>{x.seq}</td><td>{x.symbol}</td><td>{x.direction === 'long' ? '多' : '空'}</td><td>{x.mode}</td><td>{x.open_ts.slice(11, 16)}</td><td>{num(x.avg_open, 3)}</td><td>{x.close_ts.slice(11, 16)}</td><td>{num(x.avg_close, 3)}</td><td>{num(x.size_usd, 0)}</td><td>{num(x.trading_cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td style={{ textAlign: 'left' }}>{x.reason_exit}</td><td>{num(x.cum_net_pnl_usd)}</td></tr>)}
            {r.trades.length === 0 && <tr><td colSpan={14}>当日空仓（无资产满足条件）</td></tr>}</tbody></table></div>
        <div className="muted">暂停原因：{Object.entries(r.pause_reasons || {}).map(([k, v]) => `${k}×${v}`).join('；')}</div>
      </>}
    </div>
    <div className="card"><h2>多日样本外回测排名（A/B/C 分列）</h2>
      <div className="row"><button disabled={busy} onClick={run}>{busy ? '运行中（约 30–60 分钟）…' : '运行/刷新 rotation-report'}</button>{sum?.report_url && <a href={sum.report_url} target="_blank">完整 HTML 报告</a>}</div>
      {!sum?.ranking?.length ? <div className="muted">{sum?.note || '加载中…'}</div> : <div className="scroll"><table><thead><tr><th>档位</th><th>名次</th><th>策略</th><th>C 达标率</th><th>C 日均收益率</th><th>亏损日</th><th>多日回撤</th><th>日均笔数</th><th>切换</th><th>A 达标率</th><th>B 达标率</th><th>得分</th></tr></thead>
        <tbody>{sum.ranking.filter((x: any) => x.rank_in_tier <= 5).map((x: any, i: number) => <tr key={i}><td>{x.tier}</td><td>{x.rank_in_tier}</td><td style={{ textAlign: 'left' }}>{x.name}</td><td className={x.attainment > 0 ? 'pos' : 'neg'}><b>{pct(x.attainment, 0)}</b></td><td>{Number(x.mean_net_pct).toFixed(3)}%</td><td>{pct(x.losing_day_rate, 0)}</td><td>{num(x.multi_day_dd_usd, 0)}</td><td>{Number(x.mean_trades).toFixed(1)}</td><td>{Number(x.mean_switches).toFixed(1)}</td><td>{x.A_attainment === '' ? '—' : pct(x.A_attainment, 0)}</td><td>{x.B_attainment === '' ? '—' : pct(x.B_attainment, 0)}</td><td>{Number(x.score).toFixed(3)}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
