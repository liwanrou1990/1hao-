import { useEffect, useState } from 'react'
import { get, pct, num } from '../api'

export default function AI({ symbol, day }: { symbol: string; day: string }) {
  const [d, setD] = useState<any>(null)
  const [err, setErr] = useState('')
  const [h, setH] = useState<number>(0)
  useEffect(() => { setD(null); setErr(''); get(`/api/ai/${symbol}?day=${day}`).then(setD).catch(e => setErr(String(e))) }, [symbol, day])
  if (err) return <div className="card neg">{err}</div>
  if (!d) return <div className="card">训练 LightGBM walk-forward（4 周期 × 多折）…</div>
  const horizons: number[] = Array.from(new Set<number>(d.signals.map((s: any) => s.horizon)))
  const hh = h || horizons[0]
  const sig = d.signals.filter((s: any) => s.horizon === hh)
  const fired = sig.filter((s: any) => s.decision === 1)
  const reasons: Record<string, number> = {}
  sig.forEach((s: any) => { const k = s.reason.replace(/[\d.+-]+%/g, 'x%'); reasons[k] = (reasons[k] || 0) + 1 })
  return <>
    <div className="card"><h2>6. AI 信号（LightGBM，样本外） {d.simulated && <span className="tag">SIMULATED</span>}</h2>
      <table><thead><tr><th>周期(分)</th><th>折数</th><th>OOS AUC</th><th>OOS Brier</th><th>OOS样本</th><th>信号数</th><th>交易时均净收益</th><th>95% CI</th><th>命中率</th><th>统计优势</th></tr></thead>
        <tbody>{d.summary.map((m: any) => <tr key={m.horizon_min}><td>{m.horizon_min}</td><td>{m.folds}</td><td>{num(m.auc_oos_mean, 3)}</td><td>{num(m.brier_oos_mean, 3)}</td><td>{m.n_oos_bars}</td><td>{m.n_signals}</td><td>{pct(m.mean_net_ret_when_trading, 3)}</td><td>{m.ci95 ? `[${pct(m.ci95[0], 3)}, ${pct(m.ci95[1], 3)}]` : '—'}</td><td>{pct(m.hit_rate, 0)}</td><td className={m.has_edge ? 'pos' : 'neg'}>{m.has_edge ? '是' : '否 → 不交易'}</td></tr>)}</tbody></table>
      <div className="muted">{d.note}</div></div>
    <div className="grid2">
      <div className="card"><h2>概率校准（最近折，可靠性表）</h2>
        {d.folds.slice(-4).map((f: any) => <div key={f.fold + '-' + f.horizon}><b>折{f.fold} · {f.horizon}分 · 测试 {f.test_days[0]}~{f.test_days[1]}</b> · AUC {num(f.auc, 3)} · Brier {num(f.brier, 3)} · 阈值 {f.threshold == null ? '无(不交易)' : num(f.threshold, 3)} · 环境 {JSON.stringify(f.regimes_in_test)}
          <table><thead><tr><th>预测区间</th><th>n</th><th>平均预测</th><th>实际上涨率</th></tr></thead><tbody>{f.reliability.map((r: any) => <tr key={r.bin}><td>{r.bin}</td><td>{r.n}</td><td>{pct(r.pred, 1)}</td><td>{pct(r.obs, 1)}</td></tr>)}</tbody></table></div>)}</div>
      <div className="card"><h2>当日逐bar信号与不交易理由</h2>
        <div className="row"><span>周期：</span><select value={hh} onChange={e => setH(Number(e.target.value))}>{horizons.map(x => <option key={x} value={x}>{x} 分钟</option>)}</select><span className="muted">发出信号 {fired.length} / {sig.length} 根</span></div>
        <table><thead><tr><th>理由（归并）</th><th>K线数</th></tr></thead><tbody>{Object.entries(reasons).map(([k, v]) => <tr key={k}><td style={{ whiteSpace: 'normal' }}>{k}</td><td>{v}</td></tr>)}</tbody></table>
        <div className="scroll" style={{ marginTop: 8 }}><table><thead><tr><th>时间</th><th>P(上涨)</th><th>期望净收益</th><th>决策</th><th>解释（特征贡献）</th></tr></thead>
          <tbody>{(fired.length ? fired : sig.slice(60, 90)).map((s: any) => <tr key={s.idx}><td>{s.ts_ny.slice(11, 16)}</td><td>{pct(s.p_up, 1)}</td><td>{pct(s.ev, 3)}</td><td className={s.decision ? 'pos' : ''}>{s.decision ? '做多' : '不交易'}</td><td style={{ textAlign: 'left', whiteSpace: 'normal', fontSize: 11 }}>{s.decision ? s.explanation.map((e: any) => `${e.feature}(${e.contribution >= 0 ? '+' : ''}${e.contribution.toFixed(2)})`).join('，') : s.reason}</td></tr>)}</tbody></table></div></div>
    </div>
  </>
}
