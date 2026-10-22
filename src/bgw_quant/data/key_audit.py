"""密钥审计（本地、离线、不输出任何密钥）。

  * 检查仓库工作区、git 历史、docs/reports/logs 中是否出现 .env 内的密钥或给定的历史密钥指纹；
  * 对一个“未知的 32 位十六进制值”按格式与哈希关系（MD5/SHA 截断 of key/secret）判断它可能属于哪个字段；
    结论只给“匹配/不匹配/候选字段”，绝不打印该值或任何密钥。
输入只从环境变量读取（BGWQ_AUDIT_VALUE、BGWQ_AUDIT_OLD_SECRETS），不走命令行参数，避免进入 shell 历史。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..config import PROJECT_ROOT, get_settings

SCAN_DIRS = ("docs", "reports_out", "logs", "src", "config", "tests", "frontend/src")


def _fingerprint(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:12]


def _grep_tracked(value: str) -> bool:
    if not value:
        return False
    r = subprocess.run(["git", "grep", "-q", "-i", "-F", value], cwd=PROJECT_ROOT, capture_output=True)
    return r.returncode == 0


def _in_history(value: str) -> bool:
    if not value:
        return False
    r = subprocess.run(["git", "log", "--all", "-S", value, "--oneline"], cwd=PROJECT_ROOT, capture_output=True, text=True)
    return bool(r.stdout.strip())


def _scan_files(values: list[str]) -> list[str]:
    hits = []
    for d in SCAN_DIRS:
        base = PROJECT_ROOT / d
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if not f.is_file() or f.suffix in (".duckdb", ".png", ".pyc", ".woff", ".woff2"):
                continue
            try:
                t = f.read_text(errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if any(v and v.lower() in t.lower() for v in values):
                hits.append(str(f.relative_to(PROJECT_ROOT)))
    return hits


def classify_hex_value(value: str, key: str, secrets: list[str]) -> dict[str, Any]:
    v = value.strip().lower()
    out: dict[str, Any] = {"length": len(v), "is_hex": bool(re.fullmatch(r"[0-9a-f]+", v)), "hash_relation": None, "candidates": [], "conclusion": ""}
    if not out["is_hex"]:
        out["conclusion"] = "不是十六进制串"
        return out
    rel = []
    mats = {"api_key": key, **{f"secret_{i}": s for i, s in enumerate(secrets)}}
    for name, m in mats.items():
        if not m:
            continue
        for variant, mv in (("raw", m), ("lower", m.lower()), ("upper", m.upper())):
            if hashlib.md5(mv.encode()).hexdigest() == v:
                rel.append(f"md5({name},{variant})")
            for hn in ("sha1", "sha256"):
                h = hashlib.new(hn, mv.encode()).hexdigest()
                if h[:32] == v or h[-32:] == v:
                    rel.append(f"{hn}_trunc({name},{variant})")
    out["hash_relation"] = rel or None
    if len(v) == 16:
        out["candidates"].append("BGW_API_KEY（Bitget Wallet ToB x-api-key：16 位十六进制）")
    elif len(v) == 40:
        out["candidates"].append("BGW_API_SECRET（Bitget Wallet ToB secret：40 位十六进制）")
    elif len(v) == 32:
        out["candidates"] += ["Bitget Wallet ToB 商户/项目标识（appId/projectId，32 位十六进制常见）——非签名字段",
                              "某些平台的 MD5 形式口令/校验值",
                              "不是 Bitget 交易所 API Key（交易所 key 以 bg_ 前缀、secret 为 64 位）",
                              "不是 Massive/Polygon Key（含大小写字母）"]
    elif len(v) == 64:
        out["candidates"].append("Bitget 交易所 API Secret（64 位）——本系统不使用交易所 API")
    if rel:
        out["conclusion"] = "该值是本地密钥的哈希派生值：" + "、".join(rel) + "；不是独立字段"
    elif len(v) == 32:
        out["conclusion"] = ("未能与本地任何密钥建立哈希关系；格式不匹配 x-api-key(16) / secret(40)。"
                             "最可能是 Bitget Wallet 控制台中的商户/项目标识（不参与签名），本系统任何接口都不需要它；在确认用途前不存储。")
    else:
        out["conclusion"] = "见 candidates；在确认字段前不存储"
    return out


def run_key_audit() -> dict[str, Any]:
    s = get_settings()
    key, sec = s.bgw_api_key, s.bgw_api_secret
    old = [x.strip() for x in os.environ.get("BGWQ_AUDIT_OLD_SECRETS", "").split(",") if x.strip()]
    unknown = os.environ.get("BGWQ_AUDIT_VALUE", "").strip()
    values = [v for v in [key, sec, *old] if v]
    rep: dict[str, Any] = {
        "env_file_present": (PROJECT_ROOT / ".env").exists(),
        "env_file_gitignored": ".env" in (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8") if (PROJECT_ROOT / ".gitignore").exists() else False,
        "has_key": bool(key), "has_secret": bool(sec), "key_fingerprint": _fingerprint(key) if key else None, "secret_fingerprint": _fingerprint(sec) if sec else None,
        "old_secret_fingerprints": [_fingerprint(x) for x in old],
        "leak_tracked_files": [n for n, v in (("api_key", key), ("secret", sec), *((f"old_secret_{i}", x) for i, x in enumerate(old))) if _grep_tracked(v)],
        "leak_git_history": [n for n, v in (("api_key", key), ("secret", sec), *((f"old_secret_{i}", x) for i, x in enumerate(old))) if _in_history(v)],
        "leak_files_scan": _scan_files(values),
        "unknown_value": classify_hex_value(unknown, key, [sec, *old]) if unknown else None,
        "rotation_required": True,
        "rotation_reason": "API Secret 曾以明文出现在对话/聊天渠道（聊天记录不受本仓库控制），按最小权限原则应在 Bitget Wallet 控制台重置并更新本地 .env",
        "secrets_in_output": False,
    }
    rep["clean"] = not (rep["leak_tracked_files"] or rep["leak_git_history"] or rep["leak_files_scan"])
    return rep
