# DEX 套利连续监控 — 部署指南（24–72h 常驻）

> 本 Claude 云会话**无法**保持 24–72h 常驻进程；以下为可直接部署到常驻服务器的方案。
> 请勿把会话内的短验证跑当作连续监控。连续时长以监控 DB 的真实记录为准（`dex-acceptance`）。

## 前置
- Python 3.11+，已 `pip install pyyaml`；Foundry 的 `cast` 在 PATH（报价用）。
- 放行出站到各链 RPC 与 DefiLlama（与本项目 config/dex.yaml 一致）。
- 可选：`export ALCHEMY_ETH_URL=...` / `ZEROX_API_KEY=...`（安全环境变量，勿硬编码）。

## 运行（无界，常驻）
```bash
cd dao_audit
PYTHONPATH=tool python3 -m dao_audit dex-run \
  --db /var/lib/dexmon/dex_monitor.db --poll 10 --rate-limit 2
# 新区块触发增量扫描；SQLite 持久化；进程重启后自动从上次区块续跑。
```

## systemd（崩溃自动重启 = 真正连续）
见 `deploy/dexmon.service`。安装：
```bash
sudo cp deploy/dexmon.service /etc/systemd/system/
sudo systemctl enable --now dexmon
```

## 采集 24–72h 后出验收报告
```bash
PYTHONPATH=tool python3 -m dao_audit dex-acceptance --db /var/lib/dexmon/dex_monitor.db
# 输出 reports_out/dex_acceptance.{md,json}：真实连续时长、区块数、扫描次数、
# 正价差/正净收益/Fork验证次数、机会寿命分布、各路径净收益与容量、延迟与失败率。
```

## 安全边界（不可更改）
- 不连接真实钱包、不签名、不执行真实交易；仅只读报价 + 本地 fork 模拟。
- 不降低成本标准制造盈利；无正净收益一律 NO_TRADE。
- 保留 DAO 治理安全审计模块的全部限制。
