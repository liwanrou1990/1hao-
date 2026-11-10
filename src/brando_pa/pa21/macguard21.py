"""Mac 现有系统保护：部署前基线快照与验收时对照（只读）。
现有三账户与影子服务仍在运行，文件会正常增长；因此：
- *.jsonl（只追加的账本 / 事件链）：基线时的全部内容必须仍是现在文件的前缀（历史未被改写），文件不得消失；
- 其他文件：只要求仍然存在（例如 health.json、state.json 由 Mac 服务正常改写）；
- 生产仓库 HEAD 与分支、三账户初始本金配置（initial_cash_usdt）、launchd 标签加载状态必须一致；
- 生产仓库内不得出现第二十一阶段的任何目录。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

WATCH = ("reports_out/brando_pa/phase8_forward", "reports_out/brando_pa/phase13_forward", "data_store/brando_pa/forward_bars", "data_store/brando_pa/phase13_bars")
LABELS = ("com.brando-pa.forward", "com.brando-pa.shadow13")
FORBIDDEN = ("reports_out/brando_pa/phase21_forward", "data_store/brando_pa/phase21_bars", "data_store/brando_pa/phase21_feed")


def _prefix_sha(p: Path, n: int) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        left = n
        while left > 0:
            b = f.read(min(1 << 20, left))
            if not b:
                break
            h.update(b); left -= len(b)
    return h.hexdigest()


def _git(root: Path, *a) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(root), *a], capture_output=True, text=True, timeout=20).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _launchd(label: str) -> str:
    try:
        r = subprocess.run(["launchctl", "list", label], capture_output=True, text=True, timeout=10)
        return "loaded" if r.returncode == 0 else "not_loaded"
    except Exception:  # noqa: BLE001
        return "unavailable"


def _capital(root: Path) -> list:
    try:
        import yaml
        txt = (root / "config" / "brando_pa.yaml").read_text(encoding="utf-8"); out = []

        def walk(x, path=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif path.endswith("initial_cash_usdt"):
                out.append([path, x])
        walk(yaml.safe_load(txt)); return out
    except Exception as e:  # noqa: BLE001
        return [["error", str(e)[:100]]]


def snapshot(prod_root: Path) -> dict:
    prod_root = Path(prod_root).expanduser(); files = {}
    for w in WATCH:
        d = prod_root / w
        for p in sorted(d.rglob("*")) if d.exists() else []:
            if p.is_file():
                st = p.stat(); rel = str(p.relative_to(prod_root))
                files[rel] = {"size": st.st_size, "prefix_sha256": _prefix_sha(p, st.st_size) if p.suffix == ".jsonl" else None}
    return {"prod_root": str(prod_root), "head": _git(prod_root, "rev-parse", "HEAD"), "branch": _git(prod_root, "rev-parse", "--abbrev-ref", "HEAD"), "status_porcelain": _git(prod_root, "status", "--porcelain", "--untracked-files=no"),
            "initial_cash_usdt": _capital(prod_root), "launchd": {lb: _launchd(lb) for lb in LABELS}, "files": files, "forbidden_present": [f for f in FORBIDDEN if (prod_root / f).exists()]}


def compare(base: dict, prod_root: Path | None = None) -> dict:
    root = Path(prod_root or base["prod_root"]).expanduser(); cur = snapshot(root); bad = []
    for rel, b in base["files"].items():
        p = root / rel
        if not p.exists():
            bad.append(f"文件消失：{rel}"); continue
        if b["prefix_sha256"] is not None:
            if p.stat().st_size < b["size"] or _prefix_sha(p, b["size"]) != b["prefix_sha256"]:
                bad.append(f"只追加文件的历史内容被改写：{rel}")
    for k in ("head", "branch", "status_porcelain"):
        if cur[k] != base[k]:
            bad.append(f"生产仓库 {k} 变化：{base[k]} → {cur[k]}")
    if cur["initial_cash_usdt"] != base["initial_cash_usdt"]:
        bad.append(f"三账户初始本金配置变化：{base['initial_cash_usdt']} → {cur['initial_cash_usdt']}")
    for lb, s in base["launchd"].items():
        if cur["launchd"].get(lb) != s:
            bad.append(f"launchd {lb}：{s} → {cur['launchd'].get(lb)}")
    if cur["forbidden_present"]:
        bad.append(f"生产目录中出现第二十一阶段目录：{cur['forbidden_present']}")
    grown = sum(1 for rel, b in base["files"].items() if (root / rel).exists() and (root / rel).stat().st_size > b["size"])
    return {"ok": not bad, "problems": bad, "n_files_baseline": len(base["files"]), "n_files_grown_normally": grown, "checked_at_head": cur["head"]}


if __name__ == "__main__":
    import sys
    if sys.argv[1] == "snapshot":
        s = snapshot(Path(sys.argv[2])); Path(sys.argv[3]).parent.mkdir(parents=True, exist_ok=True); Path(sys.argv[3]).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({k: s[k] for k in ("prod_root", "head", "branch", "launchd", "initial_cash_usdt", "forbidden_present")} | {"n_files": len(s["files"])}, ensure_ascii=False))
    else:
        r = compare(json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))); print(json.dumps(r, ensure_ascii=False, indent=1)); sys.exit(0 if r["ok"] else 1)
