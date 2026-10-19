export async function get<T = any>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
export async function post<T = any>(url: string, body?: any): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined })
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json()
}
export const pct = (x: number | null | undefined, nd = 2) => (x == null || Number.isNaN(x) ? '—' : `${(x * 100).toFixed(nd)}%`)
export const num = (x: number | null | undefined, nd = 2) => (x == null || Number.isNaN(x) ? '—' : x.toLocaleString('zh-CN', { maximumFractionDigits: nd, minimumFractionDigits: nd }))
export const cls = (x: number | null | undefined) => (x == null ? '' : x >= 0 ? 'pos' : 'neg')
