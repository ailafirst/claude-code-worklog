#!/usr/bin/env python3
"""跨月 / 跨年边界压力测试 —— 只测确定性层，不测模型写得好不好。

judgment 层的质量由 run_bench.py 的 13 个 case 覆盖；这里测的是 journal.py 自己的
日期算术在"攒了一两个月日记"之后还对不对，具体是两件事：

  1. threads 聚合：逐 thread 的出现次数 / 首次 / 最近日期，与独立算出的期望值一致，
     STALE 判定在大规模语料下仍然正确。
  2. rollup --week/--year：只收录该 ISO 周范围内的日文件，不多不少——专门覆盖
     ISO 第 1 周横跨上一年 12 月的边界（如 2026 年 W1 = 2025-12-29 ~ 2026-01-04，
     用 date.fromisocalendar 独立算出，不复用 journal.py 自己的周边界实现）。

数据不是现造的占位正文：复用 benchmark/cases/ 全部 case 的金标准条目正文
（expected-entry.md）+ 真实重放出的 commit head sha（materialize 出的 git 仓），
循环铺满一段连续跨度，只有"日期"是为了制造跨度人为指定的——避免用"测试条目 09:00"
这种空壳内容掩盖真实场景该有的复杂度（多线程共存、正文含真实决策措辞等）。

hermetic：JOURNAL_ROOT 指临时目录；会顺带用 build_case 物化全部 case 的 git 仓到
benchmark/.work/（可重建，已 gitignore），不碰真实 ~/.claude/journal。

用法：
  py stress_multiday.py       全跑
  py stress_multiday.py -v    详细输出（含每个 thread 的期望值 vs 实得值）
"""
import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

BENCH     = Path(__file__).resolve().parent
REPO_ROOT = BENCH.parent
SKILL_DIR = REPO_ROOT / "dist" / "skills" / "journal"

sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(SKILL_DIR))
import build_case as bc   # noqa: E402
import journal as j       # noqa: E402

_verbose = "-v" in sys.argv
_results = []


def check(ok, label, detail=""):
    _results.append((ok, label, detail))
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {label}"
    if _verbose or not ok:
        if detail:
            line += f"\n         {detail}"
    print(line)


# ════════════════════════════════════════════════════════════════════════════
# 语料来源：全部 case 的金标准条目正文 + 真实重放出的 head sha
# ════════════════════════════════════════════════════════════════════════════

def load_real_entries():
    entries = []
    for cid in bc.list_case_ids():
        case = bc.load_case(cid)
        md   = (bc.CASES_DIR / cid / "expected-entry.md").read_text(encoding="utf-8")
        m    = re.search(r"```markdown\n(.*?)\n```", md, re.S)
        if not m:
            continue
        blk = j.parse_single_block(m.group(1))

        _, repo, jroot, _ = bc.materialize(case)
        rc, out, _ = bc.run_collect(repo, jroot)
        head = json.loads(out).get("head") if rc == 0 else None
        if not head:
            head = hashlib.sha1(cid.encode()).hexdigest()   # 04-non-git：无 git，补一个假 sha

        entries.append({
            "id":      cid,
            "project": blk.fields.get("project") or "proj",
            "threads": list(blk.fields.get("threads") or []),
            "body":    blk.body,
            "head":    head,
        })
    return entries


# ════════════════════════════════════════════════════════════════════════════
# 语料铺设：循环全部 case，连续 num_days 天一天一条
# ════════════════════════════════════════════════════════════════════════════

def build_corpus(entries, start, num_days):
    days = [start + timedelta(days=i) for i in range(num_days)]
    picks = [entries[i % len(entries)] for i in range(num_days)]

    expected = {}   # thread -> {count, first(str), last(str)}
    for d, e in zip(days, picks):
        ds = d.strftime("%Y-%m-%d")
        for t in e["threads"]:
            rec = expected.setdefault(t, {"count": 0, "first": ds, "last": ds})
            rec["count"] += 1
            rec["first"]  = min(rec["first"], ds)
            rec["last"]   = max(rec["last"],  ds)

    for d, e in zip(days, picks):
        ds = d.strftime("%Y-%m-%d")
        fields = {"date": ds, "project": e["project"],
                  "threads": e["threads"], "head": e["head"]}
        raw = f"## 09:00 · session\n\n{j.dump_frontmatter(fields)}\n\n{e['body']}\n"
        j.do_append(raw, cli_date=ds)

    return days, expected


