# iphone-farm — Mac + Sipolar A-832 + 30 台 iPhone 群控（非越狱）

> 主机 MacBook Pro M5 Pro 48GB；Hub Sipolar A-832（32 口 USB 2.0）；30 台 iPhone 6s/7/8 + 30 张实体 SIM。
> 控制链路采用 **YueChen-C/iPhone-use**（MIT）：USB 取画面（AVFoundation muxed 设备）+ 蓝牙 LE HID 送输入 + 本机 HTTP API（127.0.0.1:7390）。
> 不越狱、不装 App、不需要开发者账号。**暂不开发短信聚合工作台**；短信只在自有测试设备与授权联系人范围内、单机人工确认。

## 文档索引（docs/）

| 文档 | 内容 |
|---|---|
| [00_调研报告_iPhone-use与技术风险评估](docs/00_调研报告_iPhone-use与技术风险评估.md) | iPhone-use 架构/API/多机机制调研；USB 2.0 带宽、蓝牙并发、A-832 数据口数量三大风险 |
| [01_软件安装步骤](docs/01_软件安装步骤.md) | 第一阶段：装 iPhoneUse.app、ifarm、3 台连接/信任/AssistiveTouch 配对、独立控制验证 |
| [02_设备注册与编号流程](docs/02_设备注册与编号流程.md) | 改名 IF01..IF30 → 逐台插入 → `ifarm register` → 三重身份（capture_id / 名称 / UDID）+ 蓝牙 host 绑定 |
| [03_单机控制模块](docs/03_单机控制模块.md) | `ifarm run`：点击/滑动/键入/按键/Home/截图；动作前后的目标一致性校验 |
| [04_多机广播控制模块](docs/04_多机广播控制模块.md) | 第二阶段：`ifarm broadcast`，分组、白名单、并发/错峰、逐台结果 |
| [05_故障诊断与恢复模块](docs/05_故障诊断与恢复模块.md) | 第三阶段：状态机、错误记录、自动恢复（match/pair/calibration）、任务互斥 |
| [06_蓝牙瓶颈评估与XCTest备选方案](docs/06_蓝牙瓶颈评估与XCTest备选方案.md) | 蓝牙连接数成为瓶颈时的判定标准与 macOS + XCTest/WebDriverAgent 多设备方案 |
| [07_逐级实机测试报告](docs/07_逐级实机测试报告.md) | 第四阶段：5/10/15/20/30 台测试方法、指标定义、报告模板；附模拟自检（SIMULATED） |
| [08_短信测试规范](docs/08_短信测试规范.md) | 授权范围、单机限制、人工确认、禁止把模拟结果报告为真实 SMS 成功 |

## 快速开始（在 Mac 上）

```bash
cd iphone-farm
./scripts/install_mac.sh                 # 安装 /Applications/iPhoneUse.app（v1.0.1）、libimobiledevice、ifarm 虚拟环境
open -a iPhoneUse                        # 授予摄像头 + 蓝牙权限；或 --args --resident-quiet 后台常驻
./scripts/preflight_mac.sh               # USB 树 / usbmux / 蓝牙 / 服务 预检，输出 reports_out/preflight_*.txt
. .venv/bin/activate
ifarm check                              # iPhone-use 可达性、USB 枚举数、注册表
ifarm devices                            # 当前设备（id / 名称 / 蓝牙 / pointer）
ifarm register                           # 把名称为 IFxx 的设备注册为编号 xx（交叉核对 UDID，记录蓝牙 host）
ifarm verify --bind-hosts                # 核对注册表与实际状态
ifarm run 1 tap --x 0.5 --y 0.5          # 单机：编号 1 点击屏幕中心
ifarm run 2 open_settings                # 单机：Home → Spotlight → Settings → 回车
ifarm screenshot 3                       # 单机截图到 reports_out/screens/
ifarm broadcast home --group pilot       # 多机：对分组 pilot（1,2,3）广播 Home
ifarm broadcast open_settings --slots 1,2,3 --save
ifarm monitor                            # 状态检测 + 自动恢复循环（Ctrl-C 退出）
ifarm scale-test --levels 3,5,10,15,20,30 --pause-between   # 逐级实机测试，生成 reports_out/scale_test_REAL_*.md
ifarm events --kind error --tail 100     # 错误记录
```

离线自检（任何机器，**结果仅证明脚本可运行，标记 SIMULATED**）：

```bash
pip install -e ".[dev]" && pytest                       # 20 个用例，对内置 mock 服务器
./scripts/run_mock.sh 30 7399 &                          # 30 台模拟设备
IFARM_API_URL=http://127.0.0.1:7399 ifarm --simulated register --no-udid
IFARM_API_URL=http://127.0.0.1:7399 ifarm --simulated scale-test --stability-minutes 0.05 --samples 2
```

## 硬性原则

