"""Regression tests for V0.16 toolchain fixes (found in the Round 4 failure audit)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))

from dao_audit.compiler import project as P  # noqa: E402
from dao_audit.compiler.toolchain import ensure_solc  # noqa: E402


def test_remapping_trailing_slash_and_dedupe():
    r = P.normalize_remappings(["modules/=src/modules/", "modules/=src/modules", "x/=lib/x"])
    assert r == ["modules/=src/modules/", "x/=lib/x/"]
    assert P.apply_remap("x/A.sol", r) == "lib/x/A.sol"


def test_monorepo_yarn_lock_uses_declared_range(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "package.json").write_text(json.dumps(
        {"dependencies": {"@openzeppelin/contracts": "^4.8.0"}}))
    (tmp_path / "yarn.lock").write_text(
        '"@openzeppelin/contracts@^3.1.0":\n  version "3.1.0"\n\n'
        '"@openzeppelin/contracts@^4.8.0":\n  version "4.8.2"\n')
    v, src = P.locked_version(tmp_path, "pkg", "@openzeppelin/contracts")
    assert v == "4.8.2" and src == "yarn.lock"


def test_exact_declaration_wins(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"a": "1.2.3"}}))
    (tmp_path / "yarn.lock").write_text('"a@^1.0.0":\n  version "1.9.0"\n')
    assert P.locked_version(tmp_path, "", "a")[0] == "1.2.3"


def test_mispackaged_npm_solc_is_rejected_or_fixed():
    m = ensure_solc("0.8.7")
    assert m["version_string"].startswith("0.8.7+")
