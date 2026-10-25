"""Authorization boundary enforcement.

Guarantees the tool only ever reads contracts the operator was authorized to
audit, and that any chain interaction targets a LOCAL test chain — never a
public/mainnet endpoint, never real assets.
"""
from __future__ import annotations
import os
from pathlib import Path
from urllib.parse import urlparse
import yaml


class AuthorizationError(Exception):
    """Raised when an action would exceed the authorized scope."""


class Authorization:
    def __init__(self, project_root: Path, config_path: Path):
        self.root = project_root.resolve()
        self.config_path = config_path
        with open(config_path, "r", encoding="utf-8") as fh:
            self.cfg = yaml.safe_load(fh)
        self.authorized_paths = [
            (self.root / p).resolve() for p in self.cfg.get("authorized_paths", [])
        ]
        self.allowed_rpc_hosts = set(self.cfg.get("allowed_rpc_hosts", []))

    # --- source scope ---
    def is_authorized_file(self, path: Path) -> bool:
        rp = path.resolve()
        return any(self._within(rp, base) for base in self.authorized_paths)

    @staticmethod
    def _within(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    def authorized_sol_files(self):
        files = []
        for base in self.authorized_paths:
            if base.is_dir():
                files.extend(sorted(base.rglob("*.sol")))
        return files

    def assert_file_authorized(self, path: Path):
        if not self.is_authorized_file(path):
            raise AuthorizationError(
                f"拒绝分析未授权文件: {path}\n"
                f"仅 {self.config_path} 中 authorized_paths 列出的路径可被审计。"
            )

    # --- chain scope ---
    def assert_local_rpc(self, rpc_url: str | None):
        """Any provided RPC must point at an allowlisted local host."""
        if not rpc_url:
            return  # default: forge in-process EVM, no network
        host = urlparse(rpc_url).hostname or ""
        if host not in self.allowed_rpc_hosts:
            raise AuthorizationError(
                f"拒绝连接非本地 RPC 主机 '{host}'。只允许本地测试链: "
                f"{sorted(self.allowed_rpc_hosts)}。不向主网发送交易、不接触真实资产。"
            )

    def assert_no_mainnet_fork(self, env: dict | None = None):
        env = env if env is not None else os.environ
        for key in ("ETH_RPC_URL", "FOUNDRY_ETH_RPC_URL", "FORK_URL"):
            val = env.get(key)
            if val:
                self.assert_local_rpc(val)

    # --- importing authorized real contracts ---
    def import_dir(self) -> Path:
        d = self.root / "contracts" / "imported"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def record_attestation(self, record: dict):
        import json
        from datetime import datetime, timezone
        log = self.root / "config" / "imported_attestations.jsonl"
        record = dict(record)
        record["recorded_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return str(log)

    def import_contract(self, src: Path, attested_by: str, note: str) -> Path:
        """Copy an authorized real contract into contracts/imported/ and record
        the authorization attestation. Only .sol files are accepted."""
        src = Path(src)
        if src.suffix != ".sol":
            raise AuthorizationError(f"只接受 .sol 源文件: {src}")
        if not src.is_file():
            raise AuthorizationError(f"文件不存在: {src}")
        if not attested_by:
            raise AuthorizationError(
                "导入真实合约需声明授权来源 (--attested-by)：你必须拥有或已获得该合约的审计授权。")
        dest = self.import_dir() / src.name
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        self.record_attestation({
            "source": str(src.resolve()), "dest": str(dest),
            "attested_by": attested_by, "note": note,
            "engagement": self.cfg.get("authorization", {}).get("engagement", ""),
        })
        return dest
