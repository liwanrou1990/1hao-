"""第二十二阶段：真实买盘与订单流监测（预登记 docs/brando_pa_validation/phase22_orderflow/preregistration.md）。
只用交易所公开逐笔成交的主动方向（taker side）统计主动买卖；不使用普通 K 线成交量冒充主动买盘；不交易、不改动任何现有策略与账户。"""
from ..config import PROJECT_ROOT

SYMS = ("BTC-USDT", "ETH-USDT")
CB_PRODUCT = {"BTC-USDT": "BTC-USD", "ETH-USDT": "ETH-USD"}            # Coinbase 以美元计价（USD），与 USDT 分开标注
LARGE_USDT = 100_000.0                                                 # 大额成交门槛（单笔名义金额）
DATA_SUBDIR = "phase22_orderflow"                                      # own_data_dir 下：归档聚合、实时原始成交、盘口摘要
OUT_DIR = PROJECT_ROOT / "reports_out" / "brando_pa" / "phase22_orderflow"
COST_ROUND_TRIP_BPS = 30.0
