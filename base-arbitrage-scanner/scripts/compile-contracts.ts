/** Compiles contracts/*.sol with solc-js (offline) into artifacts/compiled.json: { ContractName: { abi, bytecode } } */
import solc from 'solc';
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

const dir = join(process.cwd(), 'contracts');
const sources: Record<string, { content: string }> = {};
for (const f of readdirSync(dir).filter(f => f.endsWith('.sol'))) sources[f] = { content: readFileSync(join(dir, f), 'utf8') };
const input = { language: 'Solidity', sources, settings: { optimizer: { enabled: true, runs: 200 }, evmVersion: 'paris', outputSelection: { '*': { '*': ['abi', 'evm.bytecode.object'] } } } };
const out = JSON.parse(solc.compile(JSON.stringify(input)));
const errors = (out.errors ?? []).filter((e: any) => e.severity === 'error');
if (errors.length) { console.error(errors.map((e: any) => e.formattedMessage).join('\n')); process.exit(1); }
for (const w of (out.errors ?? [])) console.warn(w.formattedMessage);
const artifacts: Record<string, { abi: unknown; bytecode: string }> = {};
for (const file of Object.keys(out.contracts)) for (const name of Object.keys(out.contracts[file])) artifacts[name] = { abi: out.contracts[file][name].abi, bytecode: '0x' + out.contracts[file][name].evm.bytecode.object };
mkdirSync('artifacts', { recursive: true });
writeFileSync('artifacts/compiled.json', JSON.stringify(artifacts, null, 1));
console.log('compiled:', Object.keys(artifacts).map(k => `${k}(${(artifacts[k].bytecode.length - 2) / 2}B)`).join(', '));
