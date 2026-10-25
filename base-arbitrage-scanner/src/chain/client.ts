import { createPublicClient, fallback, http, webSocket, type PublicClient, type Transport, type Chain } from 'viem';
import { base } from 'viem/chains';
import type { AppConfig } from '../config.js';
import { log } from '../log.js';

export interface RpcFailure { ts: number; method: string; reason: string; attempt: number; }
export type FailureSink = (f: RpcFailure) => void;

/** Token-bucket rate limiter + concurrency gate. */
export class RateLimiter {
  private tokens: number;
  private last = Date.now();
  private active = 0;
  private queue: Array<() => void> = [];
  constructor(private rps: number, private maxConcurrency: number) { this.tokens = rps; }
  private refill() {
    const now = Date.now();
    this.tokens = Math.min(this.rps, this.tokens + ((now - this.last) / 1000) * this.rps);
    this.last = now;
  }
  async acquire(): Promise<void> {
    for (;;) {
      this.refill();
      if (this.tokens >= 1 && this.active < this.maxConcurrency) { this.tokens -= 1; this.active += 1; return; }
      await new Promise<void>(r => { this.queue.push(r); setTimeout(() => { const i = this.queue.indexOf(r); if (i >= 0) this.queue.splice(i, 1); r(); }, 25); });
    }
  }
  release() { this.active -= 1; const n = this.queue.shift(); if (n) n(); }
}

export interface ChainClient {
  client: PublicClient<Transport, Chain>;
  limiter: RateLimiter;
  /** Run an RPC-bound operation with rate limiting, retry/backoff and failure recording. */
  call<T>(method: string, fn: () => Promise<T>): Promise<T>;
  failures: RpcFailure[];
  onFailure: (sink: FailureSink) => void;
}

const RETRYABLE = /(429|rate limit|timeout|ETIMEDOUT|ECONNRESET|ECONNREFUSED|socket hang up|fetch failed|503|502|504|network|too many requests|limit exceeded|disconnected|WebSocket)/i;

export function createChainClient(cfg: AppConfig, urls = cfg.rpcUrls): ChainClient {
  if (!urls.length) throw new Error('No RPC URL configured. Set BASE_RPC_URL or BASE_RPC_URLS in .env');
  const transports = urls.map(u => u.startsWith('ws') ? webSocket(u, { reconnect: true, retryCount: 3 }) : http(u, { timeout: 20_000, retryCount: 0, batch: false }));
  const transport = transports.length === 1 ? transports[0] : fallback(transports, { rank: false });
  const client = createPublicClient({ chain: base, transport }) as PublicClient<Transport, Chain>;
  const limiter = new RateLimiter(cfg.rpcMaxRps, cfg.rpcMaxConcurrency);
  const failures: RpcFailure[] = [];
  const sinks: FailureSink[] = [];
  const record = (f: RpcFailure) => { failures.push(f); if (failures.length > 5000) failures.splice(0, 1000); for (const s of sinks) s(f); };

  async function call<T>(method: string, fn: () => Promise<T>): Promise<T> {
    let lastErr: unknown;
    for (let attempt = 0; attempt <= cfg.rpcRetries; attempt++) {
      await limiter.acquire();
      try { return await fn(); }
      catch (e: any) {
        lastErr = e;
        const msg = String(e?.shortMessage ?? e?.message ?? e);
        record({ ts: Date.now(), method, reason: msg.slice(0, 300), attempt });
        if (!RETRYABLE.test(msg) || attempt === cfg.rpcRetries) break;
        const delay = Math.min(8000, 300 * 2 ** attempt) + Math.random() * 100;
        log.warn(`rpc ${method} failed (attempt ${attempt + 1}/${cfg.rpcRetries + 1}): ${msg.slice(0, 120)} -> retry in ${Math.round(delay)}ms`);
        await new Promise(r => setTimeout(r, delay));
      } finally { limiter.release(); }
    }
    throw lastErr;
  }
  return { client, limiter, call, failures, onFailure: s => sinks.push(s) };
}

/** Subscribe to new blocks: websocket if configured, otherwise polling with reconnect/backoff. */
export function watchBlocks(cc: ChainClient, cfg: AppConfig, onBlock: (n: bigint) => void, pollMs = 1000): () => void {
  let stopped = false;
  let last = -1n;
  let backoff = 1000;
  const start = async () => {
    while (!stopped) {
      try {
        const n = await cc.call('eth_blockNumber', () => cc.client.getBlockNumber());
        if (n > last) { last = n; onBlock(n); }
        backoff = 1000;
        await new Promise(r => setTimeout(r, pollMs));
      } catch (e) {
        log.warn(`block poll error, reconnecting in ${backoff}ms:`, e);
        await new Promise(r => setTimeout(r, backoff));
        backoff = Math.min(backoff * 2, 30_000);
      }
    }
  };
  if (cfg.wsUrl) {
    try {
      const ws = createPublicClient({ chain: base, transport: webSocket(cfg.wsUrl, { reconnect: true }) });
      const unwatch = ws.watchBlockNumber({ onBlockNumber: n => { if (n > last) { last = n; onBlock(n); } }, onError: e => log.warn('ws newHeads error (polling continues):', e) });
      void start();
      return () => { stopped = true; unwatch(); };
    } catch (e) { log.warn('ws subscribe failed; falling back to polling', e); }
  }
  void start();
  return () => { stopped = true; };
}
