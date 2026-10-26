"""Capability tests: what Slither's data dependency handles natively, and what
the perm.deps supplement adds. Run on three compiler generations."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tool"))

from slither.analyses.data_dependency.data_dependency import is_dependent  # noqa: E402
from slither.core.declarations.solidity_variables import SolidityVariableComposed  # noqa: E402
from slither.slithir.operations import SolidityCall  # noqa: E402

from dao_audit.analysis.perm.deps import Deps  # noqa: E402
from dao_audit.analysis.perm.snippet import fn, load_snippet  # noqa: E402

SRC = """
pragma solidity VER;
contract IAuth { function canCall(address src, bytes4 sig) public view returns (bool); }
contract T {
  address owner; IAuth authority; mapping(address=>bool) isAdmin; mapping(address=>uint) idx;
  modifier onlyOwner() { require(msg.sender == owner); _; }
  function _owner() internal view returns (address) { return owner; }
  function _sender() internal view returns (address) { return msg.sender; }
  function isAuthorized(address src) internal view returns (bool) {
    if (src == owner) return true;
    return authority.canCall(src, msg.sig);
  }
  function ownerCheck() internal view returns (bool) {
    uint i = idx[msg.sender];
    if (i == 0) return false;
    return true;
  }
  function a() external { require(isAdmin[msg.sender]); }
  function b() external { require(_sender() == _owner()); }
  function c() external onlyOwner {}
  function e() external { require(isAuthorized(msg.sender)); }
  function g() external { require(ownerCheck()); }
  function h() external { require(authority.canCall(msg.sender, msg.sig)); }
  function setOwner(address o) external { owner = o; }
}"""
MS = SolidityVariableComposed("msg.sender")


def req_arg(f):
    for n in f.nodes:
        for ir in n.irs:
            if isinstance(ir, SolidityCall) and ir.function.name.startswith("require"):
                return ir.arguments[0]
    raise AssertionError("no require")


@pytest.fixture(scope="module", params=["0.4.24", "0.5.17", "0.8.19"])
def sl(request):
    src = SRC.replace("VER", request.param)
    if request.param >= "0.5":
        src = src.replace("contract IAuth { function canCall(address src, bytes4 sig) public view returns (bool); }",
                          "interface IAuth { function canCall(address src, bytes4 sig) external view returns (bool); }")
    return load_snippet(src, request.param)


# ---- native Slither behaviour (documents the gaps; if Slither improves these flip)
def test_native_internal_data_return(sl):
    f = fn(sl, "T", "b")
    assert is_dependent(req_arg(f), MS, f)


def test_native_cross_function_state(sl):
    c = [x for x in sl.contracts if x.name == "T"][0]
    owner = [v for v in c.state_variables if v.name == "owner"][0]
    assert is_dependent(owner, fn(sl, "T", "setOwner").parameters[0], c)


def test_native_external_call_args(sl):
    f = fn(sl, "T", "h")
    assert is_dependent(req_arg(f), MS, f)


def test_gap1_mapping_key_not_native(sl):
    f = fn(sl, "T", "a")
    assert not is_dependent(req_arg(f), MS, f)


def test_gap2_control_return_not_native(sl):
    for name in ("e", "g"):
        f = fn(sl, "T", name)
        assert not is_dependent(req_arg(f), MS, f)


# ---- supplement
def test_supp_mapping_key(sl):
    f = fn(sl, "T", "a")
    s = Deps().sources(f, req_arg(f))
    assert s.sender_reached() and [sv.name for sv, _ in s.lookups] == ["isAdmin"]
    assert s.lookups[0][1].sender


def test_supp_param_bound_control_return(sl):
    f = fn(sl, "T", "e")
    s = Deps().sources(f, req_arg(f))
    assert s.sender_reached()
    assert {"owner", "authority"} <= {v.name for v in s.all_state()}
    assert any(e.sig.startswith("canCall") for e in s.ext)


def test_supp_control_return_lookup(sl):
    f = fn(sl, "T", "g")
    s = Deps().sources(f, req_arg(f))
    assert s.sender_reached() and "idx" in {v.name for v in s.all_state()}


def test_supp_keeps_native(sl):
    f = fn(sl, "T", "b")
    s = Deps().sources(f, req_arg(f))
    assert s.sender and "owner" in {v.name for v in s.state}
