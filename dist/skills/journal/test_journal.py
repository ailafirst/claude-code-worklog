#!/usr/bin/env python3
"""
journal skill 完整测试套件 —— 覆盖 spec §10 验收清单 + 边界情况。

用法：
    py test_journal.py               # 全跑
    py test_journal.py collect       # 只跑匹配 "collect" 的组
    py test_journal.py -v            # 详细输出（每条测试的断言值）

hermetic：全程用 JOURNAL_ROOT 临时目录，不碰真实 ~/.claude/journal。
"""
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# ── 全链路 UTF-8（Windows cp936 保护）──────────────────────────────────────
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

SKILL_DIR = Path(__file__).resolve().parent  # 絶対パス化 — cwd= 付きで subprocess を呼ぶと相対パスが迷子になる
SCRIPT = SKILL_DIR / "journal.py"

# 本机 python 解释器（Windows 上 python3 常是 WindowsApps 死 stub）
PYTHON = sys.executable

# ── 测试框架（极简，无额外依赖）──────────────────────────────────────────
_results = []  # list of (ok, label, detail)
_group = ""
_verbose = "-v" in sys.argv
_filter = next((a for a in sys.argv[1:] if not a.startswith("-")), "")


def group(name):
    global _group
    _group = name


def check(ok, label, detail=""):
    tag = f"[{_group}] {label}"
    _results.append((ok, tag, detail))
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {label}"
    if _verbose or not ok:
        if detail:
            line += f"\n         {detail}"
    print(line)


def run_active():
    return not _filter or _filter.lower() in _group.lower()


def cli(*args, stdin_text=None, env_extra=None, cwd=None):
    """运行 journal.py 子命令，返回 (returncode, stdout, stderr)。"""
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(cwd) if cwd else None,
    )
    return result.returncode, result.stdout, result.stderr


def make_temp_root():
    """返回一个临时目录路径字符串，调用方负责清理（用 TemporaryDirectory）。"""
    d = tempfile.mkdtemp()
    return d


def make_git_repo(commits=None):
    """在临时目录建一个有若干 commit 的 git repo，返回 (tmp_dir, [sha_list])。"""
    import shutil
    d = tempfile.mkdtemp()
    def g(*args):
        subprocess.run(["git", *args], cwd=d, capture_output=True,
                       env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                            "HOME": d})

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "T")

    shas = []
    for i, (fname, msg) in enumerate(commits or [("f.txt", "init")]):
        fpath = Path(d) / fname
        fpath.parent.mkdir(parents=True, exist_ok=True)
        with open(fpath, "a", encoding="utf-8") as f:
            f.write(f"line {i}\n")
        g("add", ".")
        g("commit", "-qm", msg)
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                           capture_output=True, text=True)
        shas.append(r.stdout.strip())
    return d, shas


def sample_block(date_str, project, threads, time="10:00",
                 body_extra="", head=""):
    threads_json = json.dumps(threads, ensure_ascii=False)
    head_line = f"\nhead: {head}" if head else ""
    return (
        f"## {time} · session\n\n"
        f"---\n"
        f"date: {date_str}\n"
        f"project: {project}\n"
        f"threads: {threads_json}{head_line}\n"
        f"---\n\n"
        f"**做成了什么**\n- 测试条目 {time}\n\n"
        f"**关键决策 / 为什么**\n- {body_extra or '无'}\n\n"
        f"**卡点 / 困惑**\n- 无\n\n"
        f"**下次 TODO**\n- 下一步\n\n"
        f"**碰到的 open thread**\n- 无\n"
    )


# ════════════════════════════════════════════════════════════════════════════
# 单元测试 —— 直接调用 journal.py 的纯函数（import 隔离，不起子进程）
# ════════════════════════════════════════════════════════════════════════════

# 把 journal 当模块 import
import importlib.util
spec_ = importlib.util.spec_from_file_location("journal", SCRIPT)
j = importlib.util.module_from_spec(spec_)
spec_.loader.exec_module(j)


