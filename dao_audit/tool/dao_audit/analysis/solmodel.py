"""Heuristic structural model of Solidity source.

This is NOT a full compiler AST. It is a brace-matching parser that recovers
the structure governance audits care about: contracts/interfaces, their
functions (name, visibility, mutability, applied modifiers, body), modifier
definitions, and state variables. It is deliberately conservative and reports
its own uncertainty; dynamic PoC evidence and false-positive assessment sit on
top of it.

Masking: comments and string/hex literals are blanked (length- and
newline-preserving) so offsets map 1:1 to the original text for line numbers.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def mask_code(text: str) -> str:
    """Return text with comments and string literals replaced by spaces,
    preserving length and newlines so offset->line mapping is exact."""
    res = []
    i, n = 0, len(text)
    state = "code"
    quote = ""
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if state == "code":
            if two == "//":
                state = "line_comment"; res.append("  "); i += 2; continue
            if two == "/*":
                state = "block_comment"; res.append("  "); i += 2; continue
            if c in ('"', "'"):
                state = "string"; quote = c; res.append(" "); i += 1; continue
            res.append(c); i += 1; continue
        if state == "line_comment":
            if c == "\n":
                state = "code"; res.append("\n"); i += 1; continue
            res.append(" "); i += 1; continue
        if state == "block_comment":
            if two == "*/":
                state = "code"; res.append("  "); i += 2; continue
            res.append("\n" if c == "\n" else " "); i += 1; continue
        if state == "string":
            if c == "\\":
                res.append("  "); i += 2; continue
            if c == quote:
                state = "code"; res.append(" "); i += 1; continue
            res.append("\n" if c == "\n" else " "); i += 1; continue
    return "".join(res)


def _line_of(masked: str, off: int) -> int:
    return masked.count("\n", 0, off) + 1


def _match(masked: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    """Return index of the matching close for the opener at open_pos, or -1."""
    depth = 0
    for i in range(open_pos, len(masked)):
        ch = masked[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return -1


_KEYWORD_SPECIFIERS = {
    "public", "external", "internal", "private",
    "view", "pure", "payable", "nonpayable",
    "virtual", "override", "returns", "immutable", "constant",
}


@dataclass
class FunctionDef:
    name: str
    kind: str                 # function | constructor | fallback | receive | modifier
    visibility: str           # external|public|internal|private|'' (default)
    mutability: str           # view|pure|payable|''
    modifiers: List[str]      # applied modifier invocations (names only)
    overrides: bool
    header_line: int
    start_line: int
    end_line: int
    header: str               # raw header text (original)
    header_masked: str        # masked header (comments/strings blanked)
    body: str                 # masked body (for safe scanning)
    body_raw: str             # original body text
    body_offset: int          # absolute offset of body start in file

    def is_state_changing(self) -> bool:
        return self.mutability not in ("view", "pure")

    def is_externally_callable(self) -> bool:
        return self.visibility in ("external", "public", "")


@dataclass
class StateVar:
    name: str
    type_raw: str
    visibility: str
    line: int
    raw: str


@dataclass
class ContractDef:
    name: str
    kind: str                 # contract | interface | library | abstract contract
    bases: List[str]
    start_line: int
    end_line: int
    functions: List[FunctionDef] = field(default_factory=list)
    modifiers: List[FunctionDef] = field(default_factory=list)
    state_vars: List[StateVar] = field(default_factory=list)
    body_raw: str = ""
    body_masked: str = ""

    @property
    def is_concrete(self) -> bool:
        return self.kind in ("contract", "abstract contract")

    @property
    def defined_modifier_names(self) -> set:
        return {m.name for m in self.modifiers}


@dataclass
class SourceUnit:
    path: str
    text: str
    masked: str
    pragma: str
    contracts: List[ContractDef] = field(default_factory=list)


_CONTRACT_RE = re.compile(
    r"(?P<kind>abstract\s+contract|contract|interface|library)\s+(?P<name>\w+)"
    r"(?:\s+is\s+(?P<bases>[^{]+?))?\s*\{"
)
_FUNC_RE = re.compile(
    r"\b(?P<kw>function|constructor|fallback|receive|modifier)\b"
    r"(?:\s+(?P<name>\w+))?\s*\("
)


def _parse_header_specifiers(header_mask: str):
    """From a function header (after param list), extract visibility,
    mutability, applied modifier names, override flag."""
    visibility = ""
    mutability = ""
    overrides = False
    modifiers: List[str] = []
    # tokens like: public onlyOwner nonReentrant returns (uint)
    # strip returns(...) clause
    header_mask = re.sub(r"returns\s*\([^)]*\)", " ", header_mask)
    header_mask = re.sub(r"override\s*\([^)]*\)", " override ", header_mask)
    i = 0
    tokens = re.findall(r"\w+|\(", header_mask)
    # Re-scan with positions to capture modifier args grouping
    for m in re.finditer(r"(\w+)\s*(\([^)]*\))?", header_mask):
        word = m.group(1)
        if word in ("public", "external", "internal", "private"):
            visibility = word
        elif word in ("view", "pure", "payable"):
            mutability = word
        elif word == "override":
            overrides = True
        elif word in ("virtual", "returns", "constant", "immutable", "nonpayable"):
            continue
        else:
            # applied modifier (e.g. onlyOwner, onlyRole(ADMIN))
            modifiers.append(word)
    return visibility, mutability, modifiers, overrides


def _parse_functions(masked: str, text: str, body_start: int, body_end: int) -> (List[FunctionDef], List[FunctionDef]):
    funcs: List[FunctionDef] = []
    mods: List[FunctionDef] = []
    region = masked[body_start:body_end]
    for m in _FUNC_RE.finditer(region):
        abs_name_start = body_start + m.start()
        # Skip matches that are nested inside another function body: we only
        # want top-level members. Check brace depth from body_start.
        depth = masked.count("{", body_start, abs_name_start) - masked.count("}", body_start, abs_name_start)
        if depth != 0:
            continue
        kw = m.group("kw")
        name = m.group("name") or kw
        # param list: the '(' is at m.end()-1
        paren_open = body_start + m.end() - 1
        paren_close = _match(masked, paren_open, "(", ")")
        if paren_close < 0:
            continue
        # header runs from after params to first '{' or ';'
        j = paren_close + 1
        brace = masked.find("{", j)
        semi = masked.find(";", j)
        # choose nearest terminator
        if brace == -1 and semi == -1:
            continue
        if brace == -1:
            term, is_body = semi, False
        elif semi == -1:
            term, is_body = brace, True
        else:
            if brace < semi:
                term, is_body = brace, True
            else:
                term, is_body = semi, False
        header_mask = masked[paren_close + 1:term]
        visibility, mutability, applied, overrides = _parse_header_specifiers(header_mask)
        header_raw = text[abs_name_start:term].strip()
        header_mask_raw = masked[abs_name_start:term]
        if is_body:
            body_close = _match(masked, term, "{", "}")
            if body_close < 0:
                continue
            body_mask = masked[term:body_close + 1]
            body_raw = text[term:body_close + 1]
            end_line = _line_of(masked, body_close)
            body_off = term
        else:
            body_mask = ""
            body_raw = ""
            end_line = _line_of(masked, term)
            body_off = term
        fd = FunctionDef(
            name=name, kind=kw, visibility=visibility, mutability=mutability,
            modifiers=applied, overrides=overrides,
            header_line=_line_of(masked, abs_name_start),
            start_line=_line_of(masked, abs_name_start), end_line=end_line,
            header=header_raw, header_masked=header_mask_raw,
            body=body_mask, body_raw=body_raw, body_offset=body_off,
        )
        if kw == "modifier":
            mods.append(fd)
        else:
            funcs.append(fd)
    return funcs, mods


_STATEVAR_RE = re.compile(
    r"^\s*(?P<type>mapping\s*\([^;]*?\)|[A-Za-z_]\w*(?:\[\])?)\s+"
    r"(?P<spec>(?:public|private|internal|constant|immutable)\s+)*"
    r"(?P<name>\w+)\s*(?:=|;)"
)


def _parse_state_vars(masked: str, text: str, body_start: int, body_end: int) -> List[StateVar]:
    out: List[StateVar] = []
    i = body_start
    region_end = body_end
    # Walk top-level (depth 0 relative to contract body) statements.
    pos = body_start
    while pos < region_end:
        depth = masked.count("{", body_start, pos) - masked.count("}", body_start, pos)
        nl = masked.find("\n", pos)
        if nl == -1 or nl > region_end:
            nl = region_end
        if depth == 0:
            line_text = text[pos:nl]
            mm = _STATEVAR_RE.match(line_text)
            if mm and "function" not in line_text and "modifier" not in line_text \
                    and "event" not in line_text and "return" not in line_text:
                vis = "public" if "public" in (mm.group("spec") or "") else ""
                out.append(StateVar(name=mm.group("name"), type_raw=mm.group("type").strip(),
                                    visibility=vis, line=_line_of(masked, pos),
                                    raw=line_text.strip()[:160]))
        pos = nl + 1
    return out


def parse_source(path: Path) -> SourceUnit:
    text = Path(path).read_text(encoding="utf-8")
    masked = mask_code(text)
    pragma_m = re.search(r"pragma\s+solidity[^;]*;", masked)
    pragma = text[pragma_m.start():pragma_m.end()] if pragma_m else ""
    su = SourceUnit(path=str(path), text=text, masked=masked, pragma=pragma)
    for cm in _CONTRACT_RE.finditer(masked):
        brace_open = masked.find("{", cm.start())
        brace_close = _match(masked, brace_open, "{", "}")
        if brace_close < 0:
            continue
        bases = []
        if cm.group("bases"):
            bases = [b.strip().split("(")[0] for b in cm.group("bases").split(",") if b.strip()]
        funcs, mods = _parse_functions(masked, text, brace_open + 1, brace_close)
        svars = _parse_state_vars(masked, text, brace_open + 1, brace_close)
        su.contracts.append(ContractDef(
            name=cm.group("name"), kind=cm.group("kind").strip(), bases=bases,
            start_line=_line_of(masked, cm.start()), end_line=_line_of(masked, brace_close),
            functions=funcs, modifiers=mods, state_vars=svars,
            body_raw=text[brace_open:brace_close + 1],
            body_masked=masked[brace_open:brace_close + 1],
        ))
    return su
