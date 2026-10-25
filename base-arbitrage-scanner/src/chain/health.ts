import { lookup } from 'node:dns/promises';
import type { AppConfig } from '../config.js';

export type RpcFailureClass =
  | 'OK'
  | 'NO_URL_CONFIGURED'
  | 'DNS_UNRESOLVED'            // host does not resolve from this machine (no direct egress or bad hostname)
  | 'EGRESS_POLICY_DENIED'      // forward proxy / cloud network policy refused the CONNECT tunnel (403/407 from proxy)
  | 'PROVIDER_AUTH_DENIED'      // TLS tunnel established, provider answered 401/403 (bad/missing API key)
  | 'PROVIDER_RATE_LIMITED'     // 429
  | 'WRONG_CHAIN'               // eth_chainId != 8453
  | 'RPC_ERROR'                 // JSON-RPC level error (method unsupported, etc.)
  | 'UNREACHABLE';              // timeout / connection refused / TLS failure

export interface RpcHealth {
  url: string; masked: string; cls: RpcFailureClass; ok: boolean; detail: string;
  chainId?: number; blockNumber?: bigint; latencyMs?: number; archive?: boolean | 'unknown'; proxied: boolean; dns: 'resolved' | 'unresolved' | 'skipped';
}

export function maskUrl(u: string): string { return u.replace(/\/\/[^@/]*@/, '//***@').replace(/\/(v2|v3)\/[^/?]+/, '/$1/***').replace(/([?&](apikey|key|token)=)[^&]+/gi, '$1***'); }

function proxyFor(url: string): string | undefined {
  const host = new URL(url).hostname;
  const noProxy = (process.env.NO_PROXY ?? process.env.no_proxy ?? '').split(',').map(s => s.trim()).filter(Boolean);
  if (noProxy.some(n => n === host || (n.startsWith('.') && host.endsWith(n)) || (n.startsWith('*') && host.endsWith(n.slice(1))))) return undefined;
  return process.env.HTTPS_PROXY ?? process.env.https_proxy ?? process.env.HTTP_PROXY ?? process.env.http_proxy;
}

/** Raw CONNECT probe through the configured proxy to distinguish policy denial (proxy 403) from provider denial (origin 403). */
async function probeProxyConnect(proxy: string, host: string, port: number, timeoutMs: number): Promise<{ status: number | 'error'; detail: string }> {
  const net = await import('node:net');
  const p = new URL(proxy);
  return new Promise(resolve => {
    const sock = net.connect({ host: p.hostname, port: Number(p.port || 80) });
    const t = setTimeout(() => { sock.destroy(); resolve({ status: 'error', detail: 'proxy connect timeout' }); }, timeoutMs);
    let buf = '';
    sock.on('connect', () => sock.write(`CONNECT ${host}:${port} HTTP/1.1\r\nHost: ${host}:${port}\r\nProxy-Connection: Keep-Alive\r\n\r\n`));
    sock.on('data', d => { buf += d.toString(); if (buf.includes('\r\n\r\n')) { clearTimeout(t); const m = buf.match(/^HTTP\/1\.[01] (\d{3})/); sock.destroy(); resolve({ status: m ? Number(m[1]) : 'error', detail: buf.split('\r\n')[0] }); } });
    sock.on('error', e => { clearTimeout(t); resolve({ status: 'error', detail: e.message }); });
  });
}

async function rpc(url: string, method: string, params: unknown[], timeoutMs: number): Promise<{ status: number; json?: any; text?: string }> {
  const ctl = new AbortController(); const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }), signal: ctl.signal });
    const text = await r.text(); let json: any; try { json = JSON.parse(text); } catch { /* non-json */ }
    return { status: r.status, json, text };
  } finally { clearTimeout(t); }
}

