# brando_pa 前瞻服务部署（第九阶段）

必须在**持续运行的主机**上启动（家用 Mac 不休眠 / Linux 服务器 / VPS）。Claude 云容器会在空闲后回收，**不能**作为 24 小时运行环境。真实交易始终禁用（无实现）。

网络：主机需能访问任一交易所公开现货行情（优先级 Binance → OKX → Bybit → Coinbase → GitHub 镜像；只读 GET，无密钥）。切换来源会写日志并做重叠偏差检查。

## 安装（通用）
```bash
git clone <repo> 1hao- && cd 1hao-
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pyarrow duckdb httpx pyyaml streamlit plotly
cp deploy/.env.example .env
export PYTHONPATH=src
python -m brando_pa.cli forward-data          # 首次：预热 forward_bars + 拉取实时 K 线，确认来源与新鲜度
python -m brando_pa.cli forward-service --once # 跑一轮，确认 health.json 生成
```

## macOS（launchd）
```bash
sed -e "s#/PATH/TO/1hao-#$(pwd)#g" -e "s#/PATH/TO/python3#$(pwd)/.venv/bin/python#g" deploy/launchd/com.brando-pa.forward.plist > ~/Library/LaunchAgents/com.brando-pa.forward.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.brando-pa.forward.plist   # 启动（开机自启、崩溃自动重启）
launchctl kickstart -k gui/$(id -u)/com.brando-pa.forward                             # 重启
launchctl bootout gui/$(id -u)/com.brando-pa.forward                                  # 停止
launchctl print gui/$(id -u)/com.brando-pa.forward | head -20                         # 状态
```
Mac 需关闭睡眠（系统设置 → 电池/节能 → 防止自动进入睡眠，或 `caffeinate -s`）。

## Linux（systemd）
```bash
sudo useradd -r -s /usr/sbin/nologin brando || true; sudo mkdir -p /opt && sudo cp -r . /opt/1hao- && sudo chown -R brando /opt/1hao-
sudo cp deploy/systemd/brando-pa-forward.service deploy/systemd/brando-pa-health.* /etc/systemd/system/
sudo cp deploy/.env.example /etc/default/brando-pa-forward
sudo systemctl daemon-reload && sudo systemctl enable --now brando-pa-forward brando-pa-health.timer   # 启动 + 开机自启
sudo systemctl restart brando-pa-forward      # 重启
sudo systemctl stop brando-pa-forward         # 停止
systemctl status brando-pa-forward; journalctl -u brando-pa-forward -f    # 状态 / 日志
```

## Linux（Docker Compose）
```bash
cp deploy/.env.example deploy/.env
docker compose -f deploy/docker/docker-compose.yml up -d --build   # 启动（restart: unless-stopped）
docker compose -f deploy/docker/docker-compose.yml restart forward  # 重启
docker compose -f deploy/docker/docker-compose.yml down             # 停止
docker compose -f deploy/docker/docker-compose.yml ps; docker logs -f brando-pa-forward   # 状态 / 日志
```

## 状态与日志检查（任意方案）
```bash
PYTHONPATH=src python -m brando_pa.cli forward-health      # 退出码 0 健康 / 1 异常（心跳年龄、数据新鲜度、质量门控、哈希链、容量）
PYTHONPATH=src python -m brando_pa.cli forward-status      # 账户状态 + 事件链校验
cat reports_out/brando_pa/phase8_forward/health.json       # 心跳
tail -f reports_out/brando_pa/phase8_forward/service.log   # 服务日志（10 MB × 5 轮转）
tail reports_out/brando_pa/phase8_forward/alerts.jsonl     # 告警
ls reports_out/brando_pa/phase8_forward/daily/             # 每日 UTC 账户报告
cat data_store/brando_pa/forward_data_status.json          # 行情来源 / 最后成功更新 / 一致性 / 来源切换
```

## 行为说明
- 主循环每 `BRANDO_PA_INTERVAL_S`（默认 600 s）：行情更新 → 账户推进 → 心跳 → UTC 日切日报 → 容量检查 → 告警。
- 断网：来源重试与回退；全部失败则本轮不新增订单，保留上次成功数据；恢复后自动补齐 K 线。
- 服务离线期间错过的日线信号：恢复后只记录 `missed_signal`（含当时应有的决定），**不回填成交**；下一次实时日线收盘按当时价格正常执行。
- 数据过期（1h 落后 > 3 小时）、单根 > 20% 跳变、来源切换重叠偏差 > 0.5%、1h→1d 不一致：暂停新增模拟订单并告警，不强制卖出。
- 账户状态 `state.json` 原子写，重启恢复；事件 `events.jsonl` 只追加 + sha256 哈希链（不轮转，体积约 2 KB/天/账户）。
