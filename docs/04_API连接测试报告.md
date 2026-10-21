# 04 · Bitget Wallet 官方 RWA API 连接测试报告

测试时间：2026-10-21（UTC）· 命令：`bgwq live-test --symbols NVDA SPCX TSLA` · 原始 JSON：`docs/api_live_test_2026-10-20.json`

## 结论：连接测试未通过（两项阻塞，均不在代码层）

| 项目 | 结果 | 说明 |
|---|---|---|
| 网络可达性 `https://bopenapi.bgwapi.io` | **失败** `ProxyError: 403 Forbidden` | 本开发容器的出网策略拒绝该主机（CONNECT 403）。文档站 `web3.bitget.com` 同样被拒 |
| API Key | 已配置（本地 `.env`，git 忽略） | 由用户提供；建议在 Bitget Wallet 后台轮换，因其曾在聊天中明文出现 |
| API Secret | **缺失** | 官方签名 `x-api-signature = base64(HMAC-SHA256(secret, payload))`，无 Secret 无法发起任何已签名请求 |
| stockList / stockInfo 核对 | 未执行 | 依赖上两项 |
| 1m / 5m / 15m K 线分页获取 | 未执行 | 依赖上两项 |
| 完整回测（真实数据） | **未运行** | 按要求“连接测试成功后再运行完整回测”，未用模拟数据冒充 |

## 已实现并通过单元测试（MockTransport，不访问网络）的接入代码

- 签名：请求头 `x-api-key / x-api-timestamp(毫秒) / x-api-signature`，payload 为键排序 JSON `{apiPath, body, …query, x-api-key, x-api-timestamp}`；与官方 TS SDK 算法一致（Node 参考向量测试）。密钥只从本地环境变量/`.env` 读取。
- `stockList` → 解析 ticker / name / status / contracts[chain, contract, symbol, data_source(ondo|xstocks), status]；`verify_assets()` 对 NVDA、SPCX、TSLA 输出代币、链、合约、发行商与 `stockInfo` 的市场状态、单笔买卖上限。
- `kline`：`kline_history()` 以 `end_time`（秒）向前分页，自动去重、按时间升序、截取到目标日；支持 1m/5m/15m/30m/1h；`fetch_bars(symbol, day, period)` 返回标准列并带 `tf`。
- 存储：DuckDB `bars` 表新增 `tf` 列（旧库自动升级），同一标的的 1m/5m/15m 分开保存；保存开高低收、成交量、成交额、笔数、UTC 与美东时间戳、来源、链、合约、质量标记。ondo 报价 K 线无成交量时标记 `NO_VOLUME_FIELD`。
- 模型接入：`bgwq ingest --periods 1m 5m 15m` 在 live 模式下用官方 K 线替换模拟数据；`compare / simple / target / optimal` 等全部模型读取代币 venue 的真实 K 线；Massive 原生基准改为可选。
- 安全：只实现只读行情端点；不含任何下单、转账、签名交易或 swap 接口调用；`research_only` 守卫不变。

## 在你的本地环境完成测试的步骤

```bash
cp .env.example .env     # 写入 BGW_API_KEY 与 BGW_API_SECRET
bgwq live-test --symbols NVDA SPCX TSLA        # 期望：network ok、assets 列出代币/链/合约、klines 各周期 ok；缺失会写在 missing_note / errors
bgwq ingest --symbols NVDA SPCX TSLA --days 30 --periods 1m 5m 15m
bgwq compare-report --symbols NVDA SPCX --wf-symbols NVDA SPCX
```
`live-test` 退出码：0 通过，3 未通过（并在 JSON 中列出每一步的错误、每个周期的 K 线根数与常规时段应有根数的差额）。
