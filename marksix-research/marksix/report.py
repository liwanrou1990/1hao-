"""报告生成：数据质量报告、形态规则表、研究报告（Markdown）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config, db
from .analysis import pattern_verdict
from .backtest import final_test_status, get_or_create_seal, latest_frozen, load_study
from .patterns import REGISTERED_AT, registry_hash, rules_table
from .validate import quality_report
from .zodiac import ZodiacMap


def _md(df: pd.DataFrame, floatfmt: int = 4) -> str:
    if df is None or df.empty:
        return "（无）\n"
    d = df.copy()
    for c in d.columns:
        if pd.api.types.is_float_dtype(d[c]):
            d[c] = d[c].map(lambda x: "" if pd.isna(x) else f"{x:.{floatfmt}g}")
    cols = [str(c) for c in d.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in d.astype(str).itertuples(index=False):
        lines.append("| " + " | ".join(x.replace("|", "／").replace("\n", " ") for x in r) + " |")
    return "\n".join(lines) + "\n"


def out_dir(kind: str) -> Path:
    p = config.REPORTS_DIR / kind
    p.mkdir(parents=True, exist_ok=True)
    return p


def data_quality_md(con, settings) -> str:
    kind = db.dataset_kind(con)
    df = db.current_draws(con)
    zmap = ZodiacMap.from_db(con)
    rep = quality_report(con, df, settings, zmap)
    L = [f"# 数据质量报告（{config.KIND_LABEL[kind]}）\n", f"生成时间：{db.now_iso()}  数据哈希：{db.data_hash(df)}\n"]
    if df.empty:
        L.append("\n**尚无数据。** 真实数据需通过 CSV 导入（见 README）。\n")
        return "\n".join(L)
    r = rep["regime"]
    L += [f"- 状态：**{rep['status']}**", f"- 当前有效期数：{rep['n_current']}", f"- 日期范围：{rep['date_range'][0]} 至 {rep['date_range'][1]}",
          f"- 来源：{rep['sources']}", f"- 原始快照数：{rep['n_snapshots']}；更正记录：{rep['n_corrections']}",
          f"- 重复有效版本：{rep['duplicate_current_versions'] or '无'}", f"- 同日多期：{rep['duplicate_dates'] or '无'}",
          f"- 号码越界：{rep['out_of_range'] or '无'}；七球重复：{rep['seven_ball_duplicates'] or '无'}",
          f"- 时间顺序异常：{rep['order_violations'] or '无'}",
          f"- 缺失期号：{rep['n_missing_draw_ids']} 个", f"- 间隔超过 14 天：{len(rep['long_gaps'])} 处",
          "", "## 49 号码制度边界", f"- 采用起点：{r.get('start')}（依据：{r.get('basis')}）",
          f"- 是否已核验：{'是' if r.get('verified') else '否——需用官方资料核对'}",
          f"- 数据中首次出现 ≥46 号：{r.get('first_ball_ge46')}；首次出现 ≥48 号：{r.get('first_ball_ge48')}",
          f"- 起点前被排除的期数：{r.get('n_excluded_before')}",
          "", "## 生肖映射", f"- 版本：{rep['zodiac_mapping_version']}",
          f"- 无映射的期数：{len(rep['draws_without_mapping'])}（这些期不参与生肖分析）"]
    if rep["missing_draw_ids"]:
        L += ["", "## 缺失期号明细"] + [f"- {g['year']}：{', '.join(g['missing'][:50])}" for g in rep["missing_draw_ids"]]
    if rep["long_gaps"]:
        L += ["", "## 长间隔"] + [f"- {g['after']} → {g['before']}：{g['days']} 天" for g in rep["long_gaps"][:50]]
    if rep["corrections"]:
        L += ["", "## 更正记录", _md(pd.DataFrame(rep["corrections"]))]
    L += ["", "## 导入日志（最近）", _md(pd.DataFrame(rep["import_log"]))]
    return "\n".join(L)


def rules_md() -> str:
    t = rules_table()
    return (f"# 固定形态规则表\n\n登记日期：{REGISTERED_AT}；登记哈希：`{registry_hash()}`\n\n"
            "所有规则在查看真实数据结果之前登记，定义修改会改变登记哈希。预测对象固定为下一期特别号码（或其生肖），"
            "观察期限固定 1 期。重叠规则（如重号与同尾、J02 与 Z-ABA-cont）是独立候选，不得把它们当作独立证据相乘。\n\n"
            + _md(t))


def research_md(con, settings, pattern_result: dict | None = None) -> str:
    kind = db.dataset_kind(con)
    df, regime, zmap = load_study(con, settings)
    split = get_or_create_seal(con, df, settings, create=False)
    L = [f"# 研究报告：香港六合彩特别号码历史形态（{config.KIND_LABEL[kind]}）\n",
         f"生成时间：{db.now_iso()}；代码版本：{config.code_version()}；配置哈希：{config.settings_hash(settings)}",
         f"形态登记哈希：{registry_hash()}\n"]
    if kind == "synthetic":
        L.append("> **本报告基于合成演示数据，只用于验证流程，结论与真实六合彩无关。**\n")
    L += ["## 一、实现与验证状态", "| 项目 | 状态 |", "|---|---|"]
    has_data = not df.empty
    L += [f"| 数据层（导入、快照、哈希、更正版本、校验） | 已实现，已测试 |",
          f"| 真实数据 | {'已导入 %d 期' % len(df) if (has_data and kind == 'real') else ('尚缺数据' if kind == 'real' else '不适用（合成库）')} |",
          f"| 49 号码制度边界 | {'已配置并注明来源' if regime.get('verified') else '未经官方核验（数据推断或未配置）'} |",
          f"| 生肖映射 | 已实现（民间通行口径，非官方，待与可信来源核对）；版本 {zmap.version if zmap else '缺失'} |",
          "| 固定形态统计、多重比较、maxT 模拟 | 已实现，已测试 |",
          "| 模型 M0/M1/M2/M2b/M3 与滚动回测 | 已实现，已测试 |"]
    fts = final_test_status(con)
    L.append(f"| 封存测试 | {'已运行 %d 次（首次为独立检验）' % fts['n_runs'] if fts['n_runs'] else '未运行'} |")
    n_pro = con.execute("SELECT COUNT(*) FROM prospective WHERE scored_at IS NOT NULL").fetchone()[0]
    L.append(f"| 前瞻验证 | {'已评分 %d 条' % n_pro if n_pro else '尚无前瞻结果'} |\n")
    if not has_data:
        L.append("**尚无数据，无法给出任何研究结论。**")
        return "\n".join(L)
    L += ["## 二、样本", f"- 研究样本（49 号码制度期间）：{len(df)} 期，{df['date'].min().date()} 至 {df['date'].max().date()}",
          f"- 制度起点：{regime.get('start')}（{regime.get('basis')}）"]
    if split:
        L += [f"- 封存：开发段 {split.n_dev} 期（至 {split.last_dev_draw}），封存测试段 {split.n_test} 期（自 {split.first_test_draw}），"
              f"封存后新增 {split.n_post} 期（前瞻期）；封存时间 {split.created_at}；测试段哈希 {'一致' if split.test_hash_ok else '**不一致（有更正）**'}"]
    L.append("")
    if pattern_result is not None:
        tab = pattern_result["table"]
        L += ["## 三、开发期形态检验（只用开发段）", pattern_verdict(tab), "",
              "效应为“实际率 − 基准率”（百分点）；p 值为泊松二项精确双侧检验；Holm/BH 为解析校正；maxT 为重跑全部登记规则的蒙特卡洛族错误率校正。\n",
              _md(tab.drop(columns=[c for c in ["类别"] if c in tab]))]
    dev = pd.read_sql_query("SELECT exp_id, model, params, metrics FROM experiments WHERE phase='dev' AND status='ok'", con)
    if not dev.empty:
        recs = []
        for r in dev.itertuples():
            m = json.loads(r.metrics)
            recs.append({"模型": r.model, "参数": r.params, "logloss": m["logloss"], "差值vsM0": m["diff_vs_M0"]["mean"],
                         "差值95%下限": m["diff_vs_M0"]["lo"], "差值95%上限": m["diff_vs_M0"]["hi"], "Top10": m["top10"]})
        d = pd.DataFrame(recs).sort_values("logloss")
        L += ["## 四、开发期模型比较（滚动预测，开发段内）",
              f"共尝试 {len(d)} 个配置（含失败另记）。在同一开发段上挑选最优配置存在选择偏差，开发期结果不能作为结论。"
              f"均匀基准 log loss = ln49 ≈ 3.8918。\n", _md(d.head(15), 6), ""]
    fz = latest_frozen(con)
    if fz:
        L += ["## 五、冻结候选", _md(pd.DataFrame(fz["candidates"])), f"各模型族尝试配置数：{fz['n_configs_tried']}\n"]
    ft = pd.read_sql_query("SELECT exp_id, model, params, metrics, notes FROM experiments WHERE phase='final_test' AND status='ok'"
                           " ORDER BY exp_id", con)
    if not ft.empty:
        recs = []
        for r in ft.itertuples():
            m = json.loads(r.metrics)
            recs.append({"实验": r.exp_id, "模型": r.model, "独立": "是" if m.get("independent") else "否",
                         "logloss": m["logloss"], "差值vsM0": m["diff_vs_M0"]["mean"],
                         "Bonf区间下限": m["diff_vs_M0_bonf"]["lo"], "Bonf区间上限": m["diff_vs_M0_bonf"]["hi"],
                         "分块自助区间": "[%.5f, %.5f]" % tuple(m.get("diff_block_boot") or (float("nan"),) * 2), "Brier": m["brier"], "Top1": m["top1"], "Top5": m["top5"],
                         "Top10": m["top10"], "生肖logloss": m.get("zodiac_logloss"), "生肖基准": m.get("zodiac_logloss_base"),
                         "ECE": m["ece"]})
        ftd = pd.DataFrame(recs)
        from .backtest import conclusion

        first = [dict(model=r["模型"], diff_vs_M0_bonf={"hi": r["Bonf区间上限"]}) for r in recs if r["独立"] == "是"]
        L += ["## 六、封存测试集结果", _md(ftd, 6), "", "**结论：** " + conclusion(first, bool(first)) if first else
              "**结论：** 没有独立的封存测试运行。"]
        runs = pd.read_sql_query("SELECT created_at, independent, note FROM final_test_runs ORDER BY id LIMIT 1", con)
        if not runs.empty:
            note = json.loads(runs["note"].iloc[0])
            pt = pd.DataFrame(note["patterns"])
            keep = [c for c in ["规则编号", "名称", "触发次数n", "实际次数O", "随机期望E", "效应(百分点)", "效应95%下限",
                                "效应95%上限", "p值", "Holm校正p", "状态"] if c in pt]
            sig = pt[(pt.get("Holm校正p", pd.Series(dtype=float)) < 0.05) & (pt["状态"] == "可计算")] if "Holm校正p" in pt else pt.iloc[0:0]
            L += ["", "### 封存测试段形态检验（首次、独立运行）",
                  ("封存测试段中没有登记形态在 Holm 校正后显著偏离随机基准。" if sig.empty else
                   "封存测试段中 Holm 校正后 p<0.05 的登记形态：" + "、".join(sig["规则编号"]) + "（仍需前瞻复核）。"),
                  "", _md(pt[keep])]
    else:
        L += ["## 六、封存测试集结果", "尚未运行。冻结候选后才能运行一次。"]
    L += ["", "## 七、局限", "- 历史数据的实际可得时间与历史更正情况未知，回测不等于真实前瞻验证。",
          "- 生肖映射为民间通行口径，未经官方确认；49 号码制度起点如未配置来源，则为数据推断。",
          "- 开奖结果在设计上是独立随机的；本系统允许并预期结论为“未发现可靠预测优势”。",
          "- 本系统不提供投注建议，不接入任何投注或资金操作。"]
    return "\n".join(L)


def write_all(con, settings, pattern_result=None) -> dict:
    kind = db.dataset_kind(con)
    d = out_dir(kind)
    files = {"data_quality": d / "数据质量报告.md", "rules": d / "形态规则表.md", "rules_csv": d / "形态规则表.csv",
             "research": d / "研究报告.md"}
    files["data_quality"].write_text(data_quality_md(con, settings), encoding="utf-8")
    files["rules"].write_text(rules_md(), encoding="utf-8")
    rules_table().to_csv(files["rules_csv"], index=False, encoding="utf-8-sig")
    files["research"].write_text(research_md(con, settings, pattern_result), encoding="utf-8")
    if pattern_result is not None:
        files["patterns_csv"] = d / "开发期形态检验.csv"
        pattern_result["table"].to_csv(files["patterns_csv"], index=False, encoding="utf-8-sig")
    return files