/** Classify one RPC endpoint. Never bypasses the proxy or the network policy; it only reports what blocks the call. */
export async function checkRpc(url: string, expectedChainId = 8453, timeoutMs = 8000): Promise<RpcHealth> {
  const masked = maskUrl(url);
  let host: string, port: number;
  try { const u = new URL(url); host = u.hostname; port = Number(u.port || (u.protocol === 'https:' || u.protocol === 'wss:' ? 443 : 80)); } catch { return { url, masked, cls: 'RPC_ERROR', ok: false, detail: 'invalid URL', proxied: false, dns: 'skipped' }; }
  const proxy = proxyFor(url); const proxied = !!proxy;
  let dns: RpcHealth['dns'] = 'skipped';
  if (!proxied) { try { await lookup(host); dns = 'resolved'; } catch { return { url, masked, cls: 'DNS_UNRESOLVED', ok: false, detail: `cannot resolve ${host} (no direct egress or bad hostname)`, proxied, dns: 'unresolved' }; } }
  if (url.startsWith('ws')) return { url, masked, cls: 'RPC_ERROR', ok: false, detail: 'websocket endpoints are probed via the http sibling; configure an https URL for health checks', proxied, dns };
  const t0 = Date.now();
  try {
    const r = await rpc(url, 'eth_chainId', [], timeoutMs);
    const latencyMs = Date.now() - t0;
    if (r.status === 401 || r.status === 403) return { url, masked, cls: 'PROVIDER_AUTH_DENIED', ok: false, detail: `provider HTTP ${r.status}: ${(r.text ?? '').slice(0, 120)}`, proxied, dns, latencyMs };
    if (r.status === 429) return { url, masked, cls: 'PROVIDER_RATE_LIMITED', ok: false, detail: 'HTTP 429', proxied, dns, latencyMs };
    if (!r.json || r.json.error) return { url, masked, cls: 'RPC_ERROR', ok: false, detail: `HTTP ${r.status} ${r.json?.error?.message ?? (r.text ?? '').slice(0, 120)}`, proxied, dns, latencyMs };
    const chainId = Number(BigInt(r.json.result));
    if (chainId !== expectedChainId) return { url, masked, cls: 'WRONG_CHAIN', ok: false, detail: `chainId ${chainId} != ${expectedChainId}`, proxied, dns, latencyMs, chainId };
    const bn = await rpc(url, 'eth_blockNumber', [], timeoutMs);
    const blockNumber = bn.json?.result ? BigInt(bn.json.result) : undefined;
    // archive probe: balance of the zero address at a block ~7 days old (302_400 blocks); non-archive nodes error with "missing trie node"/"header not found"
    let archive: RpcHealth['archive'] = 'unknown';
    if (blockNumber && blockNumber > 400_000n) { const a = await rpc(url, 'eth_getBalance', ['0x4200000000000000000000000000000000000006', '0x' + (blockNumber - 302_400n).toString(16)], timeoutMs); archive = a.json?.result ? true : a.json?.error ? false : 'unknown'; }
    return { url, masked, cls: 'OK', ok: true, detail: `chainId ${chainId}, block ${blockNumber}, ${latencyMs}ms, archive(7d)=${archive}`, proxied, dns, latencyMs, chainId, blockNumber, archive };
  } catch (e: any) {
    const msg = String(e?.cause?.message ?? e?.message ?? e);
    if (proxied && proxy) {
      const p = await probeProxyConnect(proxy, host, port, timeoutMs);
      if (p.status === 403 || p.status === 407) return { url, masked, cls: 'EGRESS_POLICY_DENIED', ok: false, detail: `proxy refused CONNECT ${host}:${port} → ${p.detail} (cloud environment network policy; add the host to Allowed domains)`, proxied, dns };
      if (p.status === 200) return { url, masked, cls: 'UNREACHABLE', ok: false, detail: `tunnel established but request failed: ${msg.slice(0, 120)}`, proxied, dns };
      return { url, masked, cls: 'UNREACHABLE', ok: false, detail: `proxy CONNECT ${p.detail}; ${msg.slice(0, 100)}`, proxied, dns };
    }
    if (/ENOTFOUND|EAI_AGAIN/.test(msg)) return { url, masked, cls: 'DNS_UNRESOLVED', ok: false, detail: msg.slice(0, 160), proxied, dns };
    return { url, masked, cls: 'UNREACHABLE', ok: false, detail: msg.slice(0, 160), proxied, dns };
  }
}

export async function checkAllRpcs(cfg: AppConfig): Promise<RpcHealth[]> {
  if (!cfg.rpcUrls.length) return [{ url: '', masked: '(none)', cls: 'NO_URL_CONFIGURED', ok: false, detail: 'set BASE_RPC_URL or BASE_RPC_URLS in .env', proxied: false, dns: 'skipped' }];
  return Promise.all(cfg.rpcUrls.map(u => checkRpc(u, cfg.chainId)));
}

/** Healthy URLs ranked by (archive, latency); used to order the viem fallback transport. */
export function rankHealthy(h: RpcHealth[]): string[] {
  return h.filter(x => x.ok).sort((a, b) => Number(b.archive === true) - Number(a.archive === true) || (a.latencyMs ?? 1e9) - (b.latencyMs ?? 1e9)).map(x => x.url);
}

export function renderDiagnosis(h: RpcHealth[]): string {
  const L = ['| endpoint | class | via proxy | detail |', '|---|---|---|---|'];
  for (const x of h) L.push(`| ${x.masked} | **${x.cls}** | ${x.proxied ? 'yes' : 'no'} | ${x.detail.replace(/\|/g, '/')} |`);
  const classes = new Set(h.map(x => x.cls));
  L.push('');
  if (classes.has('EGRESS_POLICY_DENIED')) L.push('- EGRESS_POLICY_DENIED: the cloud environment network policy refused the tunnel. Fix in the environment settings (Network access → Allowed domains), not in code. Nothing reached the RPC provider.');
  if (classes.has('DNS_UNRESOLVED')) L.push('- DNS_UNRESOLVED: the host does not resolve from this machine (no direct egress / typo).');
  if (classes.has('PROVIDER_AUTH_DENIED')) L.push('- PROVIDER_AUTH_DENIED: the provider rejected the key (401/403 from origin). Check the API key in BASE_RPC_URL.');
  if (classes.has('WRONG_CHAIN')) L.push('- WRONG_CHAIN: endpoint is not Base mainnet (8453).');
  if (classes.has('NO_URL_CONFIGURED')) L.push('- NO_URL_CONFIGURED: no RPC in .env.');
  return L.join('\n');
}