1. **每个动作必须指定完整真实设备 ID**：`client.py` 拒绝空设备；响应 `device` 回显必须等于请求，否则抛 `DeviceMismatch`。
2. **三重身份一致**才允许绑定编号：iPhone-use `capture_id`、手机名称 `IFxx`、USB 序列号/UDID；蓝牙 host 一对一。
3. **动作前校验**：名称未漂移、`pointer.host` 未漂移（防交叉绑定）、pointer 就绪；任一不满足即拒绝并记录错误。
4. **任务互斥**：每台设备一把锁（进程内 + 文件锁），广播、单机、监控恢复互不交叉。
5. **短信类与电源类动作禁止广播**；短信结果在人工确认前一律 `UNVERIFIED`。
6. **模拟数据不得冒充实机结果**：`--simulated` 使所有事件、报告带 `SIMULATED_MOCK` 标签，报告文件名带 `SIMULATED`。
7. **不得假设 32 个 USB 口就能稳定控制 30 台**：必须按 3→5→10→15→20→30 逐级实测并记录。

## 目录

```
iphone-farm/
  config/farm.yaml           API 地址、并发、恢复策略、逐级测试参数
  config/groups.yaml         设备分组（按编号）
  config/sms_policy.yaml     短信授权联系人（模板）
  src/iphone_farm/
    client.py                iPhone-use HTTP 客户端（设备 ID 强制、回显校验）
    registry.py              设备注册与编号（三重身份、蓝牙 host、macOS USB 枚举）
    actions.py               安全动作目录（home / open_settings / tap / swipe / type / key …）
    single.py                单机控制模块（身份校验 + 互斥 + 事件）
    broadcast.py             多机广播控制模块（分组、白名单、并发错峰、逐台结果）
    monitor.py               故障诊断与恢复模块（状态机、错误记录、自动恢复）
    scale_test.py            逐级测试与报告（REAL / SIMULATED 标签）
    locks.py / events.py     任务互斥、JSONL 事件日志
    mock_server.py           iPhone-use 模拟服务器（含故障注入，仅离线用）
    cli.py                   ifarm 命令行
  scripts/install_mac.sh     安装；preflight_mac.sh 预检；run_mock.sh 模拟
  tests/                     20 个离线用例
  docs/                      00–08 文档
  state/ reports_out/        运行时注册表/锁/事件；测试报告（已 gitignore）
```

---

# brando_pa — Brando Price Action 量化交易引擎（独立模块，研究/回测/模拟盘）

> 代码 `src/brando_pa/`，配置 `config/brando_pa.yaml`，数据 `data_store/brando_pa/`（15m/1h/1w 自有；1d/4h 只读复用 spot90），产物 `reports_out/brando_pa/`，测试 `tests/test_brando_pa_*.py`，报告 [docs/20_brando_pa_价格行为引擎.md](docs/20_brando_pa_价格行为引擎.md)。
> 不修改 spot90 / bgw_quant 的策略、数据与配置。**默认禁止真实下单**（仓库内没有任何交易所私有接口调用）。

```bash
pip install -r requirements.txt && export PYTHONPATH=src
python -m brando_pa.cli ingest                                  # 1w/1d/4h/1h/15m K 线（GitHub 公开 Binance 缓存；Bitget 可达时可切换适配器）
python -m brando_pa.cli analyze                                 # 关键区域 / 4h 结构 / 日线环境
python -m brando_pa.cli signal                                  # WAIT/LONG/SHORT/EXIT/NO_TRADE + 开仓/不交易理由 → reports_out/brando_pa/signals/latest.json
python -m brando_pa.cli --exec-tf 15m backtest --start 2025-02-01 --name m15     # 回测矩阵：全部/单策略 × 过滤开/关，含买入持有基准
python -m brando_pa.cli --exec-tf 1h  walkforward --start 2022-04-01 --name h1   # Walk-Forward（每个策略独立）
python -m brando_pa.cli paper --replay --replay-bars 6000      # 模拟盘全流程回放（订单去重、状态恢复）
python -m brando_pa.cli paper --once / --loop                   # 模拟盘单轮 / 持续运行（数据过期自动禁止新开仓）
python -m brando_pa.cli dashboard --port 8502                   # Streamlit 控制面板
python -m brando_pa.cli export                                 # CSV / JSON / HTML 研究导出
pytest tests/test_brando_pa_*.py -q                            # 单元测试：关键价位、跨周期对齐、三策略状态机、仓位/风控、保守成交规则、订单去重、无未来函数
# ---- 第二阶段（docs/21）----
python -m brando_pa.cli audit                                  # 数据质量与回测真实性审计（重复/缺口/错位/跨周期一致性/永续基差/资金费样本/成本假设）
python -m brando_pa.cli --exec-tf 1h experiments --start 2022-04-01 --name h1      # 入场(市价/限价) × 多周期(single/aligned/zone_limit) × 退出方案 × 策略 实验矩阵 + 漏斗
python -m brando_pa.cli --exec-tf 1h experiments --name h1_sens --only ALL__entry_market__mtf_single --set costs.funding.assumed_rate_8h=0.00004   # 成本敏感性
python -m brando_pa.cli --exec-tf 1h walkforward --name h1_p2 [--regime-gating] [--set mtf.mode=aligned]   # Walk-Forward（隔离 7 天、退出参数入网格、Bootstrap、扰动）
python -m brando_pa.cli evaluate --names h1_p2,m15_p2,h4_p2    # Strategy Evaluator：合格 / 仅研究 / 不合格
```
