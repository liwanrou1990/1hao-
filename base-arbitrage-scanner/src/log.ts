type Level = 'debug' | 'info' | 'warn' | 'error';
const order: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };
let current: Level = (process.env.LOG_LEVEL as Level) ?? 'info';
export function setLogLevel(l: Level) { current = l; }
function fmt(v: unknown): string {
  if (v instanceof Error) return `${v.name}: ${v.message}`;
  if (typeof v === 'bigint') return v.toString();
  if (typeof v === 'object' && v !== null) return JSON.stringify(v, (_k, x) => typeof x === 'bigint' ? x.toString() : x);
  return String(v);
}
function emit(level: Level, args: unknown[]) {
  if (order[level] < order[current]) return;
  const line = `${new Date().toISOString()} [${level.toUpperCase()}] ${args.map(fmt).join(' ')}`;
  (level === 'error' || level === 'warn' ? process.stderr : process.stdout).write(line + '\n');
}
export const log = {
  debug: (...a: unknown[]) => emit('debug', a),
  info: (...a: unknown[]) => emit('info', a),
  warn: (...a: unknown[]) => emit('warn', a),
  error: (...a: unknown[]) => emit('error', a),
};
