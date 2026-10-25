import { describe, it, expect } from 'vitest';
import { checkRpc, maskUrl, rankHealthy, renderDiagnosis, type RpcHealth } from '../src/chain/health.js';
import { loadConfig } from '../src/config.js';
import { OfflineFixtureSource, FixtureSimulator } from '../src/offline/fixture.js';
import { ScanEngine } from '../src/scanner/scan.js';
import { ScannerDb } from '../src/storage/db.js';
import { Runner } from '../src/runner.js';
import { buildReport } from '../src/report.js';

describe('RPC health classification', () => {
  it('masks credentials in URLs', () => {
    expect(maskUrl('https://user:pw@host/x')).toBe('https://***@host/x');
    expect(maskUrl('https://base-mainnet.g.alchemy.com/v2/SECRETKEY')).toBe('https://base-mainnet.g.alchemy.com/v2/***');
    expect(maskUrl('https://rpc.example/?apikey=abc&x=1')).toBe('https://rpc.example/?apikey=***&x=1');
  });
  it('classifies a refused local endpoint as UNREACHABLE without touching the network policy', async () => {
    const h = await checkRpc('http://127.0.0.1:1/', 8453, 2000);
    expect(h.ok).toBe(false); expect(h.cls).toBe('UNREACHABLE'); expect(h.proxied).toBe(false);
  });
  it('classifies a wrong-chain endpoint (local hardhat-less stub) via a tiny JSON-RPC server', async () => {
    const http = await import('node:http');
    const srv = http.createServer((_q, res) => { let b = ''; _q.on('data', d => b += d); _q.on('end', () => { const j = JSON.parse(b); res.setHeader('content-type', 'application/json'); res.end(JSON.stringify({ jsonrpc: '2.0', id: j.id, result: j.method === 'eth_chainId' ? '0x1' : '0x10' })); }); });
    await new Promise<void>(r => srv.listen(0, '127.0.0.1', () => r()));
    const port = (srv.address() as any).port;
    const h = await checkRpc(`http://127.0.0.1:${port}/`, 8453, 3000);
    srv.close();
    expect(h.cls).toBe('WRONG_CHAIN'); expect(h.chainId).toBe(1);
  });
  it('classifies provider auth denial (HTTP 403 from origin, not from proxy)', async () => {
    const http = await import('node:http');
    const srv = http.createServer((_q, res) => { res.statusCode = 403; res.end('forbidden: invalid api key'); });
    await new Promise<void>(r => srv.listen(0, '127.0.0.1', () => r()));
    const h = await checkRpc(`http://127.0.0.1:${(srv.address() as any).port}/`, 8453, 3000);
    srv.close();
    expect(h.cls).toBe('PROVIDER_AUTH_DENIED');
  });
  it('ranks archive + low latency first and renders a diagnosis', () => {
    const hs: RpcHealth[] = [
      { url: 'a', masked: 'a', cls: 'OK', ok: true, detail: '', proxied: false, dns: 'resolved', latencyMs: 50, archive: false },
      { url: 'b', masked: 'b', cls: 'OK', ok: true, detail: '', proxied: false, dns: 'resolved', latencyMs: 300, archive: true },
      { url: 'c', masked: 'c', cls: 'EGRESS_POLICY_DENIED', ok: false, detail: 'proxy 403', proxied: true, dns: 'skipped' },
    ];
    expect(rankHealthy(hs)).toEqual(['b', 'a']);
    expect(renderDiagnosis(hs)).toContain('EGRESS_POLICY_DENIED');
  });
});

describe('four states, opportunity duration, invalidation', () => {
  it('labels rows, merges consecutive samples into opportunities, re-checks at the next block, never produces VERIFIED_FORK/LIVE_EXECUTED on fixture data', async () => {
    const cfg = loadConfig(); const src = new OfflineFixtureSource(11, 'volatile');
    const engine = new ScanEngine(src, cfg); await engine.init();
    const db = new ScannerDb(':memory:'); db.startRun('s', 'backtest', 'OFFLINE_FIXTURE');
    const fs = new FixtureSimulator(src);
    const runner = new Runner(engine, src, db, cfg, 's', 'backtest', 'OFFLINE_FIXTURE', async (route, _a, amount, block) => fs.simulate(route, amount, block, 5));
    const b0 = src.genesisBlock + 5000n; let inval = 0;
    for (let i = 0n; i < 60n; i++) { const s = await runner.runBlock(b0 + i * 30n); inval += s.invalidated; expect(s.states.VERIFIED_FORK).toBe(0); expect(s.states.LIVE_EXECUTED).toBe(0); }
    const states = db.all("SELECT opportunity_state s, COUNT(*) n FROM candidates GROUP BY s").map((r: any) => r.s);
    expect(states).toContain('QUOTED_ESTIMATE'); expect(states).not.toContain('VERIFIED_FORK'); expect(states).not.toContain('LIVE_EXECUTED');
    const opps = db.all('SELECT * FROM opportunities'); expect(opps.length).toBeGreaterThan(0);
    for (const o of opps) { expect(o.last_block).toBeGreaterThanOrEqual(o.first_block); expect(o.samples_seen).toBeGreaterThanOrEqual(1); expect(['QUOTED_ESTIMATE']).toContain(o.best_state); }
    const invals = db.all('SELECT * FROM invalidations'); expect(invals.length).toBeGreaterThan(0);
    expect(inval).toBe(invals.filter((x: any) => x.reason !== 'still_positive').length);
    const rep = buildReport(db, 's', { title: 't' });
    expect(rep).toContain('Four opportunity states'); expect(rep).toContain('LIVE_EXECUTED | real mainnet transaction confirmed | **0'); expect(rep).toContain('Real sampled blocks: **0**'); expect(rep).toContain('Opportunity duration and invalidation');
    db.close();
  });
});
