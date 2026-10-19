import { useEffect, useState } from 'react'
import { get, post } from '../api'

export default function Reports({ day }: { day: string }) {
  const [items, setItems] = useState<any[]>([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const load = () => get('/api/reports').then(d => setItems(d.items))
  useEffect(() => { load() }, [])
  const gen = async (fast: boolean) => { setBusy(true); setMsg('生成中（全部标的，含回测与 ML 可能需数分钟）…'); try { const r = await post(`/api/report?day=${day}&with_ml=${!fast}&with_backtest=${!fast}`); setMsg(`已生成：${r.html_url}（${r.n_trades} 笔交易明细）${r.pdf ? '，含 PDF' : '，PDF 需安装 weasyprint'}`); load() } catch (e) { setMsg(String(e)) } finally { setBusy(false) } }
  return <div className="card"><h2>8. 每日报告（中文 HTML / CSV 交易明细 / PDF 可选）</h2>
    <div className="row"><button className="primary" disabled={busy} onClick={() => gen(false)}>生成 {day} 完整日报</button><button className="ghost" disabled={busy} onClick={() => gen(true)}>快速日报（仅 A/B 复盘）</button><span className="muted">{msg}</span></div>
    <table><thead><tr><th>文件</th><th>大小</th><th>时间</th></tr></thead><tbody>{items.map(i => <tr key={i.url}><td><a href={i.url} target="_blank">{i.url}</a></td><td>{(i.size / 1024).toFixed(1)} KB</td><td>{i.mtime.slice(0, 19)}</td></tr>)}</tbody></table>
    <div className="muted">自动日报：crontab 示例 <code>30 17 * * 1-5 cd 项目目录 &amp;&amp; bgwq report</code>（美东收盘后）。</div></div>
}
