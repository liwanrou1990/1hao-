from .base import BarContext, Signal, Strategy  # noqa: F401
from .breakout_retest import BreakoutRetest  # noqa: F401
from .reclaim import Reclaim  # noqa: F401
from .rejection import Rejection  # noqa: F401
from .structure_entry import BreakoutSecondConfirm, StructureEntryAtZone  # noqa: F401

STRATEGY_CLASSES = {"A_breakout_retest": BreakoutRetest, "B_reclaim": Reclaim, "C_rejection": Rejection, "A2_structure_entry": StructureEntryAtZone, "A3_breakout_second_confirm": BreakoutSecondConfirm}


def make_strategies(cfg_strats: dict, enabled_only: bool = True) -> dict:
    out = {}
    for key, cls in STRATEGY_CLASSES.items():
        p = cfg_strats.get(key, {})
        if enabled_only and not p.get("enabled", True):
            continue
        out[key] = cls(key, p)
    return out