# ── §U1: frontmatter 解析（宽容读）─────────────────────────────────────────
if not _filter or run_active():
    group("U1 frontmatter-parse")
    print(f"\n[{_group}]")

    # 裸词 list（人手写）
    lines = ["threads: [leakage-audit, replay-buffer]"]
    out = j.parse_fm_lines(lines)
    check(isinstance(out["threads"], list) and len(out["threads"]) == 2,
          "裸词 list 解析成功",
          f"got: {out['threads']}")

    # JSON 数组（脚本写）
    lines = ['threads: ["leakage-audit", "replay-buffer"]']
    out = j.parse_fm_lines(lines)
    check(out["threads"] == ["leakage-audit", "replay-buffer"],
          "JSON list round-trip")

    # 含中文、逗号的 next 字段
    val = ["补被试级 split 校验", "重跑 cross,subject baseline"]
    raw = json.dumps(val, ensure_ascii=False)
    lines = [f"next: {raw}"]
    out = j.parse_fm_lines(lines)
    check(out["next"] == val, "next 字段含中文与逗号 round-trip", f"got: {out['next']}")

    # 空 list
    lines = ["threads: []"]
    out = j.parse_fm_lines(lines)
    check(out["threads"] == [], "空 list 解析为 []")

    # 标量
    lines = ["date: 2026-06-29", "project: hi-spiced", "head: abc1234"]
    out = j.parse_fm_lines(lines)
    check(out == {"date": "2026-06-29", "project": "hi-spiced", "head": "abc1234"},
          "标量字段解析")


# ── §U2: frontmatter 序列化（写规范 JSON 数组）───────────────────────────
if not _filter or run_active():
    group("U2 frontmatter-dump")
    print(f"\n[{_group}]")

    fields = {"date": "2026-06-29", "project": "p", "threads": ["t1", "含逗号,的t2"],
              "next": ["步骤一", "步骤二"]}
    out = j.dump_frontmatter(fields)
    check(out.startswith("---\n") and out.endswith("\n---"), "输出以 --- 包裹")
    check('"含逗号,的t2"' in out, "含逗号元素被 JSON 引号保护")
    check('"步骤一"' in out and '"步骤二"' in out, "next 列表正确序列化")

    # key 顺序
    lines_out = out.splitlines()
    keys = [l.split(":")[0].strip() for l in lines_out if ":" in l and l != "---"]
    expected_first = ["date", "project", "threads", "next"]
    check(keys[:4] == expected_first, "字段按 FM_KEY_ORDER 排序", f"got: {keys}")


# ── §U3: 多 frontmatter 块拆分──────────────────────────────────────────────
if not _filter or run_active():
    group("U3 split-blocks")
    print(f"\n[{_group}]")

    text = (
        "# 2026-06-29\n\n"
        + sample_block("2026-06-29", "p", ["t1"], "10:00", "first")
        + "\n"
        + sample_block("2026-06-29", "p", ["t2"], "11:00", "second")
    )
    blocks = j.split_session_blocks(text)
    check(len(blocks) == 2, "正确拆成 2 块", f"got {len(blocks)} blocks")
    check(blocks[0].fields.get("threads") == ["t1"], "块 1 threads 正确")
    check(blocks[1].fields.get("threads") == ["t2"], "块 2 threads 正确")
    check("first" in blocks[0].body, "块 1 正文归属正确")
    check("second" in blocks[1].body, "块 2 正文归属正确")

    # 正文含 --- 水平线的情况（不能被误识别为 frontmatter fence）
    tricky = (
        "## 12:00 · session\n\n"
        "---\ndate: 2026-06-29\nproject: p\nthreads: []\n---\n\n"
        "正文里有一条分割线\n\n---\n\n就这样\n"
        "\n"
        "## 13:00 · session\n\n"
        "---\ndate: 2026-06-29\nproject: p\nthreads: []\n---\n\n"
        "第二块\n"
    )
    blocks2 = j.split_session_blocks(tricky)
    check(len(blocks2) == 2, "正文含 --- 水平线时仍正确拆成 2 块",
          f"got {len(blocks2)} blocks")
    check("分割线" in blocks2[0].body, "水平线留在块 1 正文，不被当成 frontmatter fence")


