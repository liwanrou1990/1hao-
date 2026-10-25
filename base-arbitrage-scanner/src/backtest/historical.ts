import type { MarketSource } from '../dex/types.js';
import type { Runner, BlockSummary } from '../runner.js';
import { BLOCK_TIME_SECONDS } from '../config.js';
import { log } from '../log.js';

export interface BacktestPlan { fromBlock: bigint; toBlock: bigint; sampleEvery: number; blocks: bigint[]; hours: number; rule: string; }

/**
 * Sampling rule (recorded in the report): starting from the latest block, walk back `hours` of blocks (hours*3600/2 on Base)
 * and evaluate every `sampleEvery`-th block. All reads are pinned to the sampled block (eth_call blockNumber / fork-block-number),
 * so only data observable at that block is used. No current state substitutes for historical state; if the RPC cannot serve a
 * historical block (non-archive node), the block is recorded as failed, never skipped silently.
 */
export async function planBacktest(src: MarketSource, hours: number, sampleEvery: number, endBlock?: bigint): Promise<BacktestPlan> {
  const toBlock = endBlock ?? await src.latestBlock();
  const span = BigInt(Math.floor(hours * 3600 / BLOCK_TIME_SECONDS));
  const fromBlock = toBlock - span;
  const blocks: bigint[] = [];
  for (let b = fromBlock; b <= toBlock; b += BigInt(sampleEvery)) blocks.push(b);
  return { fromBlock, toBlock, sampleEvery, blocks, hours, rule: `every ${sampleEvery} blocks (${sampleEvery * BLOCK_TIME_SECONDS}s) over ${hours}h: blocks ${fromBlock}..${toBlock}, ${blocks.length} samples` };
}

export async function runBacktest(runner: Runner, plan: BacktestPlan, onProgress?: (s: BlockSummary, i: number) => void): Promise<{ summaries: BlockSummary[]; failedBlocks: Array<{ block: bigint; reason: string }> }> {
  const summaries: BlockSummary[] = []; const failedBlocks: Array<{ block: bigint; reason: string }> = [];
  for (let i = 0; i < plan.blocks.length; i++) {
    const b = plan.blocks[i];
    try { const s = await runner.runBlock(b); summaries.push(s); onProgress?.(s, i); }
    catch (e: any) { const reason = String(e?.shortMessage ?? e?.message ?? e).slice(0, 200); failedBlocks.push({ block: b, reason }); log.warn(`backtest block ${b} failed: ${reason}`); runner.db.insertRpcFailure(runner.runId, { ts: Date.now(), method: `backtest_block_${b}`, reason, attempt: 0 }); }
  }
  return { summaries, failedBlocks };
}