def parse_threads_table(out):
    """把 cmd_threads 打印的对齐表解析回 {thread: (count, first, last, stale)}。"""
    rows = {}
    lines = out.splitlines()
    for line in lines[1:]:   # 跳过表头
        if not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        name, count, first, last, days_ago = parts[:5]
        stale = parts[5] if len(parts) > 5 else ""
        rows[name] = (int(count), first, last, stale == "STALE")
    return rows


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["JOURNAL_ROOT"] = tmp

        entries = load_real_entries()
        expected_case_count = len(bc.list_case_ids())
        check(len(entries) == expected_case_count, "全部 case 的金标准条目均提取成功",
              f"实得 {len(entries)} 条，期望 {expected_case_count}")

        # 跨度：2025-11-20 ~ 2026-01-03（45 天），横跨 11/12 月末、12/1 月末，
        # 且盖住 ISO 2026 年 W1（2025-12-29~2026-01-04）这条横跨上一年 12 月的边界。
        start, num_days = date(2025, 11, 20), 45
        days, expected = build_corpus(entries, start, num_days)

        # 额外加一条"今天"的新鲜 thread，用来验证大语料下 STALE/排序不受影响。
        j.do_append(
            f"## 08:00 · session\n\n"
            f"{j.dump_frontmatter({'date': date.today().strftime('%Y-%m-%d'), 'project': 'proj', 'threads': ['fresh-check'], 'head': 'f'*40})}\n\n"
            f"### 做成了什么\n- 今天的占位条目，只为测新鲜度\n",
            cli_date=date.today().strftime("%Y-%m-%d"),
        )

        # 干扰项：一份 week-NN.md（roll-up 产物），thread 聚合必须无视它。
        decoy = Path(tmp) / "2025" / "week-99.md"
        decoy.parent.mkdir(parents=True, exist_ok=True)
        decoy.write_text(
            "## 10:00 · session\n---\ndate: 2025-01-01\nproject: p\n"
            "threads: [\"should-not-appear\"]\n---\n", encoding="utf-8")

        day_files = [p for p in Path(tmp).rglob("*.md") if j.DAY_RE.match(p.name)]
        check(len(day_files) == num_days + 1, "日文件总数 = 45 天语料 + 1 条今天",
              f"实得 {len(day_files)}")

        # ── 1. threads 聚合：45+1 条、多种 thread 规模下，统计仍然精确 ──────────
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            j.cmd_threads(SimpleNamespace(stale_days=7))
        table = parse_threads_table(buf.getvalue())

        check("should-not-appear" not in table,
              "week-NN.md 干扰项不进入 thread 聚合（大语料下依然生效）")

        all_hit = True
        for name, exp in expected.items():
            got = table.get(name)
            ok  = got is not None and got[0] == exp["count"] and \
                  got[1] == exp["first"] and got[2] == exp["last"]
            all_hit = all_hit and ok
            check(ok, f"thread `{name}` 统计精确（count/first/last）",
                  f"期望 {exp}，实得 {got}")
        check(all_hit, "全部 thread 统计一次性核对通过（汇总）")

        fresh = table.get("fresh-check")
        check(fresh is not None and not fresh[3],
              "今天新加的 thread 不被标 STALE", f"实得 {fresh}")
        stale_names = [n for n in expected if n != "fresh-check"]
        all_stale = all(table.get(n, (0, "", "", False))[3] for n in stale_names)
        check(all_stale, "45 天前的历史 thread 全部被标 STALE（相对真实今天）")

        # ── 2. rollup --week/--year：ISO 周边界（尤其跨年那周）只收对应文件 ──────
        def check_week(year, week, label):
            iso_start = date.fromisocalendar(year, week, 1)   # 独立算法，不复用 journal.py 自己的实现
            iso_end   = iso_start + timedelta(days=6)
            exp_days  = [d for d in days if iso_start <= d <= iso_end]

            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                j.cmd_rollup(SimpleNamespace(save=False, week=week, year=year))
            out = buf2.getvalue()

            got_headers = set(re.findall(r"^# (\d{4}-\d{2}-\d{2})$", out, re.M))
            exp_headers = {d.strftime("%Y-%m-%d") for d in exp_days}
            check(got_headers == exp_headers,
                  f"{label}：ISO {year}-W{week}（{iso_start}~{iso_end}）只收录该周日文件",
                  f"期望 {sorted(exp_headers)}，实得 {sorted(got_headers)}")

        check_week(2026, 1, "跨年边界周")     # 2025-12-29 ~ 2026-01-04
        check_week(2025, 51, "普通周（对照组，不跨月不跨年）")

    total  = len(_results)
    passed = sum(1 for ok, *_ in _results if ok)
    print(f"\n{'═'*60}")
    print(f"结果: {passed}/{total} 通过", end="")
    if passed != total:
        print(f"  ({total - passed} 失败)")
        for ok, label, detail in _results:
            if not ok:
                print(f"  ✗ {label}")
                if detail:
                    print(f"      {detail}")
    else:
        print("  全绿 ✅")
    print(f"{'═'*60}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
