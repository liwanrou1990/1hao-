# 离线真实数据目录
目录约定：`data_in/<SYMBOL>/<YYYY-MM-DD>/kline.csv | trades.csv | depth.csv`（也支持 .json / .zip）。
来源：有网络的机器上运行 `python scripts/fetch_bitget_public.py --symbols BTCUSDT ... --day 2026-10-22 --out data_in`，
或 Tardis.dev 等第三方导出（trades / book_snapshot 格式自动识别），或任意含 time/open/high/low/close 列的 OHLCV CSV。
`bgwq playbook-daily` 会先自动导入本目录，再尝试在线接口；数据文件本身不入库（.gitignore）。
