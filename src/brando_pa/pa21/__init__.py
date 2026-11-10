"""第二十一阶段：25% BTC 年度再平衡固定观察基准（冻结）与独立前瞻观察账本（预登记 docs/brando_pa_validation/phase21_forward/preregistration.md）。
100,000 USDT 是虚拟研究本金，不是真实资产；不下单；不读取也不改动 Mac 三个 10,000 USDT 模拟账户。"""
from ..config import PROJECT_ROOT
from ..pa20 import MAC_ACCOUNTS_INITIAL_USDT, RESEARCH_INITIAL_USDT

FORWARD_START = "2026-11-11"            # 冻结之后第一个完整 UTC 日：该日收盘首次决策，次日开盘首次成交
ACCOUNTS = ("FIX25_Y", "B2_original", "SMA200", "BH_BTC")
LABELS = {"FIX25_Y": "25% BTC 年度再平衡（固定观察基准）", "B2_original": "原 B2（日线研究引擎）", "SMA200": "BTC SMA200", "BH_BTC": "BTC 买入持有"}
WARN = (0.35, 0.40, 0.45, 0.50)
BUDGET = 0.50
OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase21_forward"
SEED_DIR = PROJECT_ROOT / "data_store" / "brando_pa"                                  # 仓库内已提交的日线（只读，用于预热）
BARS_SUBDIR = "phase21_bars"; FEED_SUBDIR = "phase21_feed"                              # own_data_dir 下的独立行情仓与行情日志
__all__ = ["FORWARD_START", "ACCOUNTS", "LABELS", "WARN", "BUDGET", "OUT_DIR", "RESEARCH_INITIAL_USDT", "MAC_ACCOUNTS_INITIAL_USDT"]
