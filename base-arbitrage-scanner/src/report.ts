import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ScannerDb } from './storage/db.js';

const f = (n: number | null | undefined, d = 4) => n === null || n === undefined || Number.isNaN(n) ? 'n/a' : Number(n).toFixed(d);

/** Markdown report for one run, built only from what is stored in SQLite. */
export function buildReport(db: ScannerDb, runId: string, extra: { title: string; notes?: string[]; plan?: string; failedBlocks?: Array<{ block: bigint; reason: string }> }): string {
  const run = db.get('SELECT * FROM runs WHERE run_id=?', runId);
  const bs = db.get(`SELECT COUNT(*) n, MIN(block) b0, MAX(block) b1, MIN(block_time) t0, MAX(block_time) t1, SUM(evaluations) evals, SUM(candidates) cands, SUM(simulated) sims, SUM(sim_passed) pass, SUM(executable) exec, AVG(pools_scanned) pools, MAX(pools_scanned) maxpools, AVG(routes) routes, AVG(eth_usdc) eth, MAX(flash_premium_bps) prem FROM block_stats WHERE run_id=?`, runId) ?? {};
  const pools = db.all('SELECT dex, COUNT(*) n FROM pools GROUP BY dex');
  const poolList = db.all('SELECT label, address FROM pools ORDER BY dex, label');
  const cand = db.get(`SELECT COUNT(*) n, SUM(quote_ok) ok, SUM(net_profit_usd>0) pos, MAX(net_profit_usd) maxnet, AVG(CASE WHEN quote_ok=1 THEN net_profit_usd END) avgnet, SUM(sim_kind!='NONE') simd, SUM(sim_ok=1) simok, MAX(sim_net_profit_usd) maxsim, AVG(CASE WHEN sim_ok=1 THEN sim_net_profit_usd END) avgsim, SUM(executable) exec FROM candidates WHERE run_id=?`, runId) ?? {};
  const bestAmt = db.all(`SELECT amount_in, SUM(evaluations) n, SUM(quote_ok) ok, AVG(avg_net_usd) avgnet, MAX(best_net_usd) maxnet, SUM(best_net_usd > 0) blocks_pos, MAX(best_sim_net_usd) maxsim, COUNT(*) blocks FROM ladder_stats WHERE run_id=? GROUP BY amount_in ORDER BY CAST(amount_in AS INTEGER)`, runId);
  const ladderFails = db.all(`SELECT amount_in, fail_reasons FROM ladder_stats WHERE run_id=?`, runId);
  const lf: Record<string, Record<string, number>> = {}; for (const r of ladderFails) { try { const o = JSON.parse(r.fail_reasons); for (const [k, v] of Object.entries(o)) { lf[r.amount_in] ??= {}; lf[r.amount_in][k] = (lf[r.amount_in][k] ?? 0) + Number(v); } } catch { /* ignore */ } }
  const optBest = db.get(`SELECT amount_in, net_profit_usd, sim_net_profit_usd, path, block FROM candidates WHERE run_id=? AND quote_ok=1 ORDER BY COALESCE(sim_net_profit_usd,-1e18) DESC, net_profit_usd DESC LIMIT 1`, runId);
  const fees = db.get(`SELECT AVG(theoretical_spread_usd) theo, AVG(gross_profit_usd) gross, AVG(dex_fees_usd) dex, AVG(price_impact_usd) imp, AVG(flash_premium_usd) fl, AVG(gas_l2_usd) g2, AVG(gas_l1_usd) g1, AVG(priority_fee_usd) prio, AVG(buffer_usd) buf, AVG(total_cost_usd) tot, AVG(net_profit_usd) net FROM candidates WHERE run_id=? AND quote_ok=1 AND net_profit_usd > -1e9`, runId) ?? {};
  const feesTop = db.get(`SELECT theoretical_spread_usd theo, gross_profit_usd gross, dex_fees_usd dex, price_impact_usd imp, flash_premium_usd fl, gas_l2_usd g2, gas_l1_usd g1, priority_fee_usd prio, buffer_usd buf, total_cost_usd tot, net_profit_usd net, amount_in, path, block FROM candidates WHERE run_id=? AND quote_ok=1 ORDER BY net_profit_usd DESC LIMIT 1`, runId);
  const qfail = db.all(`SELECT COALESCE(quote_fail_reason,'ok') r, COUNT(*) n FROM candidates WHERE run_id=? GROUP BY r ORDER BY n DESC LIMIT 15`, runId);
  const sfail = db.all(`SELECT reason, COUNT(*) n FROM sim_failures WHERE run_id=? GROUP BY reason ORDER BY n DESC LIMIT 15`, runId);
  const rpcfail = db.all(`SELECT method, COUNT(*) n FROM rpc_failures WHERE run_id=? GROUP BY method ORDER BY n DESC LIMIT 15`, runId);
  const blockFail = db.all(`SELECT failures_json FROM block_stats WHERE run_id=?`, runId);
  const agg: Record<string, number> = {}; for (const r of blockFail) { try { for (const [k, v] of Object.entries(JSON.parse(r.failures_json))) agg[k] = (agg[k] ?? 0) + Number(v); } catch { /* ignore */ } }
  const topExec = db.all(`SELECT block, path, amount_in, net_profit_usd, sim_net_profit_usd, sim_kind FROM candidates WHERE run_id=? AND executable=1 ORDER BY sim_net_profit_usd DESC LIMIT 10`, runId);
  const ds = run?.data_source ?? 'unknown';
  const real = ds === 'ONCHAIN';
  const L: string[] = [];
  L.push(`# ${extra.title}`, '', `- run_id: \`${runId}\`  mode: **${run?.mode}**  data source: **${ds}**`, `- generated: ${new Date().toISOString()}`);
  if (!real) L.push('', `> **WARNING: data_source=${ds}.** This run did NOT use Base mainnet data. Numbers below exercise the pipeline on synthetic liquidity and carry no information about real arbitrage opportunities.`);
  if (extra.plan) L.push(`- sampling rule: ${extra.plan}`);
  for (const n of extra.notes ?? []) L.push(`- ${n}`);
  L.push('', '## Scan coverage', '', `| metric | value |`, `|---|---|`, `| blocks scanned | ${bs.n ?? 0} |`, `| block range | ${bs.b0 ?? '-'} .. ${bs.b1 ?? '-'} |`, `| time range (UTC) | ${bs.t0 ? new Date(bs.t0 * 1000).toISOString() : '-'} .. ${bs.t1 ? new Date(bs.t1 * 1000).toISOString() : '-'} |`, `| verified pools (total / avg active per block) | ${poolList.length} / ${f(bs.pools, 1)} |`, `| routes per block (avg) | ${f(bs.routes, 1)} |`, `| route×amount evaluations | ${bs.evals ?? 0} |`, `| candidates (quote net > 0) | ${bs.cands ?? 0} |`, `| simulated | ${bs.sims ?? 0} |`, `| simulation passed | ${bs.pass ?? 0} |`, `| executable (FORK_ATOMIC pass AND net ≥ threshold) | ${bs.exec ?? 0} |`, `| Aave FLASHLOAN_PREMIUM_TOTAL (bps) | ${bs.prem ?? '-'} |`, `| ETH/USDC used for gas (avg) | ${f(bs.eth, 2)} |`);
  if (extra.failedBlocks?.length) L.push(`| blocks failed (not evaluated) | ${extra.failedBlocks.length} |`);
  L.push('', `Pools by DEX: ${pools.map((p: any) => `${p.dex}=${p.n}`).join(', ') || 'none'}`, '', '<details><summary>pool list</summary>', '', ...poolList.map((p: any) => `- ${p.label} \`${p.address}\``), '', '</details>');
  L.push('', '## Profit summary (three distinct notions)', '', '| notion | definition | value |', '|---|---|---|',
    `| theoretical spread | mid-price difference, zero fee / zero impact — NOT tradable | avg ${f(fees.theo)} USD |`,
    `| quoted net profit | ExactInput quotes − flash premium − gas(L2+L1) − buffer | max ${f(cand.maxnet)} / avg ${f(cand.avgnet)} USD, ${cand.pos ?? 0} of ${cand.ok ?? 0} evaluations > 0 |`,
    `| simulated net profit | atomic path on fork/sim − gas − buffer | max ${f(cand.maxsim)} / avg ${f(cand.avgsim)} USD, ${cand.simok ?? 0} of ${cand.simd ?? 0} simulations passed |`,
    `| real executed profit | actual on-chain fills | **none — phase 1 sends no transactions** |`);
  L.push('', '## Optimal borrow amount (all route×amount evaluations, per block aggregates)', '', '| borrow USDC | evaluations | quotes ok | avg quoted net (USD) | max quoted net (USD) | blocks with best net > 0 | max simulated profit (USD, pre-gas) | top failure reasons |', '|---|---|---|---|---|---|---|---|');
  for (const b of bestAmt) L.push(`| ${(Number(b.amount_in) / 1e6).toLocaleString('en-US')} | ${b.n} | ${b.ok} | ${f(b.avgnet)} | ${f(b.maxnet)} | ${b.blocks_pos ?? 0} / ${b.blocks} | ${f(b.maxsim)} | ${Object.entries(lf[b.amount_in] ?? {}).sort((x, y) => y[1] - x[1]).slice(0, 3).map(([k, v]) => `${k}=${v}`).join(', ') || '-'} |`);
  if (optBest) L.push('', `Best single evaluation (ladder + golden-section optimizer): borrow **${(Number(optBest.amount_in) / 1e6).toLocaleString('en-US')} USDC** at block ${optBest.block}, quoted net ${f(optBest.net_profit_usd)} USD, sim net ${f(optBest.sim_net_profit_usd)} USD — ${optBest.path}`);
  L.push('', '## Cost composition', '', '| component | avg over evaluations (USD) | best evaluation (USD) |', '|---|---|---|');
  const comp: Array<[string, keyof typeof fees]> = [['theoretical spread', 'theo'], ['gross profit (quotes; fees+impact embedded)', 'gross'], ['DEX fees (embedded)', 'dex'], ['price impact (embedded)', 'imp'], ['Aave flash premium', 'fl'], ['gas L2 execution', 'g2'], ['gas L1 data fee', 'g1'], ['  of which priority fee', 'prio'], ['execution buffer', 'buf'], ['total explicit cost', 'tot'], ['net profit', 'net']];
  for (const [n, k] of comp) L.push(`| ${n} | ${f(fees[k])} | ${f(feesTop?.[k])} |`);
  if (feesTop) L.push('', `Best evaluation: block ${feesTop.block}, borrow ${(Number(feesTop.amount_in) / 1e6).toLocaleString('en-US')} USDC — ${feesTop.path}`);
  L.push('', '## Failure distribution', '', '**Quote stage (route×amount evaluations, stored subset):**', '', ...(qfail.length ? qfail.map((r: any) => `- ${r.r}: ${r.n}`) : ['- none']), '', '**Per-block aggregated failure counters (all evaluations):**', '', ...(Object.keys(agg).length ? Object.entries(agg).sort((a, b) => b[1] - a[1]).map(([k, v]) => `- ${k}: ${v}`) : ['- none']), '', '**Simulation stage:**', '', ...(sfail.length ? sfail.map((r: any) => `- ${r.reason.slice(0, 160)}: ${r.n}`) : ['- none']), '', '**RPC / block failures:**', '', ...(rpcfail.length ? rpcfail.map((r: any) => `- ${r.method}: ${r.n}`) : ['- none']));
  if (extra.failedBlocks?.length) L.push('', ...extra.failedBlocks.slice(0, 20).map(b => `- block ${b.block}: ${b.reason}`));
  L.push('', '## Verified positive-return opportunities', '');
  if (!real) L.push(`**None can exist in this run: data_source=${ds} is synthetic.** The ${cand.exec ?? 0} rows flagged "executable" below only demonstrate the pipeline on synthetic liquidity.`);
  else if (!topExec.length) L.push('**No verified positive-return opportunity.** No candidate passed the atomic fork simulation with net profit ≥ threshold in this run.');
  else L.push(`**${topExec.length} candidate(s) passed atomic fork simulation with positive net (simulated, NOT realized):**`);
  for (const t of topExec) L.push(`- block ${t.block}: borrow ${(Number(t.amount_in) / 1e6).toLocaleString('en-US')} USDC, quoted net ${f(t.net_profit_usd)}, sim net ${f(t.sim_net_profit_usd)} (${t.sim_kind}) — ${t.path}`);
  L.push('', '---', 'Definitions: *theoretical spread* = displayed-price difference (never used to flag opportunities); *simulated profit* = output of the atomic flash-loan path simulation; *real profit* = none (no transactions sent in phase 1).');
  return L.join('\n');
}

export function writeReport(path: string, content: string) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, content); }
