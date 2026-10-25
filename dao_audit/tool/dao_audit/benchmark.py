"""Safe-vs-vulnerable comparison benchmark.

Runs the detector engine across a labeled corpus (targets = positive,
fixed/mocks = negative) and computes a per-rule and overall confusion matrix:
detection rate (recall), false-positive rate, miss/false-negative rate,
precision. This measures the analyzer on BOTH vulnerable and safe contracts,
not only the vulnerable mocks.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List
import yaml

from .analysis.solmodel import parse_source
from .analysis.detectors import run_detectors


@dataclass
class RuleMetrics:
    rule_id: str
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def recall(self) -> float:        # detection rate / 检出率
        d = self.tp + self.fn
        return self.tp / d if d else 1.0

    @property
    def fpr(self) -> float:           # false positive rate / 误报率
        d = self.fp + self.tn
        return self.fp / d if d else 0.0

    @property
    def miss_rate(self) -> float:     # false negative rate / 漏报率
        return 1.0 - self.recall

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 1.0


def load_labels(project_root: Path, labels_path: Path) -> dict:
    with open(labels_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def run_benchmark(project_root: Path, labels_cfg: dict) -> dict:
    universe: List[str] = labels_cfg["rules_universe"]
    labels: Dict[str, List[str]] = labels_cfg["labels"]

    per_file = {}
    # accumulate confusion counts per rule
    counts = {r: {"tp": 0, "fp": 0, "fn": 0, "tn": 0} for r in universe}

    for rel, expected in labels.items():
        path = project_root / rel
        fired = set()
        if path.is_file():
            su = parse_source(path)
            fired = {f.rule_id for f in run_detectors(su)}
        exp = set(expected)
        per_file[rel] = {"expected": sorted(exp), "fired": sorted(fired),
                         "exists": path.is_file()}
        for r in universe:
            e = r in exp
            p = r in fired
            if e and p:
                counts[r]["tp"] += 1
            elif p and not e:
                counts[r]["fp"] += 1
            elif e and not p:
                counts[r]["fn"] += 1
            else:
                counts[r]["tn"] += 1

    rule_metrics = {r: RuleMetrics(r, **counts[r]) for r in universe}
    agg = {"tp": sum(c["tp"] for c in counts.values()),
           "fp": sum(c["fp"] for c in counts.values()),
           "fn": sum(c["fn"] for c in counts.values()),
           "tn": sum(c["tn"] for c in counts.values())}
    overall = RuleMetrics("ALL", **agg)

    return {
        "universe": universe,
        "per_file": per_file,
        "per_rule": {r: asdict(m) | {
            "recall": round(m.recall, 4), "fpr": round(m.fpr, 4),
            "miss_rate": round(m.miss_rate, 4), "precision": round(m.precision, 4),
        } for r, m in rule_metrics.items()},
        "overall": asdict(overall) | {
            "recall": round(overall.recall, 4), "fpr": round(overall.fpr, 4),
            "miss_rate": round(overall.miss_rate, 4), "precision": round(overall.precision, 4),
        },
        "corpus": {
            "positive_files": sum(1 for v in labels.values() if v),
            "negative_files": sum(1 for v in labels.values() if not v),
            "total_files": len(labels),
        },
    }


def to_markdown(bench: dict) -> str:
    o = bench["overall"]
    c = bench["corpus"]
    lines = ["# 对照基准：检出率 / 误报率 / 漏报率", "",
             f"语料：{c['positive_files']} 个漏洞合约（正样本）, {c['negative_files']} 个安全合约（负样本）, "
             f"共 {c['total_files']} 个文件 × {len(bench['universe'])} 类规则。", "",
             "## 总体", "",
             f"- 检出率 (recall): **{o['recall']*100:.1f}%**  (TP={o['tp']}, FN={o['fn']})",
             f"- 误报率 (FPR): **{o['fpr']*100:.1f}%**  (FP={o['fp']}, TN={o['tn']})",
             f"- 漏报率: **{o['miss_rate']*100:.1f}%**",
             f"- 精确率 (precision): **{o['precision']*100:.1f}%**", "",
             "## 分规则", "",
             "| 规则 | TP | FP | FN | TN | 检出率 | 误报率 | 漏报率 | 精确率 |",
             "|------|----|----|----|----|--------|--------|--------|--------|"]
    for r in bench["universe"]:
        m = bench["per_rule"][r]
        lines.append("| {r} | {tp} | {fp} | {fn} | {tn} | {rc:.0%} | {fpr:.0%} | {mr:.0%} | {pr:.0%} |".format(
            r=r, tp=m["tp"], fp=m["fp"], fn=m["fn"], tn=m["tn"],
            rc=m["recall"], fpr=m["fpr"], mr=m["miss_rate"], pr=m["precision"]))
    lines += ["", "## 逐文件", "",
              "| 文件 | 期望 | 实检 | 结果 |", "|------|------|------|------|"]
    for rel, v in bench["per_file"].items():
        exp, fired = set(v["expected"]), set(v["fired"])
        if not v["exists"]:
            verdict = "⚠️ 缺失"
        elif exp == fired:
            verdict = "✅ 一致"
        else:
            miss = exp - fired
            extra = fired - exp
            parts = []
            if miss: parts.append("漏:" + ",".join(sorted(miss)))
            if extra: parts.append("误:" + ",".join(sorted(extra)))
            verdict = "❌ " + " ".join(parts)
        lines.append(f"| `{rel.split('/')[-1]}` | {','.join(v['expected']) or '-'} | "
                     f"{','.join(v['fired']) or '-'} | {verdict} |")
    return "\n".join(lines)
