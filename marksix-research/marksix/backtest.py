"""严格验证流程。

1. 封存：按时间把研究样本最后 20% 设为一次性测试集，写入 seal 表（期号边界＋测试段哈希）。
   之后新增的开奖（晚于封存测试段）归入“前瞻期”，不并入测试集。
2. 开发期：只把开发段切片（ctx[:n_dev]）交给形态统计和模型，测试段数据在物理上不可见。
   开发段内滚动：每期先用 ≤t 的结果预测 t+1，再读取 t+1 结果。调参只在这里进行。
3. 冻结：每个模型族按开发期 log loss 选出一个配置，写入 frozen_candidates。
4. 封存测试：只对冻结候选运行一次。第一次运行标记为“独立”；之后任何再次运行都标记为
   “非独立（测试集已被查看）”，不得作为独立证据。
5. 所有实验（含失败）写入 experiments，含参数、代码版本、配置哈希、数据哈希。
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import sqlite3
import traceback
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import config, db
from .context import Ctx, build_ctx
from .metrics import block_bootstrap_ci, hac_mean_ci, logloss, summarize
from .models import STATE_FAMILIES, Model, make_model, zodiac_probs
from .patterns import REGISTRY, registry_hash
from .validate import study_frame


class SealError(RuntimeError):
    pass


@dataclass
class Split:
    n_total: int
    n_dev: int
    n_test: int
    n_post: int
    last_dev_draw: str
    first_test_draw: str
    test_hash_ok: bool
    created_at: str

    @property
    def test_range(self):
        return self.n_dev, self.n_dev + self.n_test


def _rows_hash(df: pd.DataFrame) -> str:
    cols = ["draw_id", "draw_date", "main_1", "main_2", "main_3", "main_4", "main_5", "main_6", "special"]
    return hashlib.sha256(df[cols].to_csv(index=False).encode()).hexdigest()[:16]


def load_study(con: sqlite3.Connection, settings: dict):
    from .zodiac import ZodiacMap

    df_all = db.current_draws(con)
    df, regime = study_frame(df_all, settings) if not df_all.empty else (df_all, {"start": None})
    zmap = ZodiacMap.from_db(con)
    return df, regime, zmap


def get_or_create_seal(con: sqlite3.Connection, df: pd.DataFrame, settings: dict, create: bool = True) -> Split | None:
    row = con.execute("SELECT created_at, n_dev, n_test, last_dev_draw, first_test_draw, test_hash FROM seal").fetchone()
    if row is None:
        if not create or len(df) < 50:
            return None
        n = len(df)
        n_test = int(math.ceil(settings["split"]["test_fraction"] * n))
        n_dev = n - n_test
        con.execute("INSERT INTO seal(id, created_at, data_hash, n_total, n_dev, n_test, last_dev_draw, first_test_draw,"
                    " test_hash) VALUES (1,?,?,?,?,?,?,?,?)",
                    (db.now_iso(), db.data_hash(df), n, n_dev, n_test, df["draw_id"].iloc[n_dev - 1],
                     df["draw_id"].iloc[n_dev], _rows_hash(df.iloc[n_dev:])))
        con.commit()
        row = con.execute("SELECT created_at, n_dev, n_test, last_dev_draw, first_test_draw, test_hash FROM seal").fetchone()
    created, _, n_test, last_dev, first_test, th = row
    ids = df["draw_id"].tolist()
    if last_dev not in ids:
        raise SealError(f"封存边界期号 {last_dev} 不在当前研究样本中，数据可能被更改")
    n_dev = ids.index(last_dev) + 1
    n_test = min(n_test, len(df) - n_dev)
    ok = _rows_hash(df.iloc[n_dev:n_dev + n_test]) == th
    return Split(len(df), n_dev, n_test, len(df) - n_dev - n_test, last_dev, first_test, ok, created)


def rolling_predict(model: Model, ctx: Ctx, target_lo: int, target_hi: int):
    """对目标期 [target_lo, target_hi) 逐期预测：先用 ≤t 的数据预测 t+1，再读取结果。"""
    model.prepare(ctx)
    P = np.empty((target_hi - target_lo, 49))
    for i, tgt in enumerate(range(target_lo, target_hi)):
        p = model.predict(tgt - 1)
        if p.shape != (49,) or not np.isfinite(p).all() or abs(p.sum() - 1) > 1e-9 or (p <= 0).any():
            raise ValueError(f"{model.describe()} 在第 {tgt} 期输出非法概率")
        P[i] = p
    y = ctx.s[target_lo:target_hi]
    return P, y


def _zodiac_parts(ctx: Ctx, P: np.ndarray, lo: int, hi: int):
    tg = np.arange(lo, hi)
    ok = ctx.zok[tg]
    if not ok.any():
        return None, None, None
    Zp = np.array([zodiac_probs(P[i], ctx.zm[t]) for i, t in enumerate(tg) if ok[i]])
    zy = ctx.z[tg][ok]
    Zbase = ctx.zc[tg][ok] / 49.0
    return Zp, zy, Zbase


def model_grid(settings: dict) -> list[tuple[str, dict]]:
    m = settings["models"]
    grid = [("M0", {})]
    grid += [("M1", {"window": w, "alpha": a}) for w, a in itertools.product(m["M1"]["window"], m["M1"]["alpha"])]
    grid += [("M2", {"rule": r.rule_id, "kappa": k}) for r in REGISTRY for k in m["M2"]["kappa"]]
    grid += [("M2b", {"family": f, "kappa": k}) for f in STATE_FAMILIES for k in m["M2b"]["kappa"]]
    if m["M3"].get("enabled"):
        grid += [("M3", {"C": c, "refit_every": m["M3"]["refit_every"]}) for c in m["M3"]["C"]]
    return grid


def _record(con, phase, model, params, settings, dhash, status, metrics=None, notes=None) -> int:
    cur = con.execute(
        "INSERT INTO experiments(created_at, phase, model, params, code_version, settings_hash, data_hash, status,"
        " metrics, notes) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (db.now_iso(), phase, model, json.dumps(params, ensure_ascii=False), config.code_version(),
         config.settings_hash(settings), dhash, status, json.dumps(metrics, ensure_ascii=False) if metrics else None,
         notes))
    con.commit()
    return cur.lastrowid


def _store_predictions(con, exp_id, ctx, lo, hi, P, y):
    ll = logloss(P, y)
    con.executemany(
        "INSERT INTO predictions(exp_id, draw_id, draw_date, probs, actual, logloss) VALUES (?,?,?,?,?,?)",
        [(exp_id, str(ctx.draw_ids[t]), str(pd.Timestamp(ctx.dates[t]).date()),
          json.dumps(np.round(P[i], 6).tolist()), int(y[i]), float(ll[i])) for i, t in enumerate(range(lo, hi))])
    con.commit()


def dev_ctx(df: pd.DataFrame, zmap, split: Split) -> Ctx:
    """只含开发段的上下文——测试段数据不会进入。"""
    return build_ctx(df.iloc[:split.n_dev].reset_index(drop=True), zmap)


def run_dev(con, settings: dict, progress=None, grid=None) -> dict:
    df, regime, zmap = load_study(con, settings)
    split = get_or_create_seal(con, df, settings)
    if split is None:
        raise SealError("研究样本不足 50 期，无法划分")
    ctx = dev_ctx(df, zmap, split)
    dhash = _rows_hash(df.iloc[:split.n_dev])
    lo = int(settings["dev_backtest"]["burn_in_fraction"] * split.n_dev)
    hi = split.n_dev
    grid = grid or model_grid(settings)
    base_P, y = rolling_predict(make_model("M0", {}), ctx, lo, hi)
    base_ll = logloss(base_P, y)
    results = []
    for i, (name, params) in enumerate(grid):
        if progress:
            progress(i / len(grid), f"{name} {params}")
        if name in ("M2",) and "rule" in params:
            from .patterns import RULES

            if RULES[params["rule"]].needs_zodiac and not ctx.zok.any():
                _record(con, "dev", name, params, settings, dhash, "failed", notes="生肖映射缺失，已停止")
                continue
        try:
            P, _ = rolling_predict(make_model(name, params), ctx, lo, hi)
            Zp, zy, Zb = _zodiac_parts(ctx, P, lo, hi)
            met = summarize(P, y, Zp, zy, Zb)
            d = logloss(P, y) - base_ll
            met["diff_vs_M0"] = hac_mean_ci(d)
            met["target_range"] = [str(ctx.draw_ids[lo]), str(ctx.draw_ids[hi - 1])]
            eid = _record(con, "dev", name, params, settings, dhash, "ok", met)
            results.append({"exp_id": eid, "model": name, "params": params, **met})
        except Exception as e:  # 失败结果同样保存
            _record(con, "dev", name, params, settings, dhash, "failed", notes=f"{e}\n{traceback.format_exc()[-1500:]}")
    if progress:
        progress(1.0, "完成")
    return {"split": split, "results": results, "dev_hash": dhash, "range": (lo, hi)}


def freeze(con, settings: dict, dev_results: list[dict] | None = None) -> dict:
    """每个模型族选开发期 log loss 最低的配置并冻结（只用开发期结果）。"""
    if dev_results is None:
        rows = pd.read_sql_query("SELECT exp_id, model, params, metrics, data_hash FROM experiments"
                                 " WHERE phase='dev' AND status='ok'", con)
        if rows.empty:
            raise SealError("没有开发期实验结果，无法冻结")
        latest_hash = rows["data_hash"].iloc[-1]
        rows = rows[rows["data_hash"] == latest_hash]
        dev_results = [{"exp_id": r.exp_id, "model": r.model, "params": json.loads(r.params), **json.loads(r.metrics)}
                       for r in rows.itertuples()]
        dhash = latest_hash
    else:
        dhash = None
    best = {}
    for r in dev_results:
        if r["model"] not in best or r["logloss"] < best[r["model"]]["logloss"]:
            best[r["model"]] = r
    cands = [{"model": m, "params": b["params"], "dev_logloss": b["logloss"], "dev_exp_id": b["exp_id"]}
             for m, b in sorted(best.items())]
    if dhash is None:
        dhash = con.execute("SELECT data_hash FROM experiments WHERE exp_id=?", (cands[0]["dev_exp_id"],)).fetchone()[0]
    n_cfg = {m: sum(1 for r in dev_results if r["model"] == m) for m in best}
    con.execute("INSERT INTO frozen_candidates(created_at, candidates, dev_exp_ids, data_hash, code_version)"
                " VALUES (?,?,?,?,?)",
                (db.now_iso(), json.dumps({"candidates": cands, "n_configs_tried": n_cfg}, ensure_ascii=False),
                 json.dumps([c["dev_exp_id"] for c in cands]), dhash, config.code_version()))
    con.commit()
    # 为冻结候选保存开发期逐期预测（逐期预测档案用），使用与开发期相同的开发段上下文
    try:
        df, _, zmap = load_study(con, settings)
        split = get_or_create_seal(con, df, settings, create=False)
        if split is not None:
            ctx = dev_ctx(df, zmap, split)
            lo = int(settings["dev_backtest"]["burn_in_fraction"] * split.n_dev)
            for c in cands:
                have = con.execute("SELECT COUNT(*) FROM predictions WHERE exp_id=?", (c["dev_exp_id"],)).fetchone()[0]
                if not have:
                    P, y = rolling_predict(make_model(c["model"], c["params"]), ctx, lo, split.n_dev)
                    _store_predictions(con, c["dev_exp_id"], ctx, lo, split.n_dev, P, y)
    except Exception:
        pass
    return {"candidates": cands, "n_configs_tried": n_cfg}


def latest_frozen(con):
    r = con.execute("SELECT id, created_at, candidates, data_hash FROM frozen_candidates ORDER BY id DESC LIMIT 1").fetchone()
    if r is None:
        return None
    return {"id": r[0], "created_at": r[1], **json.loads(r[2]), "data_hash": r[3]}


def final_test_status(con) -> dict:
    runs = pd.read_sql_query("SELECT * FROM final_test_runs ORDER BY id", con)
    return {"n_runs": len(runs), "runs": runs.to_dict("records"),
            "independent_done": bool(len(runs) and runs["independent"].iloc[0] == 1)}


def run_final_test(con, settings: dict, confirm: bool = False) -> dict:
    """一次性封存测试。需要 confirm=True。第二次及以后的运行标记为非独立。"""
    if not confirm:
        raise SealError("封存测试只能运行一次：请在冻结候选后显式确认（confirm=True / --confirm）")
    fz = latest_frozen(con)
    if fz is None:
        raise SealError("尚未冻结候选模型，禁止查看封存测试集")
    df, regime, zmap = load_study(con, settings)
    split = get_or_create_seal(con, df, settings, create=False)
    if split is None or split.n_test == 0:
        raise SealError("没有封存测试集")
    dev_hash = _rows_hash(df.iloc[:split.n_dev])
    if dev_hash != fz["data_hash"]:
        raise SealError("开发段数据与冻结时不一致（可能有更正），请重新做开发期实验并冻结")
    prev = final_test_status(con)
    independent = 0 if prev["n_runs"] else 1
    lo, hi = split.test_range
    ctx = build_ctx(df.iloc[:hi].reset_index(drop=True), zmap)
    dhash = _rows_hash(df.iloc[:hi])
    base_P, y = rolling_predict(make_model("M0", {}), ctx, lo, hi)
    base_ll = logloss(base_P, y)
    cands = [c for c in fz["candidates"]]
    if not any(c["model"] == "M0" for c in cands):
        cands = [{"model": "M0", "params": {}}] + cands
    m_compared = max(1, sum(1 for c in cands if c["model"] != "M0"))
    out, exp_ids = [], []
    for c in cands:
        try:
            P, _ = rolling_predict(make_model(c["model"], c["params"]), ctx, lo, hi)
            Zp, zy, Zb = _zodiac_parts(ctx, P, lo, hi)
            met = summarize(P, y, Zp, zy, Zb)
            d = logloss(P, y) - base_ll
            met["diff_vs_M0"] = hac_mean_ci(d)
            met["diff_vs_M0_bonf"] = hac_mean_ci(d, alpha=0.05 / m_compared)
            met["diff_block_boot"] = block_bootstrap_ci(d)
            met["independent"] = bool(independent)
            eid = _record(con, "final_test", c["model"], c["params"], settings, dhash, "ok", met,
                          notes=None if independent else "非独立：封存测试集此前已被查看")
            _store_predictions(con, eid, ctx, lo, hi, P, y)
            exp_ids.append(eid)
            out.append({"exp_id": eid, "model": c["model"], "params": c["params"], **met})
        except Exception as e:
            _record(con, "final_test", c["model"], c["params"], settings, dhash, "failed", notes=str(e))
    from .stats import rule_stats

    pat = rule_stats(ctx, lo, hi, settings["small_sample"])
    con.execute("INSERT INTO final_test_runs(created_at, frozen_id, independent, exp_ids, note) VALUES (?,?,?,?,?)",
                (db.now_iso(), fz["id"], independent, json.dumps(exp_ids),
                 json.dumps({"patterns": pat.to_dict("records"), "registry_hash": registry_hash()},
                            ensure_ascii=False, default=float)))
    con.commit()
    return {"independent": bool(independent), "results": out, "patterns": pat, "split": split,
            "conclusion": conclusion(out, bool(independent))}


def conclusion(results: list[dict], independent: bool) -> str:
    winners = [r for r in results if r["model"] != "M0" and r["diff_vs_M0_bonf"]["hi"] < 0]
    if not independent:
        return "封存测试集此前已被查看，本次结果不是独立检验，不能据此得出结论。"
    if not winners:
        return "未发现可靠预测优势：所有冻结候选在封存测试集上的 log loss 均未在多重比较校正后显著优于均匀基准 M0。"
    names = "、".join(f"{r['model']}" for r in winners)
    return (f"{names} 在封存测试集上 log loss 低于 M0，且 Bonferroni 校正后 HAC 置信区间上限 < 0。"
            "这只是一次样本外检验，仍需前瞻数据复核；不构成投注建议。")


# ---------------- 前瞻记录 ----------------

def predict_next(con, settings: dict) -> list[dict]:
    """基于全部已知数据，为下一期生成冻结候选的预测并记录生成时间（前瞻档案）。"""
    fz = latest_frozen(con)
    if fz is None:
        raise SealError("尚未冻结候选模型")
    df, _, zmap = load_study(con, settings)
    ctx = build_ctx(df, zmap)
    t = ctx.L - 1
    out = []
    for c in [{"model": "M0", "params": {}}] + [c for c in fz["candidates"] if c["model"] != "M0"]:
        m = make_model(c["model"], c["params"])
        m.prepare(ctx)
        p = m.predict(t)
        con.execute("INSERT INTO prospective(created_at, after_draw_id, model, params, probs, code_version, data_hash)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (db.now_iso(), str(ctx.draw_ids[t]), c["model"], json.dumps(c["params"], ensure_ascii=False),
                     json.dumps(np.round(p, 6).tolist()), config.code_version(), db.data_hash(df)))
        out.append({"model": c["model"], "params": c["params"], "probs": p})
    con.commit()
    return out


def score_prospective(con, settings: dict) -> int:
    """为已开奖的前瞻预测打分。只有预测生成时间早于开奖日 13:00 UTC（约香港 21:00）才算有效前瞻。"""
    df, _, _ = load_study(con, settings)
    ids = df["draw_id"].tolist()
    rows = con.execute("SELECT id, created_at, after_draw_id, probs FROM prospective WHERE scored_at IS NULL").fetchall()
    n = 0
    for pid, created, after, probs in rows:
        if after not in ids or ids.index(after) + 1 >= len(ids):
            continue
        nxt = df.iloc[ids.index(after) + 1]
        cutoff = pd.Timestamp(str(nxt["date"].date()) + " 13:00", tz="UTC")
        valid = pd.Timestamp(created) < cutoff
        p = np.array(json.loads(probs))
        con.execute("UPDATE prospective SET target_draw_id=?, actual=?, logloss=?, scored_at=? WHERE id=?",
                    (nxt["draw_id"] + ("" if valid else "（无效：预测晚于开奖）"), int(nxt["special"]),
                     float(-np.log(p[int(nxt["special"]) - 1])), db.now_iso(), pid))
        n += 1
    con.commit()
    return n
