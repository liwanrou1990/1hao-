# V0.18 development pre-registration (written before any V0.18 engine work)

Branch: `claude/v018-dev` (from `f0e4cd2`, tag `v0.17-frozen`). The V0.17 freeze
(`eval/engine_freeze.json`, copy `eval/v17/engine_freeze_v17.json`) and all
Round 1–4 results stay unchanged on the main audit branch and on this branch.

## Fixed rules for this phase
1. **Dev set only.** The 44 cases (rounds 1–4) are regression samples. No number
   from them is a blind score. Round 5 is not started in this phase.
2. **No tuning against blind sets.** Any rule change is validated on the dev set
   with the full regression (`blind_run.py --dev`, both dev sets) and recorded
   with its before/after numbers; old blind results are never re-scored.
3. **Evidence standard cannot be lowered.** An alert whose reaching entry points
   carry protection evidence (msg.sender-dependent condition, signature check,
   or self-checked state) stays INCONCLUSIVE unless an analysis *proves* the
   protection ineffective on a concrete path. Identifier names never decide.
4. **Result classes** (automatic layer, exactly one per finding, with code
   evidence: file, lines, reaching entry points, state involved):
   - CONFIRMED: reachable path with no effective protection shown by analysis
   - SUSPECTED: pattern present, protection effectiveness unresolved, no
     counter-evidence
   - INCONCLUSIVE: protection evidence present, effectiveness unknown
   - REFUTED: premise shown false (e.g. `from` is msg.sender at every call site)
   Manual review results are a separate layer and never enter automatic metrics.
5. **Metrics reported separately** (dev set, Wilson 95% CI): automatic recall,
   precision, safe/fixed FPR, full-chain identification rate, unanalysable
   ratio, number of items sent to manual review.

## Acceptance criteria for considering a new blind test (all required)
- automatic recall on the dev set ≥ 0.50 counting CONFIRMED only; ≥ 0.70
  counting CONFIRMED + SUSPECTED;
- safe/fixed FPR (CONFIRMED + SUSPECTED) ≤ 0.20;
- fixed-version discrimination (strict) ≥ 0.50 of pairs;
- at least one automatic CONFIRMED or SUSPECTED finding in each of B and C;
- unanalysable ratio ≤ 0.10 with every unanalysable case listed.
Failing any item → Round 5 is NOT started and the report says NOT_READY.

## Scope limit recorded in advance
Developing a permission-semantics engine (taint over SlithIR, guard/dominator
analysis, cross-contract permission graph) has been stopped by the platform
safety check four times (V0.15 ×2, V0.17, V0.18 start). That work is not
retried or reworded in this phase. What remains possible without it is listed
in the V0.18 report; anything depending on it is reported as NOT_DONE.
