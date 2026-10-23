import { useEffect, useState } from 'react'
import { get, post, pct, num, cls } from '../api'

export default function Playbook({ day }: { day: string }) {
  const [mode, setMode] = useState('cross')
  const [d, setD] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [latest, setLatest] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  useEffect(() => { get('/api/playbook/latest').then(setLatest).catch(() => {}) }, [])
  useEffect(() => { setD(null); setErr(''); get(`/api/playbook?day=${day}&mode=${encodeURIComponent(mode)}`).then(setD).catch(e => setErr(String(e))) }, [day, mode])
  const run = async () => { setBusy(true); try { await post(`/api/playbook/report?day=${day}`); setLatest(await get('/api/playbook/latest')) } catch (e) { setErr(String(e)) } finally { setBusy(false) } }
  const r = d?.results?.[Math.min(sel, (d?.results?.length ?? 1) - 1)]
  return <>
    <div className="banner"><b>事后最优模板，不预测未来：</b>在已知当日完整走势后反向求解达到档位目标的低风险交易路径；不是当时可执行的策略，不代表未来可复制收益。目标不可达时显示最高可实现净收益与缺口。{d?.banner && <span> {d.banner}</span>}</div>
    <div className="card">
      <h2>历史盈利路径模板 · {day}</h2>
      <div className="row">模式 <select value={mode} onChange={e => setMode(e.target.value)}><option value="cross">跨资产（可切换）</option>{['NVDA', 'TSLA', 'MU', 'SNDK', 'INTC', 'META', 'MSFT', 'SPY', 'QQQ', 'BTC', 'ETH', 'SOL', 'HYPE'].map(s => <option key={s} value={`single:${s}`}>单资产 {s}</option>)}</select>
        {d && <>档位 <select value={sel} onChange={e => setSel(Number(e.target.value))}>{d.results.map((x: any, i: number) => <option key={i} value={i}>{x.tier} · {num(x.capital_usd, 0)} · 目标 {num(x.target_usd, 0)}</option>)}</select></>}
        <button disabled={busy} onClick={run}>{busy ? '生成中（约 5–10 分钟）…' : '生成当日完整报告（图表+CSV）'}</button>{latest?.report_url && <a href={latest.report_url} target="_blank">最近报告 {latest.day}</a>}</div>
      {err && <div className="neg">{err}</div>}
      {!d && !err && <div>求解七档（约 1 分钟/档）…</div>}
      {r && <>
        <div className="kpi" style={{ margin: '8px 0' }}>
          <div>达标<b className={r.target_met ? 'pos' : 'neg'}>{r.target_met ? '是' : '否'}</b>{r.time_to_target_ts ? `@ ${r.time_to_target_ts.slice(11, 16)}` : ''}</div>
          <div>净收益<b className={cls(r.net_usd)}>{num(r.net_usd, 0)}</b>{pct(r.net_return, 3)}</div>
          <div>最高可实现 / 缺口<b>{num(r.max_achievable_net_usd, 0)}</b>{num(r.gap_usd, 0)}</div>
          <div>笔数 多/空<b>{r.n_trades}</b>{r.n_long}/{r.n_short}</div>
          <div>资产切换<b>{r.n_switches}</b>{(r.assets_used || []).join('/')}</div>
          <div>资金片 / λ<b>{r.n_tranches}</b>{r.lam}</div>
          <div>最大回撤（盯市）<b>{num(r.max_drawdown_usd, 0)}</b></div>
          <div>Σ最大不利波动<b>{num(r.sum_mae_usd, 0)}</b></div>
          <div>成本 / 资金成本<b>{num(r.cost_usd, 0)}</b>{num(r.funding_usd, 0)}</div>
          <div>峰值占用<b>{pct(r.peak_deployed_usd / r.capital_usd, 0)}</b></div>
        </div>
        {r.limits?.length > 0 && <div className="banner">{r.limits.map((x: string, i: number) => <div key={i}>• {x}</div>)}</div>}
        <div className="scroll"><table><thead><tr><th>#</th><th>资产</th><th>方向</th><th>入场</th><th>入场价</th><th>出场</th><th>出场价</th><th>仓位</th><th>数量</th><th>成本</th><th>最大不利</th><th>净利润</th><th>净收益率</th><th>持有</th><th>累计</th><th>达标</th></tr></thead>
          <tbody>{r.trades.map((x: any) => <tr key={x.no}><td>{x.no}</td><td>{x.symbol}</td><td>{x.direction === 'long' ? '做多' : '做空'}</td><td>{x.entry_ts.slice(11, 16)}</td><td>{num(x.entry_price, 4)}</td><td>{x.exit_ts.slice(11, 16)}</td><td>{num(x.exit_price, 4)}</td><td>{num(x.size_usd, 0)}</td><td>{num(x.qty, 4)}</td><td>{num(x.cost_usd)}</td><td>{pct(x.mae_pct, 3)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td>{x.hold_min}</td><td>{num(x.cum_net_pnl_usd)}</td><td>{x.reaches_target ? '★' : ''}</td></tr>)}
            {r.trades.length === 0 && <tr><td colSpan={16}>无可行交易</td></tr>}</tbody></table></div>
      </>}
    </div>
  </>
}
