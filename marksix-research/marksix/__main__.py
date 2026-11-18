"""命令行入口：python -m marksix <命令>。"""
from __future__ import annotations

import argparse
import json
import sys

from . import config, db, ingest, synthetic, zodiac
from .analysis import pattern_verdict, patterns_dev
from .backtest import freeze, predict_next, run_dev, run_final_test, score_prospective
from .report import write_all
from .validate import quality_report


def _con(args):
    return db.connect(args.kind)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="marksix", description="香港六合彩特别号码历史形态研究系统")
    ap.add_argument("--kind", choices=config.DATASET_KINDS, default="real", help="数据集：real 真实 / synthetic 合成")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="初始化数据库并安装内置生肖映射")
    p = sub.add_parser("import-csv", help="导入 CSV（真实数据必须注明来源）")
    p.add_argument("path")
    p.add_argument("--source", help="来源（CSV 无来源列时必填），如：HKJC官方结果页 https://bet.hkjc.com/ch/marksix/results")
    p.add_argument("--fetched-at", help="采集时间")
    p.add_argument("--correction-reason", help="若此文件用于更正已有记录，填写更正原因；否则不同内容会被拒绝")
    p = sub.add_parser("make-synthetic", help="生成合成演示数据（只写入合成库）")
    p.add_argument("--n", type=int, default=1500)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--plant-repeat", type=float, default=None, help="植入重号信号的概率（功效检查用）")
    sub.add_parser("validate", help="数据质量检查")
    p = sub.add_parser("patterns", help="开发期固定形态检验（只用开发段）")
    p.add_argument("--sims", type=int, default=None)
    sub.add_parser("dev", help="开发期滚动回测（全部配置）")
    sub.add_parser("freeze", help="冻结每个模型族的最优开发期配置")
    p = sub.add_parser("final-test", help="一次性封存测试（需 --confirm）")
    p.add_argument("--confirm", action="store_true")
    sub.add_parser("predict-next", help="生成下一期前瞻预测并记录时间")
    sub.add_parser("score-prospective", help="为已开奖的前瞻预测评分")
    p = sub.add_parser("report", help="生成数据质量报告、规则表、研究报告")
    p.add_argument("--sims", type=int, default=None)
    p = sub.add_parser("pipeline", help="合成数据全流程：生成→形态→开发回测→冻结→封存测试→报告")
    p.add_argument("--sims", type=int, default=200)
    args = ap.parse_args(argv)
    settings = config.load_settings()
    con = _con(args)
    pr = lambda *a, **k: print(*a, **k, flush=True)

    if args.cmd == "init":
        pr(f"已安装映射行数：{zodiac.install_builtin(con)}；数据库：{config.db_path(args.kind)}")
    elif args.cmd == "import-csv":
        zodiac.install_builtin(con)
        r = ingest.import_csv(con, args.path, source=args.source, correction_reason=args.correction_reason,
                              fetched_at=args.fetched_at)
        pr(json.dumps(r.summary(), ensure_ascii=False))
        for c in r.conflicts[:20]:
            pr("冲突:", c)
        for c in r.invalid[:20]:
            pr("无效:", c)
    elif args.cmd == "make-synthetic":
        if args.kind != "synthetic":
            sys.exit("合成数据只能写入合成库：请加 --kind synthetic")
        zodiac.install_builtin(con)
        df = synthetic.make_draws(args.n, seed=args.seed, plant_repeat_prob=args.plant_repeat)
        r = ingest.import_bytes(con, synthetic.to_csv_bytes(df), source=None, filename=f"synthetic_seed{args.seed}.csv")
        pr(json.dumps(r.summary(), ensure_ascii=False))
    elif args.cmd == "validate":
        rep = quality_report(con, db.current_draws(con), settings, zodiac.ZodiacMap.from_db(con))
        pr(json.dumps({k: v for k, v in rep.items() if k not in ("import_log", "draws_without_mapping")},
                      ensure_ascii=False, indent=1, default=str)[:6000])
    elif args.cmd == "patterns":
        res = patterns_dev(con, settings, n_sims=args.sims)
        pr(res["table"][["规则编号", "名称", "触发次数n", "实际次数O", "随机期望E", "效应(百分点)", "p值", "maxT校正p", "状态"]]
           .to_string() if "maxT校正p" in res["table"] else res["table"].to_string())
        pr(pattern_verdict(res["table"]))
    elif args.cmd == "dev":
        res = run_dev(con, settings, progress=lambda f, m: pr(f"[{f:5.1%}] {m}"))
        best = sorted(res["results"], key=lambda r: r["logloss"])[:10]
        for r in best:
            pr(r["model"], r["params"], f"logloss={r['logloss']:.5f}", f"diff={r['diff_vs_M0']['mean']:+.5f}")
    elif args.cmd == "freeze":
        pr(json.dumps(freeze(con, settings), ensure_ascii=False, indent=1))
    elif args.cmd == "final-test":
        res = run_final_test(con, settings, confirm=args.confirm)
        for r in res["results"]:
            d = r["diff_vs_M0_bonf"]
            pr(r["model"], r["params"], f"logloss={r['logloss']:.5f}", f"diff={d['mean']:+.5f} [{d['lo']:+.5f},{d['hi']:+.5f}]")
        pr("独立检验" if res["independent"] else "非独立（测试集已被查看）")
        pr("结论：", res["conclusion"])
    elif args.cmd == "predict-next":
        for r in predict_next(con, settings):
            top = r["probs"].argsort()[::-1][:5] + 1
            pr(r["model"], "最高概率号码", top.tolist(), "概率", r["probs"].max().round(5), "随机基准", round(1 / 49, 5))
    elif args.cmd == "score-prospective":
        pr("已评分：", score_prospective(con, settings))
    elif args.cmd == "report":
        res = None
        try:
            res = patterns_dev(con, settings, n_sims=args.sims, record=False)
        except Exception as e:
            pr("形态检验未运行：", e)
        for k, v in write_all(con, settings, res).items():
            pr(k, v)
    elif args.cmd == "pipeline":
        if args.kind != "synthetic":
            sys.exit("pipeline 只用于合成数据：请加 --kind synthetic")
        zodiac.install_builtin(con)
        if con.execute("SELECT COUNT(*) FROM draws").fetchone()[0] == 0:
            df = synthetic.make_draws(1500, seed=0)
            ingest.import_bytes(con, synthetic.to_csv_bytes(df), source=None, filename="synthetic_seed0.csv")
        res = patterns_dev(con, settings, n_sims=args.sims)
        pr(pattern_verdict(res["table"]))
        run_dev(con, settings, progress=lambda f, m: None)
        freeze(con, settings)
        ft = run_final_test(con, settings, confirm=True)
        pr("封存测试结论：", ft["conclusion"])
        for k, v in write_all(con, settings, res).items():
            pr(k, v)


if __name__ == "__main__":
    main()
