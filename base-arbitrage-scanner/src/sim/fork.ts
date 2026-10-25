import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createPublicClient, http, encodeFunctionData, decodeFunctionResult, decodeErrorResult, encodeDeployData, type Address, type Hex, type PublicClient } from 'viem';
import type { AppConfig } from '../config.js';
import type { Route } from '../scanner/routes.js';
import { log } from '../log.js';

export interface SimResult { kind: 'FORK_ATOMIC'; ok: boolean; amountOut?: bigint; premium?: bigint; profit?: bigint; gasUsed?: bigint; failReason?: string; block: bigint; }

const req = async (url: string, method: string, params: unknown[] = []) => {
  const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
  const j = await r.json() as any;
  if (j.error) { const e = new Error(`${method}: ${j.error.message}`) as Error & { data?: unknown }; e.data = j.error.data; throw e; }
  return j.result;
};

export function detectForkProvider(pref: AppConfig['forkProvider']): 'anvil' | 'hardhat' | 'none' {
  if (pref === 'none') return 'none';
  const hasAnvil = spawnSync('anvil', ['--version'], { stdio: 'ignore' }).status === 0;
  if (pref === 'anvil') return hasAnvil ? 'anvil' : 'none';
  if (pref === 'hardhat') return 'hardhat';
  return hasAnvil ? 'anvil' : 'hardhat';
}

/** Manages a local fork node (Anvil preferred, Hardhat node as equivalent fallback). */
export class ForkNode {
  proc?: ChildProcess; url: string; provider: 'anvil' | 'hardhat';
  constructor(private cfg: AppConfig, provider?: 'anvil' | 'hardhat') { this.provider = provider ?? (detectForkProvider(cfg.forkProvider) as any); this.url = `http://127.0.0.1:${cfg.forkPort}`; if (this.provider === 'none' as any) throw new Error('no fork provider available'); }

  async start(opts: { forkUrl?: string; blockNumber?: bigint; chainId?: number } = {}): Promise<void> {
    const port = String(this.cfg.forkPort);
    let cmd: string, args: string[];
    if (this.provider === 'anvil') {
      cmd = 'anvil'; args = ['--port', port, '--silent', '--no-rate-limit'];
      if (opts.forkUrl) args.push('--fork-url', opts.forkUrl); if (opts.blockNumber !== undefined) args.push('--fork-block-number', opts.blockNumber.toString()); if (opts.chainId) args.push('--chain-id', String(opts.chainId));
    } else {
      cmd = 'npx'; args = ['hardhat', 'node', '--port', port];
      if (opts.forkUrl) args.push('--fork', opts.forkUrl); if (opts.blockNumber !== undefined) args.push('--fork-block-number', opts.blockNumber.toString());
    }
    log.info(`fork: starting ${cmd} ${args.map(a => a.includes('://') ? a.replace(/\/\/.*@/, '//***@').replace(/\/v2\/.*/, '/v2/***') : a).join(' ')}`);
    this.proc = spawn(cmd, args, { stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, HARDHAT_DISABLE_TELEMETRY_PROMPT: 'true' } });
    let stderr = ''; this.proc.stderr?.on('data', d => { stderr += d.toString(); }); this.proc.stdout?.on('data', () => {});
    const deadline = Date.now() + this.cfg.forkTimeoutMs;
    while (Date.now() < deadline) {
      if (this.proc.exitCode !== null) throw new Error(`fork node exited: ${stderr.slice(-500)}`);
      try { await req(this.url, 'eth_chainId'); return; } catch { await new Promise(r => setTimeout(r, 400)); }
    }
    this.stop(); throw new Error(`fork node did not become ready in ${this.cfg.forkTimeoutMs}ms: ${stderr.slice(-300)}`);
  }
  /** Re-point the fork at another block without restarting the process. */
  async resetTo(forkUrl: string, blockNumber: bigint) {
    if (this.provider === 'anvil') await req(this.url, 'anvil_reset', [{ forking: { jsonRpcUrl: forkUrl, blockNumber: Number(blockNumber) } }]);
    else await req(this.url, 'hardhat_reset', [{ forking: { jsonRpcUrl: forkUrl, blockNumber: Number(blockNumber) } }]);
  }
  async accounts(): Promise<Address[]> { return req(this.url, 'eth_accounts'); }
  async blockNumber(): Promise<bigint> { return BigInt(await req(this.url, 'eth_blockNumber')); }
  rpc(method: string, params: unknown[] = []) { return req(this.url, method, params); }
  client(): PublicClient { return createPublicClient({ transport: http(this.url, { timeout: 120_000 }) }); }
  stop() { if (this.proc && this.proc.exitCode === null) { this.proc.kill('SIGTERM'); } this.proc = undefined; }
}

