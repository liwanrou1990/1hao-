import { useEffect, useState } from 'react'
import { get } from './api'
import Rankings from './components/Rankings'
import Replay from './components/Replay'
import Backtest from './components/Backtest'
import AI from './components/AI'
import Training from './components/Training'
import Reports from './components/Reports'
import Hindsight from './components/Hindsight'
import Target from './components/Target'
import Simple from './components/Simple'
import Compare from './components/Compare'

const TABS = ['档位对比', '多空教学模拟', '固定收益目标', '事后最优路径', '行情排名', '复盘与交易', '回测/本金容量', 'AI 信号', '训练模式', '日报']

export default function App() {
  const [status, setStatus] = useState<any>(null)
  const [syms, setSyms] = useState<any>(null)
  const [symbol, setSymbol] = useState('NVDA')
  const [day, setDay] = useState('')
  const [tab, setTab] = useState(0)
  const [err, setErr] = useState('')
  useEffect(() => {
    get('/api/status').then(s => { setStatus(s); if (s.latest_day) setDay(s.latest_day) }).catch(e => setErr(String(e)))
    get('/api/symbols').then(setSyms).catch(() => {})
  }, [])
  if (err) return <div style={{ padding: 20 }} className="neg">后端不可用：{err}。请先运行 <code>bgwq serve</code>。</div>
  if (!status || !day) return <div style={{ padding: 20 }}>初始化（首次需生成/接入 70 个交易日数据）…</div>
  const days: string[] = syms?.days?.[symbol] ?? [day]
  return <>
    <header><h1>Bitget Wallet 美股代币化 AI 量化复盘与交易员训练系统</h1>
      <span className={`mode ${status.data_mode === 'live' ? 'live' : ''}`}>{status.data_mode === 'live' ? 'LIVE 数据' : 'SIMULATED 模拟数据'}</span>
      <span style={{ fontSize: 12 }}>成本模型 {status.cost_profile} · 本金 {status.capital_usd} USD · 执行模式 {status.execution.mode}（不连钱包/不签名/不下单）</span></header>
    <nav>{TABS.map((t, i) => <button key={t} className={tab === i ? 'active' : ''} onClick={() => setTab(i)}>{t}</button>)}
      <span style={{ marginLeft: 'auto' }} className="row">标的 <select value={symbol} onChange={e => setSymbol(e.target.value)}>{(syms?.symbols ?? ['NVDA']).map((s: string) => <option key={s} value={s}>{s}{syms?.tradable?.includes(s) ? '' : '（待验证）'}</option>)}</select>
        日期 <select value={day} onChange={e => setDay(e.target.value)}>{[...days].reverse().map(d => <option key={d}>{d}</option>)}</select></span></nav>
    <main>
      {status.data_mode !== 'live' && <div className="banner">{status.banner} 阻塞项：{status.blockers?.slice(0, 2).join('；')}</div>}
      {tab === 0 && <Compare symbol={symbol} day={day} />}
      {tab === 1 && <Simple symbol={symbol} day={day} />}
      {tab === 2 && <Target symbol={symbol} day={day} />}
      {tab === 3 && <Hindsight symbol={symbol} day={day} />}
      {tab === 4 && <Rankings day={day} />}
      {tab === 5 && <Replay symbol={symbol} day={day} />}
      {tab === 6 && <Backtest symbol={symbol} />}
      {tab === 7 && <AI symbol={symbol} day={day} />}
      {tab === 8 && <Training symbol={symbol} day={day} />}
      {tab === 9 && <Reports day={day} />}
    </main>
  </>
}
