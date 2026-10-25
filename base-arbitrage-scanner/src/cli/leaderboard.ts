import type { ScannerDb } from '../storage/db.js';
import type { BlockSummary } from '../runner.js';

const f = (n: number | null | undefined, d = 2) => n === null || n === undefined ? '-' : n.toFixed(d);
const pad = (s: string | number, w: number, right = false) => { s = String(s); return right ? s.padStart(w) : s.padEnd(w).slice(0, w); };

export function renderLeaderboard(db: ScannerDb, runId: string | undefined, top = 15): string {
  const where = runId ? 'WHERE run_id=?' : ''; const params = runId ? [runId] : [];
  const rows = db.all(`SELECT block, path, amount_in, gross_profit_usd, total_cost_usd, net_profit_usd, sim_kind, sim_ok, sim_net_profit_usd, sim_fail_reason, executable, data_source, quote_fail_reason
    FROM candidates ${where} ORDER BY (sim_ok=1) DESC, COALESCE(sim_net_profit_usd, -1e18) DESC, net_profit_usd DESC LIMIT ?`, ...params, top);
  const lines: string[] = [];
  lines.push(`${pad('#', 3)} ${pad('block', 10, true)} ${pad('borrow USDC', 12, true)} ${pad('quoteNet', 10, true)} ${pad('simNet', 10, true)} ${pad('sim', 24)} ${pad('exec', 4)} ${pad('src', 15)} path`);
  rows.forEach((r: any, i: number) => {
    const sim = r.sim_kind === 'NONE' ? 'not simulated' : r.sim_ok ? `PASS(${r.sim_kind})` : `FAIL:${(r.sim_fail_reason ?? '').slice(0, 12)}`;
    lines.push(`${pad(i + 1, 3)} ${pad(r.block, 10, true)} ${pad((Number(r.amount_in) / 1e6).toFixed(0), 12, true)} ${pad(f(r.net_profit_usd), 10, true)} ${pad(f(r.sim_net_profit_usd), 10, true)} ${pad(sim, 24)} ${pad(r.executable ? 'YES' : 'no', 4)} ${pad(r.data_source, 15)} ${r.path}`);
  });
  if (!rows.length) lines.push('(no candidates stored yet)');
  return lines.join('\n');
}

export function renderBlockStats(summaries: BlockSummary[], last = 12): string {
  const lines = [`${pad('block', 10, true)} ${pad('time(UTC)', 20)} ${pad('pools', 5, true)} ${pad('excl', 4, true)} ${pad('routes', 6, true)} ${pad('evals', 6, true)} ${pad('cand', 5, true)} ${pad('sim', 4, true)} ${pad('pass', 4, true)} ${pad('exec', 4, true)} ${pad('bestQuoteNet', 12, true)} ${pad('bestSimNet', 10, true)}`];
  for (const s of summaries.slice(-last)) lines.push(`${pad(s.block, 10, true)} ${pad(new Date(s.time * 1000).toISOString().replace('T', ' ').slice(0, 19), 20)} ${pad(s.poolsScanned, 5, true)} ${pad(s.poolsExcluded, 4, true)} ${pad(s.routes, 6, true)} ${pad(s.evaluations, 6, true)} ${pad(s.candidates, 5, true)} ${pad(s.simulated, 4, true)} ${pad(s.simPassed, 4, true)} ${pad(s.executable, 4, true)} ${pad(f(s.bestNetUsd, 3), 12, true)} ${pad(f(s.bestSimNetUsd, 3), 10, true)}`);
  return lines.join('\n');
}