# ── §U4: 路径解析──────────────────────────────────────────────────────────
if not _filter or run_active():
    group("U4 path-resolve")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["JOURNAL_ROOT"] = tmp
        p = j.resolve_day_path(date(2026, 6, 29))
        check(p.name == "2026-06-29.md", "日文件名格式正确")
        check(p.parent.name == "06", "月目录正确")
        check(p.parent.parent.name == "2026", "年目录正确")
    del os.environ["JOURNAL_ROOT"]


# ── §U5: validate 校验规则───────────────────────────────────────────────
if not _filter or run_active():
    group("U5 validate")
    print(f"\n[{_group}]")

    def raises_ve(fields):
        try:
            j.validate(fields)
            return False
        except j.ValidationError:
            return True

    check(not raises_ve({"date": "2026-06-29", "project": "p", "threads": []}),
          "合法字段通过")
    check(raises_ve({"project": "p", "threads": []}),
          "缺 date 被拒")
    check(raises_ve({"date": "2026/06/29", "project": "p", "threads": []}),
          "date 格式非 YYYY-MM-DD 被拒")
    check(raises_ve({"date": "2026-06-29", "project": "", "threads": []}),
          "空 project 被拒")
    check(raises_ve({"date": "2026-06-29", "project": "p", "threads": "nope"}),
          "threads 为字符串被拒")
    check(raises_ve({"date": "2026-06-29", "project": "p", "threads": [],
                     "blockers": "nope"}),
          "blockers 为字符串被拒")
    check(not raises_ve({"date": "2026-06-29", "project": "p", "threads": ["t"],
                          "blockers": ["b"], "next": ["n"]}),
          "可选 list 字段合法时通过")


# ── §U6: parse_shortstat──────────────────────────────────────────────────
if not _filter or run_active():
    group("U6 parse-shortstat")
    print(f"\n[{_group}]")

    s = " 2 files changed, 41 insertions(+), 13 deletions(-)"
    r = j.parse_shortstat(s)
    check(r == {"files": 2, "insertions": 41, "deletions": 13}, "典型 shortstat",
          f"got: {r}")
    check(j.parse_shortstat("") == {"files": 0, "insertions": 0, "deletions": 0},
          "空字符串返回零值")
    check(j.parse_shortstat(None) == {"files": 0, "insertions": 0, "deletions": 0},
          "None 返回零值")
    s2 = " 1 file changed, 3 insertions(+)"
    r2 = j.parse_shortstat(s2)
    check(r2["files"] == 1 and r2["insertions"] == 3 and r2["deletions"] == 0,
          "单数形式（file / insertion）解析正确", f"got: {r2}")


# ════════════════════════════════════════════════════════════════════════════
# 集成测试 —— subprocess 跑 CLI，验证 §10 验收清单
# ════════════════════════════════════════════════════════════════════════════

# ── §10.1: --help 可用，零依赖──────────────────────────────────────────────
if not _filter or run_active():
    group("I1 §10.1 help")
    print(f"\n[{_group}]")

    rc, out, err = cli("--help")
    check(rc == 0, "--help 退出码 0")
    check("collect" in out and "append" in out and "threads" in out,
          "--help 列出全部子命令", f"stdout snippet: {out[:120]}")

    # 确认零额外 pip 依赖
    rc2, out2, _ = cli("selftest")
    check(rc2 == 0, "selftest 全绿（间接验证零 pip 依赖）")


