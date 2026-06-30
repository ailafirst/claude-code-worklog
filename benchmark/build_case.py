#!/usr/bin/env python3
"""benchmark 夹具构建器 —— 把一个 case 落成真实 git 仓，并跑真正的 journal collect。

为什么要真 git 仓而不是预存 collect.json：
  journal 的一半价值在确定性层（git delta / --since 回退 / uncommitted 统计）。
  只有把 case 的 commit 序列真的重放进 `git`，collect 输出才是"真的"，
  才能用来验证 skill 在真实仓库下的表现，而非纸面假设。

每个 case = cases/<id>/case.json（元数据 + git 任务）+ context.md（推理 ground truth）
            + expected-entry.md（金标准日记 + 评分要点）。

用法：
  py build_case.py <id> --collect          构建单个 case 并打印 collect JSON
  py build_case.py <id>                     只构建夹具到 .work/<id>/
  py build_case.py --all                    构建全部并核对 case.expect（自检确定性层）

夹具落在 benchmark/.work/<id>/{repo, journal}/，可反复重建，不污染用户环境。
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

BENCH      = Path(__file__).resolve().parent
REPO_ROOT  = BENCH.parent
JOURNAL_PY = REPO_ROOT / "dist" / "skills" / "journal" / "journal.py"
CASES_DIR  = BENCH / "cases"
WORK       = BENCH / ".work"


# ── 时间：用相对天偏移保证可复现（day=0 即"今天"，--since=midnight 才抓得到）──
def _abs_dt(day_offset, hhmm):
    d = date.today() + timedelta(days=day_offset)
    hh, mm = (hhmm or "12:00").split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm))


def _git(repo, *args, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run(["git", *args], cwd=str(repo), env=e,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{p.stderr.strip()}")
    return p.stdout


def _write_file(repo, rel, content):
    fp = repo / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    text = content if content.endswith("\n") else content + "\n"
    with open(fp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _apply(repo, spec):
    for rel, content in (spec.get("files") or {}).items():
        _write_file(repo, rel, content)
    for rel in (spec.get("delete") or []):
        fp = repo / rel
        if fp.exists():
            fp.unlink()


def _commit(repo, spec, shas, key):
    _apply(repo, spec)
    _git(repo, "add", "-A")
    dt  = _abs_dt(spec.get("day", 0), spec.get("time", "12:00"))
    iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
    env = {"GIT_AUTHOR_DATE": iso, "GIT_COMMITTER_DATE": iso}
    _git(repo, "commit", "-q", "-m", spec.get("message", "commit"), env=env)
    shas[key] = _git(repo, "rev-parse", "HEAD").strip()


def _write_prior_journal(jroot, case, sha):
    """case 09 类：预置一份"今天"的日文件，其末块 head 落在某提交。

    这样 collect 的 last_head_today() 会回填出 since，走 since..HEAD 的续接路径。
    """
    today    = date.today()
    day_file = jroot / f"{today:%Y}" / f"{today:%m}" / f"{today:%Y-%m-%d}.md"
    day_file.parent.mkdir(parents=True, exist_ok=True)
    threads  = json.dumps(case.get("expected_threads", []), ensure_ascii=False)
    block = (
        f"# {today:%Y-%m-%d}\n\n"
        f"## 09:00 · session\n\n"
        f"---\ndate: {today:%Y-%m-%d}\nproject: {case.get('project','')}\n"
        f"threads: {threads}\nhead: {sha}\n---\n\n"
        f"**做成了什么**\n- 上一次 session（基准夹具预置），head 落在此提交\n"
    )
    with open(day_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(block)


def _rm_readonly(func, path, _exc):
    """Windows：git 把 object 文件设只读，rmtree 删不掉 → 先清只读位再删。"""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def materialize(case):
    """把 case 落成 .work/<id>/{repo,journal}/，返回 (workdir, repo, jroot, shas)。"""
    wd = WORK / case["id"]
    if wd.exists():
        shutil.rmtree(wd, onerror=_rm_readonly)
    repo  = wd / "repo"
    jroot = wd / "journal"
    repo.mkdir(parents=True)
    jroot.mkdir(parents=True)

    shas = {}
    if case.get("git", "repo") == "repo":
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "bench@local")
        _git(repo, "config", "user.name", "bench")
        _git(repo, "config", "commit.gpgsign", "false")
        _git(repo, "config", "core.autocrlf", "false")
        if case.get("init"):
            _commit(repo, case["init"], shas, "init")
        for i, c in enumerate(case.get("commits", [])):
            _commit(repo, c, shas, i)
        # 显式定分支名：宿主 git 默认分支可能是 master，夹具需可复现
        if shas:
            _git(repo, "branch", "-M", case.get("branch") or "main")
        if case.get("uncommitted"):
            _apply(repo, case["uncommitted"])
    else:
        # 非 git 目录：按顺序铺文件，不建 .git —— 验证 collect 的空结构降级
        for spec in ([case.get("init")] + case.get("commits", [])
                     + [case.get("uncommitted")]):
            if spec:
                _apply(repo, spec)

    notes = case.get("session_notes") or []
    if notes:
        sp = jroot / ".session-scratch"
        with open(sp, "w", encoding="utf-8", newline="\n") as f:
            for n in notes:
                f.write(f"- [09:00] {n}\n")

    phf = case.get("prior_head_from")
    if phf is not None and case.get("git", "repo") == "repo":
        sha = shas.get("init") if phf == "init" else shas.get(int(phf))
        if sha:
            _write_prior_journal(jroot, case, sha)

    return wd, repo, jroot, shas


def run_collect(repo, jroot, fresh=False):
    env = dict(os.environ)
    env["JOURNAL_ROOT"] = str(jroot)
    args = [sys.executable, str(JOURNAL_PY), "collect"]
    if fresh:
        args.append("--fresh")
    p = subprocess.run(args, cwd=str(repo), env=env,
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def check_expect(case, data):
    """核对 case.expect 声明的确定性层不变量；返回问题列表（空=通过）。"""
    e = case.get("expect", {})
    problems = []

    def cmp(name, actual, want):
        if actual != want:
            problems.append(f"{name}: 实得 {actual!r} ≠ 期望 {want!r}")

    if "commit_count" in e:
        cmp("commit_count", len(data.get("commits", [])), e["commit_count"])
    if "branch" in e:
        cmp("branch", data.get("branch"), e["branch"])
    if "has_session_notes" in e:
        cmp("has_session_notes", bool(data.get("session_notes")),
            e["has_session_notes"])
    if "since_used" in e:
        cmp("since_used", data.get("since") is not None, e["since_used"])
    if "files_changed_min" in e:
        n = len(data.get("files_changed", []))
        if n < e["files_changed_min"]:
            problems.append(f"files_changed: {n} < 期望≥{e['files_changed_min']}")
    if "uncommitted_min" in e:
        n = len(data.get("uncommitted", {}).get("files", []))
        if n < e["uncommitted_min"]:
            problems.append(f"uncommitted: {n} < 期望≥{e['uncommitted_min']}")
    return problems


def load_case(case_id):
    cj = CASES_DIR / case_id / "case.json"
    if not cj.exists():
        sys.exit(f"找不到 case：{cj}")
    case = json.loads(cj.read_text(encoding="utf-8"))
    case["id"] = case_id  # 以文件夹名为准
    return case


def list_case_ids():
    if not CASES_DIR.exists():
        return []
    return sorted(p.name for p in CASES_DIR.iterdir()
                  if (p / "case.json").exists())


def cmd_single(case_id, do_collect):
    case = load_case(case_id)
    wd, repo, jroot, shas = materialize(case)
    print(f"[built] {case_id} → {wd}")
    if not do_collect:
        return 0
    rc, out, err = run_collect(repo, jroot)
    (wd / "collect.json").write_text(out, encoding="utf-8")
    if err.strip():
        print(err.strip(), file=sys.stderr)
    sys.stdout.write(out)
    return rc


def cmd_all():
    ids = list_case_ids()
    if not ids:
        sys.exit("cases/ 下没有任何 case")
    print(f"\n=== 构建并核对 {len(ids)} 个 case ===\n")
    failed = 0
    for cid in ids:
        case = load_case(cid)
        try:
            wd, repo, jroot, shas = materialize(case)
            rc, out, err = run_collect(repo, jroot)
            if rc != 0:
                print(f"  [FAIL] {cid:<22} collect 退出码 {rc}: {err.strip()}")
                failed += 1
                continue
            data = json.loads(out)
            (wd / "collect.json").write_text(out, encoding="utf-8")
            problems = check_expect(case, data)
            if problems:
                print(f"  [FAIL] {cid:<22} " + "; ".join(problems))
                failed += 1
            else:
                tag = case.get("title", "")
                print(f"  [OK]   {cid:<22} {tag}")
        except Exception as ex:
            print(f"  [FAIL] {cid:<22} 构建异常: {ex}")
            failed += 1
    print(f"\n结果：{len(ids) - failed}/{len(ids)} 通过")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description="journal benchmark 夹具构建器")
    ap.add_argument("case", nargs="?", help="case id（cases/ 下的文件夹名）")
    ap.add_argument("--collect", action="store_true", help="构建后跑 collect 并打印")
    ap.add_argument("--all", action="store_true", help="构建全部并核对 expect")
    args = ap.parse_args()

    if not JOURNAL_PY.exists():
        sys.exit(f"找不到 journal.py：{JOURNAL_PY}")

    if args.all:
        sys.exit(cmd_all())
    if not args.case:
        ap.error("需指定 case id，或用 --all")
    sys.exit(cmd_single(args.case, args.collect))


if __name__ == "__main__":
    main()
