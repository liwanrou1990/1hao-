import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Target({ symbol, day }: { symbol: string; day: string }) {
  const [caps, setCaps] = useState<any>(null)
  const [cap, setCap] = useState<number>(100000)
  const [d, setD] = useState<any>(null)
  const [mode, setMode] = useState<'teaching' | 'strategy'>('teaching')
  const [wf, setWf] = useState<any>(null)
  const [sum, setSum] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { get('/api/target/capitals').then(setCaps).catch(() => {}) }, [])
  useEffect(() => { setD(null); setErr(''); get(`/api/target/teaching/${symbol}?day=${day}&capital=${cap}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day, cap])
  useEffect(() => { setWf(null); get(`/api/target/walkforward/${symbol}?capital=${cap}`).then(setWf).catch(e => setWf({ error: String(e) })) }, [symbol, cap])
  useEffect(() => { setSum(null); get(`/api/target/summary?day=${day}`).then(setSum).catch(() => {}) }, [day])
  const r = d ? (mode === 'teaching' ? d.teaching : d.strategy) : null
  return <>
    <div className="banner"><b>固定收益是研究目标而非保证。</b>A 教学路径 = 已知完整走势后反向挑选的稳定小额交易（事后，不可复制）；B 策略 = 不使用未来信息、每根收盘决策下根分批成交、参数仅在训练窗选择、绩效只报样本外。两者严格分开。</div>
    {caps?.tier_issues && <div className="banner">{caps.tier_issues.map((s: string, i: number) => <div key={i}>• {s}</div>)}<div>• 目标取值 target_mode={caps.config.target_mode}；示例“10 万→0.98%”无法由当前档位表推导，可在 tiers.yaml 修改或显式指定。</div></div>}
    <div className="card">
      <h2>{symbol} · {day} · 多次交易累计实现固定日收益目标 {d?.simulated && <span className="tag">SIMULATED · 非正式验证</span>}{d && <span className="tag">{d.source}</span>}</h2>
      <div className="row">本金 <select value={cap} onChange={e => setCap(Number(e.target.value))}>{(caps?.capitals ?? [{ capital_usd: 100000, label: '' }]).map((c: any) => <option key={c.capital_usd} value={c.capital_usd}>{c.label} {num(c.capital_usd, 0)} USD</option>)}</select>
        模式 <select value={mode} onChange={e => setMode(e.target.value as any)}><option value="teaching">A 教学路径（事后）</option><option value="strategy">B 策略同日回放（无未来信息）</option></select>
        {d && <span className="muted"><a href={d.chart_url} target="_blank">独立图表页</a></span>}</div>
      {err && <div className="neg">{err}</div>}
      {!d && !err && <div>计算中…</div>}
      {r && <>
        <div className="kpi" style={{ margin: '8px 0' }}>
          <div>目标<b>{num(r.target_usd)}</b>{pct(r.target_return, 3)} / 日</div>
          <div>已实现净利润<b className={cls(r.realized_net_usd)}>{num(r.realized_net_usd)}</b>{pct(r.realized_net_return, 3)}</div>
          <div>交易<b>{r.trades.length}</b>买卖各 {r.trades.length} 次{r.stopped_reason ? ` · ${r.stopped_reason}` : ''}</div>
          <div>结果<b className={r.target_met ? 'pos' : 'neg'}>{r.target_met ? '达标' : '未达标'}</b>{r.target_hit_ts ? `@ ${r.target_hit_ts.slice(11, 16)}` : (mode === 'teaching' ? '显示最高可行累计' : '')}</div>
          <div>累计曲线回撤<b>{num(r.max_drawdown_usd)}</b>{r.unverifiable ? '不可验证' : '可验证'}{r.partial_fills ? ` · 部分成交 ${r.partial_fills}` : ''}</div>
        </div>
        {(r.reasons_not_met?.length > 0 || r.notes?.length > 0) && <div className="banner">{[...(r.reasons_not_met ?? []), ...(r.notes ?? [])].map((n: string, i: number) => <div key={i}>• {n}</div>)}</div>}
        <Chart candles={d.candles} markers={r.markers} />
        <div className="muted">累计已实现净利润曲线（USD）</div>
        <Chart candles={[]} line={r.cum} height={150} />
        <div className="scroll"><table><thead><tr><th>#</th><th>买入(首批)</th><th>批</th><th>均价</th><th>卖出(末批)</th><th>批</th><th>均价</th><th>投入</th><th>数量</th><th>成本</th><th>净盈亏</th><th>净收益</th><th>累计</th><th>最大不利</th><th>最大有利</th><th>持有</th><th>入场理由</th><th>出场理由</th></tr></thead>
          <tbody>{r.trades.map((x: any) => <tr key={x.seq}><td>{x.seq}</td><td>{x.buy_ts.slice(11, 16)}</td><td>{x.entry.n_children}</td><td>{num(x.avg_entry, 3)}</td><td>{x.sell_ts.slice(11, 16)}</td><td>{x.exit.n_children}</td><td>{num(x.avg_exit, 3)}</td><td>{num(x.size_usd)}</td><td>{num(x.qty, 4)}</td><td>{num(x.cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td><td>{num(x.cum_net_pnl_usd)}</td><td>{x.mae_pct.toFixed(2)}%</td><td>{x.mfe_pct.toFixed(2)}%</td><td>{x.hold_bars}</td><td style={{ textAlign: 'left', whiteSpace: 'normal' }}>{x.reason_entry}</td><td style={{ textAlign: 'left' }}>{x.reason_exit}</td></tr>)}
            {r.trades.length === 0 && <tr><td colSpan={18}>无交易</td></tr>}</tbody></table></div>
        <div className="muted">{r.disclaimer}</div></>}
    </div>
    <div className="card"><h2>B. 策略滚动样本外验证（{symbol}，本金 {num(cap, 0)}）</h2>
      {!wf ? <div>walk-forward 计算中…</div> : wf.error ? <div className="neg">{wf.error}</div> : <>
        <div className="kpi" style={{ margin: '8px 0' }}>
          <div>样本外天数<b>{wf.oos_stats.n_days}</b>{wf.folds.length} 折</div>
          <div>达标率<b className={wf.oos_stats.attainment_rate >= 0.5 ? 'pos' : 'neg'}>{pct(wf.oos_stats.attainment_rate, 0)}</b>样本内 {pct(wf.in_sample_mean.attainment_rate, 0)}（对照）</div>
          <div>亏损日比例<b className="neg">{pct(wf.oos_stats.losing_day_rate, 0)}</b></div>
          <div>日均净收益<b className={cls(wf.oos_stats.mean_daily_return)}>{pct(wf.oos_stats.mean_daily_return, 3)}</b>标准差 {pct(wf.oos_stats.std_daily_return, 3)}</div>
          <div>最大回撤<b className="neg">{pct(wf.oos_stats.max_drawdown_pct, 2)}</b>最差日 {pct(wf.oos_stats.worst_day_return, 2)}</div>
          <div>可执行性<b>{wf.oos_stats.avg_trades_per_day.toFixed(1)} 笔/日</b>部分成交 {wf.oos_stats.partial_fill_days} 天 · 不可验证 {wf.oos_stats.unverifiable_days} 天</div>
        </div>
        <div className="scroll"><table><thead><tr><th>日期</th><th>净利润</th><th>收益率</th><th>达标</th><th>笔数</th><th>停止原因</th><th>回撤</th><th>部分成交</th></tr></thead>
          <tbody>{wf.oos_days.map((x: any) => <tr key={x.day}><td>{x.day}</td><td className={cls(x.net_usd)}>{num(x.net_usd)}</td><td>{pct(x.net_return, 3)}</td><td className={x.target_met ? 'pos' : 'neg'}>{x.target_met ? '达标' : '未达标'}</td><td>{x.n_trades}</td><td style={{ textAlign: 'left' }}>{x.stopped ?? ''}</td><td>{num(x.max_dd_usd)}</td><td>{x.partial_fills}</td></tr>)}</tbody></table></div>
        <div className="muted">{wf.note}</div></>}
    </div>
    <div className="card"><h2>A. 教学路径总览（全标的 × 本金）· {day}</h2>
      {!sum ? <div>计算中…</div> : <div className="scroll"><table><thead><tr><th>标的</th><th>本金</th><th>目标</th><th>已实现</th><th>收益率</th><th>笔数</th><th>达标</th><th>达标时刻</th><th>资金利用</th><th>可验证</th><th>未达标原因</th></tr></thead>
        <tbody>{sum.rows.map((x: any, i: number) => x.error ? <tr key={i}><td>{x.symbol}</td><td>{num(x.capital_usd, 0)}</td><td colSpan={9}>{x.error}</td></tr> : <tr key={i}><td>{x.symbol}{!x.tradable && <span className="tag">待验证</span>}</td><td>{num(x.capital_usd, 0)}</td><td>{num(x.target_usd)}</td><td className={cls(x.realized_net_usd)}>{num(x.realized_net_usd)}</td><td>{x.realized_pct.toFixed(3)}%</td><td>{x.n_trades}</td><td className={x.target_met ? 'pos' : 'neg'}><b>{x.target_met ? '达标' : '未达标'}</b></td><td>{x.target_hit_ts ? x.target_hit_ts.slice(11, 16) : '—'}</td><td>{pct(x.capital_utilization, 0)}</td><td className={x.unverifiable ? 'neg' : 'pos'}>{x.unverifiable ? '不可验证' : '可验证'}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{x.reasons}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
