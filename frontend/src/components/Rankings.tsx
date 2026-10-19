import { useEffect, useState } from 'react'
import { get, num, cls } from '../api'

export default function Rankings({ day }: { day: string }) {
  const [data, setData] = useState<any>(null)
  const [err, setErr] = useState('')
  useEffect(() => { get(`/api/rankings?day=${day}`).then(setData).catch(e => setErr(String(e))) }, [day])
  if (err) return <div className="card neg">{err}</div>
  if (!data) return <div className="card">加载中…</div>
  return <div className="card">
    <h2>1. 每日涨跌与行情排名 · {data.day} {data.simulated && <span className="tag">SIMULATED 模拟数据</span>}</h2>
    <div className="scroll"><table><thead><tr><th>#</th><th>标的</th><th>开盘</th><th>收盘</th><th>涨跌</th><th>振幅</th><th>成交额(USD)</th><th>代币收盘</th><th>代币基差(bps)</th><th>环境(模拟)</th><th>来源</th><th>可交易</th></tr></thead>
      <tbody>{data.rows.map((r: any) => <tr key={r.symbol}><td>{r.rank}</td><td><b>{r.symbol}</b></td><td>{num(r.open)}</td><td>{num(r.close)}</td>
        <td className={cls(r.change_pct)}>{r.change_pct.toFixed(2)}%</td><td>{r.range_pct.toFixed(2)}%</td><td>{num(r.amount_usd, 0)}</td>
        <td>{num(r.token_close)}</td><td>{num(r.token_basis_bps, 1)}</td><td>{r.regime_sim ?? '—'}</td><td><span className="tag">{r.source}</span></td><td>{r.tradable ? '是' : '否（待验证）'}</td></tr>)}</tbody></table></div>
    <div className="muted">代币收盘来自代币 venue（链上成交/发行商报价），与原生股票价格分开存放，不混同；基差 = 代币/原生 − 1。</div>
  </div>
}