# ── §10.2a: collect 在 git repo 内输出合法 JSON───────────────────────────
if not _filter or run_active():
    group("I2a §10.2 collect-git")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        repo, shas = make_git_repo([
            ("src/split.py",  "fix subject leakage"),
            ("src/scaler.py", "normalize only on train fold"),
        ])
        rc, out, err = cli("collect", cwd=repo,
                           env_extra={"JOURNAL_ROOT": tmp})
        check(rc == 0, "collect 退出码 0")
        try:
            data = json.loads(out)
            valid_json = True
        except json.JSONDecodeError:
            valid_json = False
            data = {}
        check(valid_json, "stdout 是合法 JSON")
        check(isinstance(data.get("commits"), list) and len(data["commits"]) >= 1,
              "commits 非空", f"got: {data.get('commits')}")
        check(data.get("branch") is not None, "branch 非 null",
              f"got: {data.get('branch')}")
        check(data.get("head") is not None, "head 非 null",
              f"got: {data.get('head')}")
        check(isinstance(data.get("files_changed"), list),
              "files_changed 是 list")
        check("src/scaler.py" in data.get("files_changed", []),
              "files_changed 包含改动文件",
              f"got: {data.get('files_changed')}")
        check("uncommitted" in data, "含 uncommitted 字段")

        # --since 场景：只报 since..HEAD 之间的 commit
        since = shas[0]  # 第 1 个 commit（只有 fix subject leakage）
        rc2, out2, _ = cli("collect", "--since", since, cwd=repo,
                            env_extra={"JOURNAL_ROOT": tmp})
        data2 = json.loads(out2) if rc2 == 0 else {}
        check(len(data2.get("commits", [])) == 1,
              "--since 只报范围内的 commit",
              f"got {len(data2.get('commits', []))} commits, expected 1")

        import shutil; shutil.rmtree(repo, ignore_errors=True)


# ── §10.2b: collect 在非 git 目录不崩溃─────────────────────────────────
if not _filter or run_active():
    group("I2b §10.2 collect-non-git")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        rc, out, err = cli("collect", cwd=tmp, env_extra={"JOURNAL_ROOT": tmp})
        check(rc == 0, "非 git 目录退出码仍 0")
        try:
            data = json.loads(out)
        except Exception:
            data = {}
        check(data.get("commits") == [], "commits 为 []")
        check(data.get("branch") is None, "branch 为 null")
        check(data.get("head") is None, "head 为 null")


# ── §10.3 + §10.4: append 落盘，多块可拆分────────────────────────────────
if not _filter or run_active():
    group("I3 §10.3-4 append-and-split")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        ENV = {"JOURNAL_ROOT": tmp}
        b1 = sample_block("2026-06-29", "hi-spiced", ["leakage-audit"], "14:32",
                           "归一化只在训练折 fit（abc1234 改 src/scaler.py）")
        b2 = sample_block("2026-06-29", "hi-spiced", ["replay-buffer"], "15:10",
                           "第二次 capture：补被试级 split 校验")

        rc1, _, err1 = cli("append", stdin_text=b1, env_extra=ENV)
        check(rc1 == 0, "第 1 次 append 退出码 0", err1.strip())

        rc2, _, err2 = cli("append", stdin_text=b2, env_extra=ENV)
        check(rc2 == 0, "第 2 次 append 退出码 0", err2.strip())

        day_file = Path(tmp) / "2026" / "06" / "2026-06-29.md"
        check(day_file.exists(), "日文件已创建")

        text = day_file.read_text(encoding="utf-8")

        # §10.3: 文件头、frontmatter 合法、五槽位
        check(text.startswith("# 2026-06-29"), "文件头 # YYYY-MM-DD 正确")
        check(text.count("# 2026-06-29") == 1, "文件头只写一次")
        for slot in ("**做成了什么**", "**关键决策 / 为什么**", "**卡点 / 困惑**",
                     "**下次 TODO**", "**碰到的 open thread**"):
            check(text.count(slot) == 2, f"五槽位 '{slot}' 在两块都出现")

        # §10.4: 连续两次 append 后，parser 拆成 2 个独立块
        blocks = j.split_session_blocks(text)
        check(len(blocks) == 2, "两块可被正确拆分", f"got {len(blocks)}")
        check(blocks[0].fields.get("project") == "hi-spiced", "块 1 project")
        check(blocks[1].fields.get("threads") == ["replay-buffer"], "块 2 threads")

        # commit 应以 hash 引用，不贴 diff（规格规定模型行为；这里只验机器侧）
        check("abc1234" in text, "commit hash 在正文中出现")

        # §10.5: round-trip
        check(blocks[0].fields.get("date") == "2026-06-29", "date round-trip")
        check(isinstance(blocks[0].fields.get("threads"), list), "threads round-trip 是 list")

        # Windows 换行检查
        check("\r\n" not in text, "无 CRLF（Windows 换行未混入）")


