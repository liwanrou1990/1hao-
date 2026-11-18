# 香港六合彩特别号码历史形态研究系统

中文界面、本地运行（macOS / Windows / Linux）。目标是**检验预先登记的固定历史形态，对下一期特别号码（或其生肖）是否具有样本外预测价值**。
系统允许、并且很可能得出“**未发现可靠预测优势**”的结论。本系统**不提供投注建议，不接入任何投注或资金操作**。

技术栈：Python 3.10–3.13 + SQLite + Streamlit（依赖版本见 `requirements.txt`，完整锁定见 `requirements-lock.txt`）。

## 一键启动

| 系统 | 命令 |
|---|---|
| macOS | 双击 `scripts/start_mac.command`，或终端执行 `./scripts/start_mac_linux.sh` |
| Linux | `./scripts/start_mac_linux.sh` |
| Windows | 双击 `scripts\start_windows.bat` |

脚本会在项目下创建 `.venv`，安装锁定依赖，初始化两个数据库，并打开 http://localhost:8501 。
需预先安装 Python 3.10–3.13（Windows 建议从 python.org 安装并勾选“Add to PATH”）。

手动方式：
```bash
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m streamlit run app/main.py
```

## 真实数据（目前尚缺）

系统**不会编造数据或接口**。真实数据库目前为空，需要导入带来源的官方开奖 CSV：
```bash
python -m marksix --kind real import-csv 开奖.csv --source "HKJC 官方结果页 https://bet.hkjc.com/ch/marksix/results"
python -m marksix --kind real validate
```
CSV 模板见 `data/templates/开奖数据模板.csv`（列：期号, 开奖日期, 正码1..正码6, 特别号码, 来源, 采集时间；也接受英文列名或“正码”一列）。
- 同一文件重复导入不产生新记录；已有期号内容不同会被**拒绝**，只有加 `--correction-reason "原因"` 时才追加新版本，旧版本保留。
- 每个导入文件原样保存为快照（SHA-256），并在 `data/real/raw/` 留副本。
- 官方接口采集模块 `marksix/hkjc_fetch.py` 为**实验性、未验证**：开发环境中未实际调用官方数据接口，须由用户在本机确认实际接口后自行配置，在此之前请用 CSV 导入。
- 49 号码制度起点：在 `config/settings.json` 的 `regime_49.start_date/source` 填写经官方资料核验的日期；未填写时用“数据中首次出现 48/49 号”的保守推断，并在所有页面标注“未经官方核验”。

## 研究流程（命令行，与界面等价）

```bash
python -m marksix --kind real patterns --sims 1000   # 开发期形态检验（只用开发段；首次运行会封存最后 20%）
python -m marksix --kind real dev                    # 开发期滚动回测，全部配置（含失败）写入实验记录
python -m marksix --kind real freeze                 # 每个模型族冻结开发期最优配置
python -m marksix --kind real final-test --confirm   # 一次性封存测试（再次运行会被标记为非独立）
python -m marksix --kind real predict-next           # 前瞻：开奖前记录预测
python -m marksix --kind real score-prospective      # 开奖后评分
python -m marksix --kind real report                 # 生成 reports/real/ 下的三份报告
```
合成数据全流程演示：`python -m marksix --kind synthetic pipeline --sims 1000`

## 目录

| 路径 | 内容 |
|---|---|
| `marksix/db.py` `ingest.py` | SQLite 存储、CSV 导入、快照与哈希、更正版本、真实/合成隔离 |
| `marksix/validate.py` | 重复、缺期、时间顺序、范围、七球重复、制度边界 |
| `marksix/zodiac.py` | 生肖映射（按日期、春节切换、号码数量基准） |
| `marksix/context.py` `patterns.py` | 35 条预先登记的固定形态 |
| `marksix/stats.py` | 精确检验、效应与区间、Holm/BH、蒙特卡洛 maxT |
| `marksix/models.py` | M0 均匀、M1 窗口频率、M2 形态事件频率、M2b 形态状态频率、M3 逻辑回归 |
| `marksix/metrics.py` `backtest.py` | 指标、封存、滚动回测、冻结、一次性测试、前瞻 |
| `marksix/report.py` | 数据质量报告、形态规则表、研究报告 |
| `app/` | 中文 Streamlit 界面（入口 `app/main.py`） |
| `tests/` | 140 项测试 |
| `reports/` | 已生成的报告（真实：尚缺数据；合成：流程演示；植入信号：功效检查） |
| `docs/实施与验证说明.md` | 已实现 / 已验证 / 尚缺数据 清单与设计决策 |

## 测试

```bash
./scripts/run_tests.sh            # Windows: scripts\run_tests_windows.bat
```