export function loadArtifact(name: string): { abi: any; bytecode: Hex } {
  const all = JSON.parse(readFileSync('artifacts/compiled.json', 'utf8'));
  if (!all[name]) throw new Error(`artifact ${name} missing; run npm run compile-contracts`);
  return all[name];
}

/** Deploy a contract from an unlocked node account via eth_sendTransaction (no private key handling in this codebase). */
export async function deployFromUnlocked(node: ForkNode, name: string, args: unknown[] = []): Promise<Address> {
  const art = loadArtifact(name);
  const [from] = await node.accounts();
  const data = encodeDeployData({ abi: art.abi, bytecode: art.bytecode, args });
  const hash = await node.rpc('eth_sendTransaction', [{ from, data, gas: '0x7a1200' }]);
  for (let i = 0; i < 200; i++) { const r = await node.rpc('eth_getTransactionReceipt', [hash]); if (r) { if (r.status !== '0x1') throw new Error(`deploy ${name} reverted`); return r.contractAddress; } await new Promise(r => setTimeout(r, 100)); }
  throw new Error('deploy receipt timeout');
}
export async function sendFromUnlocked(node: ForkNode, to: Address, abi: any, functionName: string, args: unknown[] = [], from?: Address): Promise<void> {
  const f = from ?? (await node.accounts())[0];
  const hash = await node.rpc('eth_sendTransaction', [{ from: f, to, data: encodeFunctionData({ abi, functionName, args }), gas: '0x7a1200' }]);
  for (let i = 0; i < 200; i++) { const r = await node.rpc('eth_getTransactionReceipt', [hash]); if (r) { if (r.status !== '0x1') throw new Error(`${functionName} reverted`); return; } await new Promise(r => setTimeout(r, 100)); }
  throw new Error('tx receipt timeout');
}

/** Atomic path simulator: FlashArbExecutor.simulate() via eth_call against the fork at its current (forked) block. */
export class ForkSimulator {
  executor?: Address;
  constructor(public node: ForkNode, private aavePool: Address) {}
  async deploy() { this.executor = await deployFromUnlocked(this.node, 'FlashArbExecutor', [this.aavePool]); log.info(`fork: FlashArbExecutor deployed at ${this.executor}`); }

  static legsOf(route: Route) { return route.legs.map(l => ({ pool: l.pool.address, kind: l.pool.dex === 'AERODROME_V2' ? 1 : 0, tokenIn: l.tokenIn, tokenOut: l.tokenOut })); }

  async simulate(route: Route, asset: Address, amount: bigint): Promise<SimResult> {
    if (!this.executor) await this.deploy();
    const art = loadArtifact('FlashArbExecutor');
    const block = await this.node.blockNumber();
    const data = encodeFunctionData({ abi: art.abi, functionName: 'simulate', args: [asset, amount, ForkSimulator.legsOf(route)] });
    const [from] = await this.node.accounts();
    try {
      const out: Hex = await this.node.rpc('eth_call', [{ from, to: this.executor, data, gas: '0xf00000' }, 'latest']);
      const [amountOut, premium, profit] = decodeFunctionResult({ abi: art.abi, functionName: 'simulate', data: out }) as [bigint, bigint, bigint];
      let gasUsed: bigint | undefined;
      try { gasUsed = BigInt(await this.node.rpc('eth_estimateGas', [{ from, to: this.executor, data }])); } catch (e: any) { log.debug('estimateGas failed', e?.message); }
      return { kind: 'FORK_ATOMIC', ok: profit >= 0n, amountOut, premium, profit, gasUsed, block, failReason: profit >= 0n ? undefined : 'negative profit' };
    } catch (e: any) {
      const msg: string = e?.message ?? String(e);
      // revert data: JSON-RPC error.data (anvil/hardhat) or a hex blob embedded in the message
      const raw = typeof e?.data === 'string' ? e.data : typeof e?.data?.data === 'string' ? e.data.data : undefined;
      const m = raw && /^0x[0-9a-fA-F]{8,}$/.test(raw) ? [raw] : msg.match(/0x[0-9a-fA-F]{8,}/);
      if (m) { try { const d = decodeErrorResult({ abi: art.abi, data: m[0] as Hex }); if (d.errorName === 'Unprofitable') { const [received, owed] = d.args as [bigint, bigint]; return { kind: 'FORK_ATOMIC', ok: false, amountOut: received, premium: undefined, profit: received - owed, block, failReason: `Unprofitable: received ${received} < owed ${owed}` }; } return { kind: 'FORK_ATOMIC', ok: false, block, failReason: `${d.errorName}(${(d.args ?? []).join(',')})` }; } catch { /* fallthrough */ } }
      return { kind: 'FORK_ATOMIC', ok: false, block, failReason: msg.slice(0, 300) };
    }
  }
}
