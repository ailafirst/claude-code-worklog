#!/usr/bin/env python3
"""交叉裁判：对一次已有评测里的同一批被测条目，换一个裁判模型重评。

用途：subject=judge 同模型时存在自评偏袒。固定 subject 输出、只换 judge 重评，
可干净地检验『裁判是否过宽』——若换裁判后分数显著松动，说明原评分不稳健。

用法：py rejudge.py <report.json> <judge_model>
"""
import json
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(BENCH.parent / "dist" / "skills" / "journal"))
import run_bench as rb       # noqa: E402
import build_case as bc      # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main():
    if len(sys.argv) < 3:
        sys.exit("用法：py rejudge.py <report.json> <judge_model>")
    report_path, judge_model = sys.argv[1], sys.argv[2]
    base, key = rb.load_creds()
    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))

    rows, out = [], []
    print(f"\n=== 交叉裁判：固定 subject 条目，换 judge={judge_model} 重评 ===\n")
    for r in rep["records"]:
        entry = r.get("entry")
        if not entry:
            continue
        cid  = r["case"]
        case = bc.load_case(cid)
        exp  = (bc.CASES_DIR / cid / "expected-entry.md").read_text(encoding="utf-8")
        old  = (r.get("judge") or {}).get("score")
        old_v = (r.get("judge") or {}).get("verdict")
        try:
            v, _ = rb.run_judge(base, key, judge_model, case, exp, entry, 0.0, 6000)
            new, new_v = v.get("score"), v.get("verdict")
            print(f"  {cid:<22} 原(pro)={old}/{old_v:<4}  新({judge_model})={new}/{new_v}")
            rows.append((cid, old, new, old_v, new_v))
            out.append({"case": cid, "old_score": old, "new_score": new,
                        "old_verdict": old_v, "new_verdict": new_v,
                        "deductions": v.get("deductions", []),
                        "new_rationale": v.get("rationale", "")})
        except Exception as e:
            print(f"  {cid:<22} rejudge 失败: {e}")
            out.append({"case": cid, "error": str(e)})

    olds = [x[1] for x in rows if isinstance(x[1], int)]
    news = [x[2] for x in rows if isinstance(x[2], int)]
    print("\n--- 汇总 ---")
    if olds and news:
        print(f"  原裁判(pro)均分:   {sum(olds)/len(olds):.1f}")
        print(f"  交叉裁判({judge_model})均分: {sum(news)/len(news):.1f}")
        print(f"  逐 case 平均分差:  {sum(o-n for o,n in zip(olds,news))/len(olds):+.1f}")
        flips = [x[0] for x in rows if x[3] == "pass" and x[4] == "fail"]
        if flips:
            print(f"  pass→fail 翻转:    {flips}")
    op = BENCH / ".work" / f"rejudge-{judge_model}.json"
    op.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  明细写入 {op}")


if __name__ == "__main__":
    main()
