# 真实行情连通性诊断（第八阶段）

探测时间：2026-11-07T16:56:17Z（UTC）。所有出站 HTTPS 经由云环境代理；"Tunnel connection failed: 403" 表示**环境网络白名单**拒绝（不是数据源拒绝）；HTTP 451 / CloudFront 国家封锁表示**数据源按地区限制**（服务商规则，不绕过）。

## 1. 初始环境（个人 Default 环境，白名单只含 DeFi/CoinGecko 主机）

| 数据源 | 结果 | 说明 |
|---|---|---|
| binance_spot_api | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| binance_spot_api1 | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| binance_data_vision | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| binance_us | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| okx | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| bybit | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| coinbase_exchange | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| coinbase_api | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| kraken | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| bitstamp | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| gateio | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| kucoin | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| mexc | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| htx | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| bitget | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| gemini | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| cryptocompare | 不可达 | URLError: <urlopen error Tunnel connection failed: 403 Forbidden> |
| coingecko | 可达 | HTTP 200 |
| defillama_coins | 可达 | HTTP 200 |
| static_klines_cache | 可达 | HTTP 200 |

结论：20 个数据源中只有 CoinGecko（非交易所、无 1h K 线）、DefiLlama（聚合价格）与 GitHub 上的 Binance 现货 K 线镜像（数小时延迟）可达；交易所官方接口全部被环境白名单拦截。

## 2. 处理：为本仓库创建专用云环境并加入交易所公开行情主机（经用户批准）

- 新环境 `1hao- (Binance spot market data)`，白名单：api.binance.com、data.binance.vision、raw.githubusercontent.com、api.coingecko.com、coins.llama.fi，随后追加 www.okx.com、api.bybit.com、api.exchange.coinbase.com。
- 白名单放开后的实测：

| 数据源 | 结果 | 说明 |
|---|---|---|
| api.binance.com | HTTP 451 | 服务商按地区限制（'Service unavailable from a restricted location'）；**不绕过**。在非受限地区的机器上为首选来源 |
| data.binance.vision | HTTP 200 | 只有历史日文件，无实时 K 线 |
| www.okx.com /api/v5/market/history-candles | 可达，返回含 confirm 标志的 K 线 | **本环境实际使用的实时来源**（OKX 现货 BTC-USDT / ETH-USDT） |
| api.bybit.com | CloudFront 国家封锁 | 服务商限制；不绕过 |
| api.exchange.coinbase.com | 可达 | 第二实时来源（BTC-USDT / ETH-USDT，无 4h 粒度） |
| raw.githubusercontent.com（Binance 镜像） | 可达，1h 延迟约 7 小时 | 最后回退与历史预热来源 |
| api.coingecko.com | 可达 | 仅作非交易所参考价对照（> 3% 偏离告警），不用于成交 |

## 3. 来源优先级与回退（代码 `src/brando_pa/forward/datafeed.py`）

Binance 现货 → OKX 现货 → Bybit 现货 → Coinbase Exchange → Binance 镜像。每个来源最多重试 3 次（指数退避 1/2/4 s），全部失败则记录 `source_failed` 并切换下一来源；全部来源失败时 `ok=false`，保留上一次成功时间与来源，**不伪造任何价格**。

## 4. 首次真实更新（2026-11-07 17:35 UTC）

来源 okx_spot；BTC/ETH 1h 最新已收盘 17:00 UTC（落后 0.6 h）、1d 最新 2026-11-07 00:00；1h→1d 聚合一致性最大相对误差 0（10 天）；与 Binance 历史重叠 4 根 K 线的收盘价最大相对差 1.2 bp（1h）/ 0.6 bp（1d）；CoinGecko 参考价偏离 < 0.02%。

## 5. 在具备合法访问条件的联网机器上部署

```bash
git clone <repo> && cd 1hao- && pip install -r requirements.txt   # 或现有环境
export PYTHONPATH=src
python -m brando_pa.cli forward-data            # 首次：从历史仓预热 forward_bars 并拉取实时 K 线（来源自动选择）
python -m brando_pa.cli forward-run             # 启动三个前瞻账户（首次运行只记录 warmup，首个信号在下一个 UTC 日线收盘后）
# 常驻：
python -m brando_pa.cli forward-run --loop --interval 600
# 或 cron（每小时第 7 分）：
7 * * * * cd /path/1hao- && bash scripts/brando_pa_forward.sh >> reports_out/brando_pa/phase8_forward/cron.log 2>&1
```

网络要求：允许出站到上表任一交易所公开主机（无密钥、只读 GET）；在 Binance 可用地区将自动以 Binance 为首选，否则 OKX / Coinbase。时间：全部 UTC；机器时钟需 NTP 同步（未收盘 K 线判定依赖本机时间）。