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
  const topExec = db.all(`SELECT block, block_time, path, pools, amount_in, total_cost_usd, net_profit_usd, sim_net_profit_usd, sim_kind FROM candidates WHERE run_id=? AND executable=1 ORDER BY sim_net_profit_usd DESC LIMIT 10`, runId);
  const states = db.all(`SELECT COALESCE(opportunity_state,'NONE') s, COUNT(*) n FROM candidates WHERE run_id=? GROUP BY s`, runId);
  const stateCount = (k: string) => Number(states.find((x: any) => x.s === k)?.n ?? 0);
  const dist = db.all(`SELECT CASE WHEN v < -100 THEN 'a <-100' WHEN v < -10 THEN 'b -100..-10' WHEN v < 0 THEN 'c -10..0' WHEN v = 0 THEN 'd 0' WHEN v < 1 THEN 'e 0..1' WHEN v < 10 THEN 'f 1..10' WHEN v < 100 THEN 'g 10..100' ELSE 'h >=100' END b, COUNT(*) n FROM (SELECT net_profit_usd v FROM candidates WHERE run_id=? AND quote_ok=1) GROUP BY b ORDER BY b`, runId);
  const simDist = db.all(`SELECT CASE WHEN v < -100 THEN 'a <-100' WHEN v < -10 THEN 'b -100..-10' WHEN v < 0 THEN 'c -10..0' WHEN v = 0 THEN 'd 0' WHEN v < 1 THEN 'e 0..1' WHEN v < 10 THEN 'f 1..10' WHEN v < 100 THEN 'g 10..100' ELSE 'h >=100' END b, COUNT(*) n FROM (SELECT sim_net_profit_usd v FROM candidates WHERE run_id=? AND sim_net_profit_usd IS NOT NULL) GROUP BY b ORDER BY b`, runId);
  const opps = db.all(`SELECT * FROM opportunities WHERE run_id=? ORDER BY COALESCE(best_sim_net_usd, -1e18) DESC, best_net_usd DESC LIMIT 25`, runId);
  const oppAgg = db.get(`SELECT COUNT(*) n, SUM(best_state='VERIFIED_FORK') vf, SUM(invalidated_next_block) inv, AVG(samples_seen) avg_samples, AVG(blocks_sampled_between) avg_blocks, MAX(blocks_sampled_between) max_blocks FROM opportunities WHERE run_id=?`, runId) ?? {};
  const inval = db.all(`SELECT reason, COUNT(*) n FROM invalidations WHERE run_id=? GROUP BY reason ORDER BY n DESC`, runId);
  const txCost = db.get(`SELECT AVG(gas_l2_usd + gas_l1_usd) avg_gas, MAX(gas_l2_usd + gas_l1_usd) max_gas, AVG(gas_units) avg_units, AVG(sim_gas_used) avg_sim_gas, AVG(flash_premium_usd) avg_prem, AVG(total_cost_usd) avg_total FROM candidates WHERE run_id=? AND quote_ok=1`, runId) ?? {};
  const sampleEvery = db.get(`SELECT MIN(b2.block - b1.block) d FROM block_stats b1 JOIN block_stats b2 ON b1.run_id=b2.run_id AND b2.block > b1.block WHERE b1.run_id=?`, runId)?.d;
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
  L.push('', '## Four opportunity states (mutually exclusive, highest reached per stored row)', '', '| state | meaning | rows |', '|---|---|---|',
    `| THEORETICAL_SPREAD | mid-price spread > 0 but executable quotes not profitable — never actionable | ${stateCount('THEORETICAL_SPREAD')} |`,
    `| QUOTED_ESTIMATE | ExactInput quotes − all costs > 0, not confirmed by fork | ${stateCount('QUOTED_ESTIMATE')} |`,
    `| VERIFIED_FORK | atomic flashLoanSimple→swap→repay on a real-chain fork repaid principal+premium with net > 0 | ${stateCount('VERIFIED_FORK')}${real ? '' : ' (impossible on synthetic data)'} |`,
    `| LIVE_EXECUTED | real mainnet transaction confirmed | **0 — phase 1 never broadcasts** |`,
    '', `Real sampled blocks: **${real ? bs.n ?? 0 : 0}** (${real ? 'ONCHAIN' : `data source is ${ds}, so 0 real blocks`}); sampling interval observed in block_stats: ${sampleEvery ?? 'n/a'} blocks.`);
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
  L.push('', '## Net profit distribution (USD buckets)', '', '| bucket | quoted net rows | simulated net rows |', '|---|---|---|');
  const buckets = ['a <-100', 'b -100..-10', 'c -10..0', 'd 0', 'e 0..1', 'f 1..10', 'g 10..100', 'h >=100'];
  for (const b of buckets) L.push(`| ${b.slice(2)} | ${dist.find((x: any) => x.b === b)?.n ?? 0} | ${simDist.find((x: any) => x.b === b)?.n ?? 0} |`);
  L.push('', '## Estimated transaction cost', '', `| metric | value |`, `|---|---|`, `| avg gas (L2+L1) per candidate | ${f(txCost.avg_gas)} USD |`, `| max gas (L2+L1) | ${f(txCost.max_gas)} USD |`, `| avg modeled gas units | ${f(txCost.avg_units, 0)} |`, `| avg fork-measured gas (eth_estimateGas) | ${f(txCost.avg_sim_gas, 0)} |`, `| avg Aave premium | ${f(txCost.avg_prem)} USD |`, `| avg total explicit cost (premium+gas+buffer) | ${f(txCost.avg_total)} USD |`);
  L.push('', '## Opportunity duration and invalidation (de-duplicated across consecutive samples)', '', `- distinct opportunities (route × amount, consecutive samples merged): **${oppAgg.n ?? 0}**; reached VERIFIED_FORK: **${oppAgg.vf ?? 0}**`, `- invalidated at the next sampled block (state change / competition / re-quote failure): **${oppAgg.inv ?? 0}**`, `- avg samples seen: ${f(oppAgg.avg_samples, 2)}; avg span: ${f(oppAgg.avg_blocks, 1)} blocks (~${f((oppAgg.avg_blocks ?? 0) * 2, 0)} s); max span: ${oppAgg.max_blocks ?? 0} blocks`, '', '**Next-block re-check outcomes:**', '', ...(inval.length ? inval.map((r: any) => `- ${r.reason}: ${r.n}`) : ['- none']));
  if (opps.length) { L.push('', '| first block | last block | samples | span (blocks) | best state | best quoted net | best sim net | best borrow | invalidated next | path |', '|---|---|---|---|---|---|---|---|---|---|'); for (const o of opps) L.push(`| ${o.first_block} | ${o.last_block} | ${o.samples_seen} | ${o.blocks_sampled_between} | ${o.best_state} | ${f(o.best_net_usd)} | ${f(o.best_sim_net_usd)} | ${(Number(o.best_amount_in) / 1e6).toLocaleString('en-US')} | ${o.invalidated_next_block ? 'yes' : (o.invalidation_reason ?? 'n/a')} | ${o.path} |`); }
  L.push('', '## Failure distribution', '', '**Quote stage (route×amount evaluations, stored subset):**', '', ...(qfail.length ? qfail.map((r: any) => `- ${r.r}: ${r.n}`) : ['- none']), '', '**Per-block aggregated failure counters (all evaluations):**', '', ...(Object.keys(agg).length ? Object.entries(agg).sort((a, b) => b[1] - a[1]).map(([k, v]) => `- ${k}: ${v}`) : ['- none']), '', '**Simulation stage:**', '', ...(sfail.length ? sfail.map((r: any) => `- ${r.reason.slice(0, 160)}: ${r.n}`) : ['- none']), '', '**RPC / block failures:**', '', ...(rpcfail.length ? rpcfail.map((r: any) => `- ${r.method}: ${r.n}`) : ['- none']));
  if (extra.failedBlocks?.length) L.push('', ...extra.failedBlocks.slice(0, 20).map(b => `- block ${b.block}: ${b.reason}`));
  L.push('', '## VERIFIED_FORK opportunities (fork-verified, NOT LIVE_EXECUTED)', '');
  if (!real) L.push(`**None can exist in this run: data_source=${ds} is synthetic.** No row can reach VERIFIED_FORK or LIVE_EXECUTED.`);
  else if (!topExec.length) L.push('**No VERIFIED_FORK opportunity.** No candidate passed the atomic fork simulation with net profit ≥ threshold in this run. LIVE_EXECUTED: 0 (phase 1 never broadcasts).');
  else { L.push(`**${topExec.length} candidate(s) reached VERIFIED_FORK (simulated on a fork of real chain state; realized profit = none, LIVE_EXECUTED = 0):**`, '', '| block | time (UTC) | borrow USDC | total cost | quoted net | sim net | pools | path |', '|---|---|---|---|---|---|---|---|'); for (const t of topExec) L.push(`| ${t.block} | ${new Date(t.block_time * 1000).toISOString()} | ${(Number(t.amount_in) / 1e6).toLocaleString('en-US')} | ${f(t.total_cost_usd)} | ${f(t.net_profit_usd)} | ${f(t.sim_net_profit_usd)} | ${String(t.pools).replace(/>/g, ' → ')} | ${t.path} |`); }
  L.push('', '---', 'Definitions: *theoretical spread* = displayed-price difference (never used to flag opportunities); *simulated profit* = output of the atomic flash-loan path simulation; *real profit* = none (no transactions sent in phase 1).');
  return L.join('\n');
}

export function writeReport(path: string, content: string) { mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, content); }
