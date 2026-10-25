import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import type { ScannerDb } from './db.js';

function esc(v: unknown): string { if (v === null || v === undefined) return ''; const s = typeof v === 'object' ? JSON.stringify(v) : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; }
export function rowsToCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return '';
  const cols = Object.keys(rows[0]);
  return [cols.join(','), ...rows.map(r => cols.map(c => esc(r[c])).join(','))].join('\n') + '\n';
}
export function exportTable(db: ScannerDb, table: string, outPath: string, where = '', ...params: unknown[]): number {
  const rows = db.all(`SELECT * FROM ${table} ${where}`, ...params);
  mkdirSync(dirname(outPath), { recursive: true });
  writeFileSync(outPath, rowsToCsv(rows));
  return rows.length;
}
