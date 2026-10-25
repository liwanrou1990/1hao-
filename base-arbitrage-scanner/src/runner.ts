import type { Address } from 'viem';
import type { AppConfig } from './config.js';
import type { MarketSource } from './dex/types.js';
import { ScanEngine, type BlockScanResult, type Evaluation } from './scanner/scan.js';
import { ScannerDb, type CandidateRow, type DataSource, type SimKind } from './storage/db.js';
import type { Route } from './scanner/routes.js';
import { log } from './log.js';

export interface SimOutcome { kind: SimKind; ok: boolean; amountOut?: bigint; premium?: bigint; profit?: bigint; gasUsed?: bigint; failReason?: string; block: bigint; }
export type SimulateFn = (route: Route, asset: Address, amount: bigint, block: bigint) => Promise<SimOutcome>;

export interface BlockSummary { block: number; time: number; poolsScanned: number; poolsExcluded: number; routes: number; evaluations: number; candidates: number; simulated: number; simPassed: number; executable: number; bestNetUsd: number | null; bestSimNetUsd: number | null; failures: Record<string, number>; }

/** Scans one block, simulates the candidates and persists everything. Shared by live scan and backtest. */
export class Runner {
  constructor(public engine: ScanEngine, public src: MarketSource, public db: ScannerDb, public cfg: AppConfig, public runId: string, public mode: 'live' | 'backtest', public dataSource: DataSource, public simulate?: SimulateFn, public maxSimPerBlock = 10) {}

  private rowFor(e: Evaluation, r: BlockScanResult, sim?: SimOutcome): CandidateRow {
    const usdcDec = this.src.tokens().USDC.decimals; const toUsd = (v?: bigint) => v === undefined ? null : Number(v) / 10 ** usdcDec;
    const c = e.cost;
    const simProfit = sim ? toUsd(sim.profit) : null;
    // simulated net = on-fork profit (already net of DEX fees, impact and flash premium) - gas - buffer, using sim gasUsed when available
    let simNet: number | null = null;
    if (sim && sim.ok && simProfit !== null && c) {
      const ethUsd = r.gas.ethUsdc || 0; const gasUnits = sim.gasUsed ?? BigInt(c.gasUnits);
      const gasUsd = Number((r.gas.baseFeePerGas + r.gas.maxPriorityFeePerGas) * gasUnits) / 1e18 * ethUsd + c.gasL1DataUsd;
      simNet = simProfit - gasUsd - c.executionBufferUsd;
    }
    const executable = sim?.kind === 'FORK_ATOMIC' && sim.ok && simNet !== null && simNet >= this.cfg.minNetProfitUsd ? 1 : 0;
    return {
      run_id: this.runId, mode: this.mode, data_source: this.dataSource, block: Number(r.block), block_time: r.timestamp, route_id: e.route.id, path: e.route.path, hops: e.route.hops,
      tokens: [e.route.legs[0].symbolIn, ...e.route.legs.map(l => l.symbolOut)].join('->'), dexes: e.route.legs.map(l => l.pool.dex).join('>'), pools: e.route.legs.map(l => l.pool.address).join('>'),
      amount_in: e.amountIn.toString(), amount_out: e.amountOut.toString(), amount_source: e.amountSource,
      theoretical_spread_usd: c?.theoreticalSpreadUsd ?? 0, gross_profit_usd: c?.grossProfitUsd ?? 0, dex_fees_usd: c?.dexFeesUsd ?? 0, price_impact_usd: c?.priceImpactUsd ?? 0, flash_premium_bps: r.fl.premiumBps, flash_premium_usd: c?.flashLoanPremiumUsd ?? 0,
      gas_units: c?.gasUnits ?? 0, gas_l2_usd: c?.gasL2Usd ?? 0, gas_l1_usd: c?.gasL1DataUsd ?? 0, priority_fee_usd: c?.priorityFeeUsd ?? 0, buffer_usd: c?.executionBufferUsd ?? 0, total_cost_usd: c?.totalCostUsd ?? 0, net_profit_usd: c?.netProfitUsd ?? 0, roi: c?.roi ?? 0,
      quote_ok: e.ok ? 1 : 0, quote_fail_reason: e.failReason ?? null, sim_kind: sim?.kind ?? 'NONE', sim_ok: sim ? (sim.ok ? 1 : 0) : null, sim_profit_usd: simProfit, sim_gas_used: sim?.gasUsed === undefined ? null : Number(sim.gasUsed), sim_fail_reason: sim?.failReason ?? null, sim_net_profit_usd: simNet,
      executable, quotes_json: JSON.stringify(e.quotes.map(q => ({ pool: q.pool, in: q.amountIn.toString(), out: q.amountOut.toString(), ok: q.ok, err: q.error, impactBps: q.priceImpactBps, gas: q.gasEstimate?.toString() }))),
    };
  }

