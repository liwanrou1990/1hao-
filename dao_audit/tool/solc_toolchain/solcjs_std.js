// Standard-JSON compile through an exact solc-js release (npm "solc" package).
//
// usage: node solcjs_std.js <node_modules/solc dir> <base>[,:<base>...] < input.json
//
// solc >= 0.4.11 understands standard JSON natively. Older releases (<= 0.4.10)
// only have the legacy API; for those this script runs the legacy compile and
// translates its output into the standard-JSON shape (sources[f].ast holds the
// legacy AST, contracts[f][Name] holds abi/bytecode/sourceMap), so the same
// downstream tools (crytic-compile / Slither) can consume it. Nothing about the
// compiler's semantics is emulated: the real historical compiler does the work.
const path = require('path'), fs = require('fs');
const solc = require(process.argv[2]);
const bases = (process.argv[3] || '.').split(/[,:]/).filter(Boolean);

function findImports(p) {
  for (const b of bases) {
    const f = path.resolve(b, p);
    if (fs.existsSync(f)) return { contents: fs.readFileSync(f, 'utf8') };
  }
  return { error: 'File not found: ' + p };
}

function legacy(inputStr) {
  const input = JSON.parse(inputStr);
  const sources = {};
  for (const [name, s] of Object.entries(input.sources || {})) {
    if (typeof s.content === 'string') sources[name] = s.content;
    else {
      const r = findImports((s.urls && s.urls[0]) || name);
      if (r.error) return JSON.stringify({ errors: [{ severity: 'error', type: 'IOError',
        component: 'general', message: r.error, formattedMessage: r.error }] });
      sources[name] = r.contents;
    }
  }
  const opt = input.settings && input.settings.optimizer && input.settings.optimizer.enabled ? 1 : 0;
  const out = solc.compile({ sources }, opt, findImports);
  const res = { errors: [], sources: {}, contracts: {} };
  for (const e of out.errors || []) {
    const sev = /Warning:/.test(e) ? 'warning' : 'error';
    res.errors.push({ severity: sev, type: sev === 'warning' ? 'Warning' : 'Error',
      component: 'general', message: e, formattedMessage: e });
  }
  let id = 0;
  for (const [f, s] of Object.entries(out.sources || {})) {
    res.sources[f] = { id: id++, legacyAST: s.AST, ast: s.AST };
  }
  for (const [key, c] of Object.entries(out.contracts || {})) {
    const i = key.lastIndexOf(':');
    const file = i >= 0 ? key.slice(0, i) : Object.keys(sources)[0];
    const name = i >= 0 ? key.slice(i + 1) : key;
    (res.contracts[file] = res.contracts[file] || {})[name] = {
      abi: c.interface ? JSON.parse(c.interface) : [],
      userdoc: {}, devdoc: {},
      evm: {
        bytecode: { object: c.bytecode || '', sourceMap: c.srcmap || '' },
        deployedBytecode: { object: c.runtimeBytecode || '', sourceMap: c.srcmapRuntime || '' },
      },
    };
  }
  return JSON.stringify(res);
}

let input = '';
process.stdin.on('data', d => input += d).on('end', () => {
  let out;
  if (typeof solc.compileStandardWrapper === 'function') out = solc.compileStandardWrapper(input, findImports);
  else if (typeof solc.compileStandard === 'function') out = solc.compileStandard(input, findImports);
  else {
    // solc-js >= 0.5 exposes standard JSON as compile(string, {import})
    let parsed = null;
    try { parsed = JSON.parse(solc.compile(input, { import: findImports })); } catch (e) { parsed = null; }
    out = parsed && (parsed.sources || parsed.contracts) ? JSON.stringify(parsed) : legacy(input);
  }
  process.stdout.write(out);
});
