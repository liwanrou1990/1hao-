# Round 4 pre-registration (V0.15 read-only baseline)

Written and committed **before** any round-4 detector run. The engine freeze
(`eval/engine_freeze.json`) is committed in the same commit. Nothing below may
change after results exist.

## Engine under test
V0.15 **read-only audit baseline**: exact historical compiler build
(`dao_audit/compiler/*`, solc-js) + Slither 0.11.6 built-in capabilities only
(`dao_audit/analysis/baseline.py`). It is NOT the full permission-semantics
engine; that engine was not built (see FINAL_REPORT_V0.15.md, limitations).

- **Scored track ("primary")**: Slither built-in detector results mapped to
  classes (unprotected-upgrade, tx-origin, protected-vars → VULN-1; suicidal,
  arbitrary-send-eth/erc20/erc20-permit, controlled-delegatecall → VULN-4),
  restricted to the case's own contract files and to code reachable from at
  least one entry point without time/lifecycle conditions (dev-set rule, fixed
  before round 4).
- **Exploratory track ("with_heuristic")**: primary + HEURISTIC_ONLY /
  INCONCLUSIVE signals. Reported, never used for the verdict.
- VULN-2 and VULN-3 have no Slither capability → expected recall 0 for those
  classes; reported as not covered, not as safe.

## Dataset
`eval/dataset_r4/`: 28 cases (14 vulnerable, 10 fixed, 4 secure controls),
4 chain cases, no project overlap with rounds 1–3, curated by an agent that
never read the detector; answers in `eval/answers/` read only by
`eval/evaluate_v15.py`. Source types: 15 exploited incidents, 7 audit
findings, 2 bug bounties, 4 controls; 7 cases marked source_certainty=uncertain.
Isolation: `eval/check_isolation.py` static check PASS before the run; runtime
check (files opened by the detector) is run after and reported.

## Metrics (all with Wilson 95% CI)
Per class and overall Recall, Precision (located-TP rule of PREREGISTRATION.md
§3), safe/fixed FPR (≥1 high/critical governance finding), case detection
rate, fixed-version discrimination rate (vulnerable version located ≥1 truth
item AND its fixed version has no high/critical governance finding),
full-chain identification rate (PREREGISTRATION.md rule: ≥2 located classes
incl. a treasury class) reported **separately** from ordinary detection;
unanalysable cases listed, not dropped; breakdown by source type / certainty.

## Verdict (unchanged V0.12 criteria, primary track only)
Conclusive (N_vuln=14 ≥ 10, chain cases 4 ≥ 3). PASS needs all of:
1. overall Recall ≥ 0.70 with CI lower bound ≥ 0.50;
2. full-chain identification rate ≥ 0.50;
3. safe FPR ≤ 0.30.
PARTIAL = 1–2 met; FAIL = none. One run; no re-run after seeing results except
to fix a crash, which must be reported.

## Expectation stated in advance
Dev-set (rounds 1–3, not blind) primary track: Recall 0.12, Precision 0.29,
safe FPR 0.29, chain 0/4. A FAIL on round 4 is the expected outcome for a
detector-only baseline; the run measures it rather than assuming it.
