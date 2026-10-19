import { useEffect, useState } from 'react'
import Chart from './Chart'
import { get, pct, num, cls } from '../api'

export default function Hindsight({ symbol, day }: { symbol: string; day: string }) {
  const [d, setD] = useState<any>(null)
  const [sum, setSum] = useState<any>(null)
  const [pf, setPf] = useState<any>(null)
  const [sel, setSel] = useState(0)
  const [which, setWhich] = useState<'max' | 'target'>('max')
  const [pfTier, setPfTier] = useState('T1')
  const [err, setErr] = useState('')
  useEffect(() => { setD(null); setErr(''); get(`/api/hindsight/${symbol}?day=${day}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day])
  useEffect(() => { setSum(null); get(`/api/hindsight/summary?day=${day}`).then(setSum).catch(() => {}) }, [day])
  useEffect(() => { setPf(null); get(`/api/hindsight/portfolio?day=${day}&tier=${pfTier}&point=mid`).then(setPf).catch(() => {}) }, [day, pfTier])
  if (err) return <div className="card neg">{err}</div>
  if (!d) return <div className="card">求解七档本金事后最优路径…</div>
  const t = d.tiers[Math.min(sel, d.tiers.length - 1)]
  const trades = which === 'max' ? t.trades : t.target_path
  return <>
    <div className="banner"><b>已知完整走势的事后最优模拟</b>：使用昨日全部K线反向求解“当时怎样买卖才能取得更高收益”，实盘无法预知走势，不代表未来可获得同等收益，也不要求交易员复制。</div>
    <div className="card">
      <h2>{symbol} · {d.day} · 事后最优路径 <span className="tag">{d.source}</span>{d.fallback_native && <span className="tag neg">无代币K线，使用原生价格</span>}{d.simulated && <span className="tag">SIMULATED</span>}</h2>
      <div className="row"><span>本金档位：</span><select value={sel} onChange={e => setSel(Number(e.target.value))}>{d.tiers.map((x: any, i: number) => <option key={i} value={i}>{x.tier} {x.point} · {num(x.capital_usd, 0)} USD · 目标 {x.target_daily_pct[0]}%</option>)}</select>
        <select value={which} onChange={e => setWhich(e.target.value as any)}><option value="max">最高收益路径</option><option value="target">最少交易达标路径</option></select>
        <span className="muted"><a href={d.chart_url} target="_blank">独立图表页</a> · 时间轴为美东时间</span></div>
      <div className="kpi" style={{ margin: '8px 0' }}>
        <div>最高可实现净收益<b className={cls(t.max_net_return)}>{pct(t.max_net_return, 3)}</b>{num(t.max_net_profit_usd)} USD · {t.n_trades} 笔</div>
        <div>目标 {t.target_daily_pct[0]}%/日<b className={t.target_met ? 'pos' : 'neg'}>{t.target_met ? '达标' : '未达标'}</b>{t.target_met ? `最少 ${t.min_trades_for_target} 笔` : '显示最高可行收益'}</div>
        <div>单笔最大投入<b>{num(t.max_deployed_usd, 0)}</b>{t.liquidity_capped_any ? '受流动性限制' : '未受限制'}</div>
        <div>可验证性<b className={t.unverifiable ? 'neg' : 'pos'}>{t.unverifiable ? '不可验证' : '可验证'}</b>{t.liquidity_known ? '流动性已知' : '流动性未知'}</div>
      </div>
      {t.notes?.length > 0 && <div className="banner">{t.notes.map((n: string, i: number) => <div key={i}>• {n}</div>)}</div>}
      <Chart candles={d.candles} markers={which === 'max' ? t.markers_max : t.markers_target} />
      <div className="scroll"><table><thead><tr><th>#</th><th>买入时间</th><th>买入参考</th><th>买入成交</th><th>卖出时间</th><th>卖出参考</th><th>卖出成交</th><th>投入(USD)</th><th>数量</th><th>成本</th><th>净盈亏</th><th>净收益</th><th>持有(根)</th><th>流动性限制</th></tr></thead>
        <tbody>{trades.map((x: any, i: number) => <tr key={i}><td>{i + 1}</td><td>{x.buy_ts.slice(11, 16)}</td><td>{num(x.buy_ref, 3)}</td><td>{num(x.buy_exec, 3)}</td><td>{x.sell_ts.slice(11, 16)}</td><td>{num(x.sell_ref, 3)}</td><td>{num(x.sell_exec, 3)}</td><td>{num(x.size_usd)}</td><td>{num(x.qty, 4)}</td><td>{num(x.cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td className={cls(x.net_ret)}>{pct(x.net_ret, 3)}</td><td>{x.hold_bars}</td><td>{x.liquidity_capped ? '是' : '否'}</td></tr>)}
          {trades.length === 0 && <tr><td colSpan={14}>{which === 'max' ? '无交易：没有任何波段能覆盖成本' : '目标不可达：无达标路径'}</td></tr>}</tbody></table></div>
      {t.best_by_k && Object.keys(t.best_by_k).length > 0 && <div className="muted">交易次数 ≤ k 的最优净利润：{Object.entries(t.best_by_k).map(([k, v]: any) => `k=${k}: ${num(v)}`).join('；')}</div>}
    </div>
    <div className="card"><h2>多股票组合（轮动 vs 并行均分，中位本金）</h2>
      <div className="row">档位 <select value={pfTier} onChange={e => setPfTier(e.target.value)}>{['T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7'].map(x => <option key={x}>{x}</option>)}</select></div>
      {!pf ? <div>计算中…</div> : <>
        <div className="kpi" style={{ margin: '8px 0' }}>
          <div>本金<b>{num(pf.capital_usd, 0)}</b>目标 {pct(pf.target_return)}</div>
          <div>轮动<b className={cls(pf.sequential.return)}>{pct(pf.sequential.return, 3)}</b>{pf.sequential.n_trades} 笔</div>
          <div>并行均分 m={pf.parallel.m}<b className={cls(pf.parallel.return)}>{pct(pf.parallel.return, 3)}</b>{Object.keys(pf.parallel.allocation).join('/')}</div>
          <div>更优<b className={pf.target_met ? 'pos' : 'neg'}>{pf.best_mode === 'sequential' ? '轮动' : '并行均分'} {pct(pf.best_return, 3)}</b>{pf.target_met ? '达标' : '未达标'}</div></div>
        <div className="scroll"><table><thead><tr><th>#</th><th>股票</th><th>买入</th><th>买入成交</th><th>卖出</th><th>卖出成交</th><th>投入</th><th>数量</th><th>成本</th><th>净盈亏</th><th>净收益</th></tr></thead>
          <tbody>{pf.sequential.trades.map((x: any, i: number) => <tr key={i}><td>{i + 1}</td><td>{x.symbol}</td><td>{x.buy_ts.slice(11, 16)}</td><td>{num(x.buy_exec, 3)}</td><td>{x.sell_ts.slice(11, 16)}</td><td>{num(x.sell_exec, 3)}</td><td>{num(x.size_usd)}</td><td>{num(x.qty, 4)}</td><td>{num(x.cost_usd)}</td><td className={cls(x.net_pnl_usd)}>{num(x.net_pnl_usd)}</td><td>{pct(x.net_ret, 3)}</td></tr>)}</tbody></table></div>
        <div className="muted">{pf.notes.join('；')}</div></>}
    </div>
    <div className="card"><h2>全标的 × 七档总览 · {day}</h2>
      {sum?.tier_issues && <div className="banner">{sum.tier_issues.map((s: string, i: number) => <div key={i}>• {s}</div>)}<div>• 图片中的理财周期不等于日收益口径；此处只把档位百分比作为单日研究目标对照。</div></div>}
      {!sum ? <div>计算中（10 标的 × 21 档点）…</div> : <div className="scroll"><table><thead><tr><th>标的</th><th>档位</th><th>点</th><th>本金</th><th>目标/日</th><th>最高可实现</th><th>净利润</th><th>笔数</th><th>达标</th><th>最少笔数</th><th>单笔最大投入</th><th>流动性限制</th><th>可验证</th></tr></thead>
        <tbody>{sum.rows.map((r: any, i: number) => <tr key={i}><td>{r.symbol}{!r.tradable && <span className="tag">待验证</span>}</td><td>{r.tier}</td><td>{r.point}</td><td>{num(r.capital_usd, 0)}</td><td>{r.target_pct}%</td><td className={cls(r.max_net_return_pct)}>{r.max_net_return_pct.toFixed(3)}%</td><td>{num(r.max_net_profit_usd)}</td><td>{r.n_trades}</td><td className={r.target_met ? 'pos' : 'neg'}><b>{r.target_met ? '达标' : '未达标'}</b></td><td>{r.min_trades_for_target ?? '—'}</td><td>{num(r.max_deployed_usd, 0)}</td><td>{r.liquidity_capped ? '是' : '否'}</td><td className={r.unverifiable ? 'neg' : 'pos'}>{r.unverifiable ? '不可验证' : '可验证'}</td></tr>)}</tbody></table></div>}
    </div>
  </>
}