# ── §10.6: append 对非法 frontmatter 报错并非零退出──────────────────────
if not _filter or run_active():
    group("I4 §10.6 append-validation")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        ENV = {"JOURNAL_ROOT": tmp}

        # 缺 date
        bad_no_date = "## 10:00 · session\n\n---\nproject: p\nthreads: []\n---\n\n正文\n"
        rc, _, err = cli("append", stdin_text=bad_no_date, env_extra=ENV)
        check(rc != 0, "缺 date → 非零退出", f"rc={rc}, err={err.strip()}")
        check(err.strip() != "", "缺 date → stderr 有错误信息")

        # threads 非 list
        bad_threads = ("## 10:00 · session\n\n"
                       "---\ndate: 2026-06-29\nproject: p\nthreads: nope\n---\n\n正文\n")
        rc2, _, err2 = cli("append", stdin_text=bad_threads, env_extra=ENV)
        check(rc2 != 0, "threads 非 list → 非零退出", f"rc={rc2}, err={err2.strip()}")

        # project 为空
        bad_proj = ("## 10:00 · session\n\n"
                    "---\ndate: 2026-06-29\nproject:  \nthreads: []\n---\n\n正文\n")
        rc3, _, err3 = cli("append", stdin_text=bad_proj, env_extra=ENV)
        check(rc3 != 0, "空 project → 非零退出")

        # 确认合法 block 没被误拒
        good = sample_block("2026-06-29", "p", [], "10:00")
        rc4, _, _ = cli("append", stdin_text=good, env_extra=ENV)
        check(rc4 == 0, "合法 block 不被误拒")


# ── §10.7: threads 列出 thread、最近日期、STALE──────────────────────────
if not _filter or run_active():
    group("I5 §10.7 threads-command")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        ENV = {"JOURNAL_ROOT": tmp}
        today_str = date.today().strftime("%Y-%m-%d")
        stale_str = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")

        # 今天活跃的 thread
        b_fresh = sample_block(today_str, "proj", ["active-thread"], "09:00")
        cli("append", stdin_text=b_fresh, env_extra=ENV)

        # 10 天前（stale）的 thread —— 用 --date 写到那天
        b_stale = sample_block(stale_str, "proj", ["stale-thread"], "09:00")
        cli("append", "--date", stale_str, stdin_text=b_stale, env_extra=ENV)

        rc, out, _ = cli("threads", "--stale-days", "7", env_extra=ENV)
        check(rc == 0, "threads 退出码 0")
        check("active-thread" in out, "活跃 thread 出现在表格")
        check("stale-thread" in out, "停滞 thread 出现在表格")
        check("STALE" in out, "超过 stale-days 的 thread 有 STALE 标记",
              f"stdout:\n{out}")

        # 顺序：最近活跃在前
        active_pos = out.find("active-thread")
        stale_pos = out.find("stale-thread")
        check(active_pos < stale_pos, "最近活跃的 thread 排在前面",
              f"active_pos={active_pos}, stale_pos={stale_pos}")

        # roll-up 文件不被误计入 thread（v2 预留文件名格式）
        rollup_file = Path(tmp) / "2026" / "week-26.md"
        rollup_file.parent.mkdir(parents=True, exist_ok=True)
        rollup_file.write_text("## 10:00 · session\n---\ndate: 2026-06-20\n"
                               "project: p\nthreads: [\"should-not-appear\"]\n---\n",
                               encoding="utf-8")
        rc2, out2, _ = cli("threads", env_extra=ENV)
        check("should-not-appear" not in out2,
              "roll-up 文件（week-NN.md）不被误当作日条目")