  async runBlock(block: bigint): Promise<BlockSummary> {
    const r = await this.engine.scanBlock(block);
    const usdc = this.src.tokens().USDC;
    for (const [, s] of r.states) this.db.insertSnapshot(block, s);
    // simulate top candidates (pre-sim net > 0), limited per block
    const toSim = r.candidates.slice(0, this.maxSimPerBlock);
    const sims = new Map<Evaluation, SimOutcome>();
    let simPassed = 0, executable = 0, bestSimNet: number | null = null;
    if (this.simulate) for (const e of toSim) {
      try { const s = await this.simulate(e.route, usdc.address, e.amountIn, block); sims.set(e, s); if (!s.ok) this.db.insertSimFailure(this.runId, block, e.route.id, s.failReason ?? 'unknown'); }
      catch (err: any) { const reason = `sim_error: ${err?.message ?? err}`; sims.set(e, { kind: 'NONE', ok: false, failReason: reason, block }); this.db.insertSimFailure(this.runId, block, e.route.id, reason); r.failures[reason.slice(0, 60)] = (r.failures[reason.slice(0, 60)] ?? 0) + 1; }
    }
    // persist: all candidates + the top 20 evaluations by net (so losing routes and fail reasons are kept too) + ladder of best route
    const rows: CandidateRow[] = [];
    const keep = new Set<Evaluation>(r.candidates);
    [...r.evaluations].filter(e => e.ok && e.cost).sort((a, b) => b.cost!.netProfitUsd - a.cost!.netProfitUsd).slice(0, 20).forEach(e => keep.add(e));
    const failed = r.evaluations.filter(e => !e.ok); failed.slice(0, 30).forEach(e => keep.add(e));
    for (const e of keep) {
      const sim = sims.get(e);
      const row = this.rowFor(e, r, sim);
      if (row.sim_ok === 1) simPassed++; if (row.executable) executable++;
      if (row.sim_net_profit_usd !== null && (bestSimNet === null || row.sim_net_profit_usd > bestSimNet)) bestSimNet = row.sim_net_profit_usd;
      // duplicate filtering: same (route, amount) with unchanged net within dedupeBlocks is not re-inserted unless it was simulated
      const prev = this.db.lastSimilar(row.route_id, row.amount_in, this.cfg.dedupeBlocks, row.block);
      if (prev && !sim && Math.abs(prev.net_profit_usd - row.net_profit_usd) < 0.005) continue;
      rows.push(row);
    }
    for (const row of rows) this.db.insertCandidate(row);
    // unbiased per-amount ladder statistics over ALL evaluations of this block (not only stored rows)
    const byAmt = new Map<string, { n: number; ok: number; best: number | null; sum: number; bestSim: number | null; fails: Record<string, number> }>();
    for (const e of r.evaluations) {
      if (e.amountSource !== 'LADDER') continue;
      const k = e.amountIn.toString(); let a = byAmt.get(k); if (!a) { a = { n: 0, ok: 0, best: null, sum: 0, bestSim: null, fails: {} }; byAmt.set(k, a); }
      a.n++; if (e.ok && e.cost) { a.ok++; a.sum += e.cost.netProfitUsd; if (a.best === null || e.cost.netProfitUsd > a.best) a.best = e.cost.netProfitUsd; } else { const f = (e.failReason ?? 'unknown').replace(/:.*$/, ''); a.fails[f] = (a.fails[f] ?? 0) + 1; }
      const sim = sims.get(e); if (sim?.ok && sim.profit !== undefined) { const v = Number(sim.profit) / 10 ** usdc.decimals; if (a.bestSim === null || v > a.bestSim) a.bestSim = v; }
    }
    for (const [amount_in, a] of byAmt) this.db.insertLadderStat({ run_id: this.runId, block: Number(block), amount_in, evaluations: a.n, quote_ok: a.ok, best_net_usd: a.best, avg_net_usd: a.ok ? a.sum / a.ok : null, best_sim_net_usd: a.bestSim, fail_reasons: JSON.stringify(a.fails) });
    const best = r.candidates[0]?.cost?.netProfitUsd ?? ([...r.evaluations].filter(e => e.ok && e.cost).sort((a, b) => b.cost!.netProfitUsd - a.cost!.netProfitUsd)[0]?.cost?.netProfitUsd ?? null);
    const summary: BlockSummary = { block: Number(block), time: r.timestamp, poolsScanned: r.poolsScanned, poolsExcluded: r.poolsExcluded.length, routes: r.routes, evaluations: r.evaluations.length, candidates: r.candidates.length, simulated: sims.size, simPassed, executable, bestNetUsd: best, bestSimNetUsd: bestSimNet, failures: r.failures };
    this.db.insertBlockStats({ run_id: this.runId, block: summary.block, block_time: r.timestamp, data_source: this.dataSource, pools_scanned: r.poolsScanned, pools_excluded: r.poolsExcluded.length, routes: r.routes, evaluations: r.evaluations.length, candidates: r.candidates.length, simulated: sims.size, sim_passed: simPassed, executable, best_net_usd: best, flash_premium_bps: r.fl.premiumBps, flash_available: r.fl.availableLiquidity.toString(), base_fee_wei: r.gas.baseFeePerGas.toString(), priority_fee_wei: r.gas.maxPriorityFeePerGas.toString(), eth_usdc: r.gas.ethUsdc, failures_json: JSON.stringify(r.failures) });
    log.debug(`block ${block}: pools=${r.poolsScanned} routes=${r.routes} evals=${r.evaluations.length} cand=${r.candidates.length} sim=${sims.size} pass=${simPassed} exec=${executable} bestNet=${best?.toFixed(4)}`);
    return summary;
  }
}