# ── §10.8: 纯 markdown，grep 直接命中────────────────────────────────────
if not _filter or run_active():
    group("I6 §10.8 grep-able")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        ENV = {"JOURNAL_ROOT": tmp}
        b = sample_block("2026-06-29", "p", ["leakage-audit"], "10:00",
                         "leakage-audit: 被试级已修，窗口重叠检测还没做")
        cli("append", stdin_text=b, env_extra=ENV)

        # grep 等价：直接读文件找字符串
        found = any(
            "leakage-audit" in p.read_text(encoding="utf-8")
            for p in Path(tmp).rglob("*.md")
        )
        check(found, "thread 名 'leakage-audit' 直接 grep 可命中",
              f"searched under {tmp}")

        # 确认不含二进制 / JSON 包装
        day_file = next(Path(tmp).rglob("*.md"))
        raw = day_file.read_bytes()
        check(b"\x00" not in raw, "文件无 null 字节（是纯文本）")


# ── §10.extra: --since 回退链（今天日文件 head → midnight）────────────────
if not _filter or run_active():
    group("I7 collect --since fallback")
    print(f"\n[{_group}]")

    repo, shas = make_git_repo([
        ("a.py", "commit A"),
        ("b.py", "commit B"),
    ])
    with tempfile.TemporaryDirectory() as tmp:
        ENV = {"JOURNAL_ROOT": tmp}
        today_str = date.today().strftime("%Y-%m-%d")

        # 先 append 一个含 head=shas[0] 的 session 块
        b = sample_block(today_str, "p", [], "08:00", head=shas[0])
        cli("append", stdin_text=b, env_extra=ENV)

        # collect 不给 --since，应自动回退到 shas[0] 并只报 commit B
        rc, out, _ = cli("collect", cwd=repo, env_extra=ENV)
        check(rc == 0, "--since 回退链测试：collect 退出码 0")
        try:
            data = json.loads(out)
        except Exception:
            data = {}
        subjects = [c["subject"] for c in data.get("commits", [])]
        check("commit B" in subjects, "从今天日文件的 head 回退：只报 commit B",
              f"subjects={subjects}")
        check("commit A" not in subjects, "commit A 不在范围内")

    import shutil; shutil.rmtree(repo, ignore_errors=True)


# ── §10.extra: path 子命令─────────────────────────────────────────────────
if not _filter or run_active():
    group("I8 path-command")
    print(f"\n[{_group}]")

    with tempfile.TemporaryDirectory() as tmp:
        rc, out, _ = cli("path", "--date", "2026-06-29",
                          env_extra={"JOURNAL_ROOT": tmp})
        check(rc == 0, "path 退出码 0")
        check("2026-06-29.md" in out, "path 输出含日文件名", f"got: {out.strip()}")
        check("2026" in out and "06" in out, "path 输出含年/月目录")


# ── §10.extra: rollup v2 stub──────────────────────────────────────────────
if not _filter or run_active():
    group("I9 rollup-stub")
    print(f"\n[{_group}]")

    rc, _, err = cli("rollup")
    check(rc == 2, "rollup stub 退出码 2（表示未实现，非错误）", f"rc={rc}")
    check("v2" in err.lower() or "未实现" in err, "rollup stderr 提示 v2/未实现")


# ════════════════════════════════════════════════════════════════════════════
# 结果汇总
# ════════════════════════════════════════════════════════════════════════════
total = len(_results)
passed = sum(1 for ok, *_ in _results if ok)
failed = total - passed

print(f"\n{'═'*60}")
print(f"结果: {passed}/{total} 通过", end="")
if failed:
    print(f"  ({failed} 失败)")
    print("\n失败列表:")
    for ok, label, detail in _results:
        if not ok:
            print(f"  ✗ {label}")
            if detail:
                print(f"      {detail}")
else:
    print("  全绿 ✅")
print(f"{'═'*60}")

sys.exit(0 if failed == 0 else 1)
