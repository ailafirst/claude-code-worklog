#!/usr/bin/env python3
"""Claude Code 日记 skill 的确定性引擎（纯 Python 3 标准库，零 pip 依赖）。

职责边界：只做"不该每次重新推导"的事 —— 日期/路径解析、frontmatter 读写
round-trip、git delta、thread 聚合、session 随手记、快照保存、周蒸馏素材收集。
需要判断力的事（把 session 写成高信号条目、synthesize 周 rollup）留给模型。

子命令：
  collect   采集 git 原料 → JSON（自动合并 pending snapshot + session notes）
  append    从 stdin 读 session 块落盘（成功后清空 session notes）
  note      记录 session 中的随手想法到暂存区（供下次 collect 使用）
  snapshot  保存当前 git delta 快照（SessionEnd hook 调用）
  threads   聚合所有 thread 活跃度
  rollup    周蒸馏：无 --save 时输出本周素材，--save 时将 stdin 写入 week 文件
  path      打印日文件路径（调试用）
  selftest  hermetic 自检

数据根目录：~/.claude/journal/（可用 JOURNAL_ROOT 环境变量覆盖）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Windows 关键：全链路 UTF-8，否则中文在 cp936 下静默乱码 ──────────────
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

LIST_FIELDS  = ("threads", "blockers", "next")
FM_KEY_ORDER = ("date", "project", "threads", "blockers", "head", "next")
DAY_RE       = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")  # 排除 week/month rollup 文件


class ValidationError(Exception):
    """append 入库前校验失败 —— 这是内置 verify。"""


class SessionBlock:
    __slots__ = ("heading", "fields", "body")

    def __init__(self, heading, fields, body):
        self.heading = heading
        self.fields  = fields
        self.body    = body


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# ════════════════════════════════════════════════════════════════════════════
# 核心层：frontmatter / 块解析（无副作用，可单测）
# ════════════════════════════════════════════════════════════════════════════

def parse_fm_lines(lines):
    """把 frontmatter 行解析成 dict。列表字段宽容读：先试 JSON，退回逗号切分。"""
    fields = {}
    for line in lines:
        if ":" not in line or not line.strip():
            continue
        key, _, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            fields[key] = _parse_list(raw)
        else:
            fields[key] = raw
    return fields


def _parse_list(raw):
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [str(x) for x in val]
    except json.JSONDecodeError:
        pass  # 人手写的裸词（threads: [a, b]）不是合法 JSON，退回切分
    inner = raw[1:-1].strip()
    if not inner:
        return []
    return [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]


def dump_frontmatter(fields):
    """写入规范化：列表一律输出合法 JSON 数组，round-trip 绝对可靠。"""
    keys  = [k for k in FM_KEY_ORDER if k in fields]
    keys += [k for k in fields if k not in FM_KEY_ORDER]
    out   = ["---"]
    for k in keys:
        v = fields[k]
        if isinstance(v, list):
            items = ", ".join(json.dumps(x, ensure_ascii=False) for x in v)
            out.append(f"{k}: [{items}]")
        else:
            out.append(f"{k}: {v}")
    out.append("---")
    return "\n".join(out)


def _split_block_fm(lines):
    """在一段（H2 之后）里取第一个 ---...--- 作为 frontmatter，其后为正文。"""
    fences = [i for i, l in enumerate(lines) if l.strip() == "---"]
    if len(fences) < 2:
        return {}, lines
    a, b = fences[0], fences[1]
    return parse_fm_lines(lines[a + 1:b]), lines[b + 1:]


def split_session_blocks(text):
    """两级解析：先按 H2(## …) 锚切块，再在块内取 frontmatter。

    比全文扫 --- 配对更稳——不会被正文水平线带偏。
    """
    lines  = text.splitlines()
    heads  = [i for i, l in enumerate(lines) if l.startswith("## ")]
    blocks = []
    for n, start in enumerate(heads):
        end     = heads[n + 1] if n + 1 < len(heads) else len(lines)
        heading = lines[start]
        fields, body = _split_block_fm(lines[start + 1:end])
        blocks.append(SessionBlock(heading, fields, "\n".join(body).strip()))
    return blocks


def parse_single_block(raw):
    """解析一个外部喂入的 session 块；缺 H2 时按现在时刻补一个。"""
    lines = raw.splitlines()
    hidx  = next((i for i, l in enumerate(lines) if l.startswith("## ")), None)
    if hidx is None:
        heading, rest = f"## {datetime.now():%H:%M} · session", lines
    else:
        heading, rest = lines[hidx], lines[hidx + 1:]
    fields, body = _split_block_fm(rest)
    return SessionBlock(heading, fields, "\n".join(body).strip())


def render_block(blk):
    return f"{blk.heading}\n\n{dump_frontmatter(blk.fields)}\n\n{blk.body}\n"


# ════════════════════════════════════════════════════════════════════════════
# 路径 / 校验
# ════════════════════════════════════════════════════════════════════════════

def journal_root():
    env  = os.environ.get("JOURNAL_ROOT")
    base = env if env else "~/.claude/journal"
    return Path(os.path.expanduser(base))


def resolve_day_path(d):
    return journal_root() / f"{d:%Y}" / f"{d:%m}" / f"{d:%Y-%m-%d}.md"


def resolve_date(cli_date, fm_date):
    val = cli_date or fm_date
    if val:
        return datetime.strptime(val, "%Y-%m-%d").date()
    return date.today()


def validate(fields):
    d = fields.get("date")
    if not d:
        raise ValidationError("frontmatter 缺少必填 date")
    try:
        datetime.strptime(d, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(f"date 非法（须 YYYY-MM-DD）：{d!r}")
    if not (fields.get("project") or "").strip():
        raise ValidationError("frontmatter 缺少非空 project")
    if not isinstance(fields.get("threads"), list):
        raise ValidationError("threads 必须是 list（可空）")
    for k in ("blockers", "next"):
        if k in fields and not isinstance(fields[k], list):
            raise ValidationError(f"{k} 若存在必须是 list")


# ════════════════════════════════════════════════════════════════════════════
# git 封装（失败返回 None / 空，绝不抛栈）
# ════════════════════════════════════════════════════════════════════════════

def run_git(*args):
    try:
        p = subprocess.run(["git", *args], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    return p.stdout if p.returncode == 0 else None


def in_git_repo():
    out = run_git("rev-parse", "--is-inside-work-tree")
    return bool(out) and out.strip() == "true"


def git_head():
    out = run_git("rev-parse", "HEAD")
    return out.strip() if out else None


def is_ancestor(a, b):
    try:
        p = subprocess.run(["git", "merge-base", "--is-ancestor", a, b],
                           capture_output=True)
    except FileNotFoundError:
        return False
    return p.returncode == 0


def _zero_stats():
    return {"files": 0, "insertions": 0, "deletions": 0}


def parse_shortstat(text):
    res = _zero_stats()
    if not text:
        return res
    for key, pat in (("files",      r"(\d+)\s+files?\s+changed"),
                     ("insertions", r"(\d+)\s+insertions?\(\+\)"),
                     ("deletions",  r"(\d+)\s+deletions?\(-\)")):
        m = re.search(pat, text)
        if m:
            res[key] = int(m.group(1))
    return res


def _commits(log_args):
    out  = run_git("log", *log_args, "--format=%H%x1f%s")
    rows = []
    if out:
        for line in out.splitlines():
            if "\x1f" in line:
                sha, subj = line.split("\x1f", 1)
                rows.append({"sha": sha[:7], "subject": subj})
    return rows


def last_head_today():
    path = resolve_day_path(date.today())
    if not path.exists():
        return None
    for blk in reversed(split_session_blocks(path.read_text(encoding="utf-8"))):
        if blk.fields.get("head"):
            return blk.fields["head"]
    return None


# ════════════════════════════════════════════════════════════════════════════
# session notes 暂存区（.session-scratch）
# 用途：session 过程中随手记录想法，供下次 collect 自动带入 JSON
# ════════════════════════════════════════════════════════════════════════════

def _scratch_path():
    return journal_root() / ".session-scratch"


def _read_session_notes():
    p = _scratch_path()
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8").strip()
    return text if text else None


def _clear_session_notes():
    p = _scratch_path()
    if p.exists():
        _write_text(p, "")


# ════════════════════════════════════════════════════════════════════════════
# pending snapshot（.pending-collect.json）
# 用途：SessionEnd hook 保存 git delta，供下次 collect 使用
#       解决"session 结束后 git 上下文还在，collect 却要事后重算"的问题
# ════════════════════════════════════════════════════════════════════════════

def _pending_path():
    return journal_root() / ".pending-collect.json"


def _load_pending_snapshot():
    """若 pending snapshot 存在、cwd 匹配、且不超过 24h，返回其 git_data；否则 None。"""
    p = _pending_path()
    if not p.exists():
        return None
    try:
        data  = json.loads(p.read_text(encoding="utf-8"))
        if data.get("cwd") != str(Path.cwd()):
            return None
        age_h = (datetime.now() - datetime.fromisoformat(data["timestamp"])
                 ).total_seconds() / 3600
        if age_h > 24:
            return None
        return data.get("git_data")
    except Exception:
        return None


def _save_pending_snapshot(git_data):
    payload = {
        "cwd":       str(Path.cwd()),
        "timestamp": datetime.now().isoformat(),
        "git_data":  git_data,
    }
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_text(p, json.dumps(payload, ensure_ascii=False, indent=2))


# ════════════════════════════════════════════════════════════════════════════
# git 采集核心（collect / snapshot 共用）
# ════════════════════════════════════════════════════════════════════════════

def _do_collect(since=None):
    """纯函数：采集 git delta，返回 dict（不含 session_notes）。"""
    if not in_git_repo():
        return {"since": None, "head": None, "branch": None,
                "commits": [], "files_changed": [], "stats": _zero_stats(),
                "uncommitted": {"files": [], "stats": _zero_stats()}}

    head   = git_head()
    branch = (run_git("rev-parse", "--abbrev-ref", "HEAD") or "").strip() or None
    if branch == "HEAD":
        branch = None

    since = since or last_head_today()

    if since and head and is_ancestor(since, head):
        commits     = _commits([f"{since}..{head}"])
        names       = run_git("diff", "--name-only", since, head) or ""
        files       = [f for f in names.splitlines() if f]
        stats       = parse_shortstat(run_git("diff", "--shortstat", since, head))
        used_since  = since
    else:
        commits     = _commits(["--since=midnight"])
        names       = run_git("log", "--since=midnight", "--name-only", "--format=") or ""
        files       = sorted({f for f in names.splitlines() if f})
        stats       = parse_shortstat(
                          run_git("log", "--since=midnight", "--shortstat", "--format=") or "")
        stats["files"] = len(files)
        used_since  = None

    porcelain   = run_git("status", "--porcelain") or ""
    uncommitted = {
        "files": [l[3:] for l in porcelain.splitlines() if l.strip()],
        "stats": parse_shortstat(run_git("diff", "--shortstat") or ""),
    }
    return {"since": used_since, "head": head, "branch": branch,
            "commits": commits, "files_changed": files, "stats": stats,
            "uncommitted": uncommitted}


# ════════════════════════════════════════════════════════════════════════════
# 文件 I/O
# ════════════════════════════════════════════════════════════════════════════

def _write_text(path, text):
    # 显式 open：Path.write_text 直到 3.10 才支持 newline=，这里兼容 3.8。
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_append(path, block_text, d):
    block_text = block_text.strip() + "\n"
    if not path.exists():
        _write_text(path, f"# {d:%Y-%m-%d}\n\n{block_text}")
        return
    existing = path.read_text(encoding="utf-8")
    # 强制块间空行：否则两块 H2/frontmatter 粘连会把解析带崩
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    _write_text(path, existing + sep + block_text)


def iter_day_files():
    root = journal_root()
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if DAY_RE.match(p.name))


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ════════════════════════════════════════════════════════════════════════════
# 子命令：collect
# ════════════════════════════════════════════════════════════════════════════

def cmd_collect(args):
    fresh = getattr(args, "fresh", False)

    # 优先使用 pending snapshot（--fresh 时跳过）
    if not fresh:
        pending = _load_pending_snapshot()
        if pending is not None:
            pending["from_snapshot"] = True
            notes = _read_session_notes()
            if notes:
                pending["session_notes"] = notes
            _emit(pending)
            return

    # 实时采集
    data  = _do_collect(getattr(args, "since", None))
    notes = _read_session_notes()
    if notes:
        data["session_notes"] = notes
    _emit(data)


# ════════════════════════════════════════════════════════════════════════════
# 子命令：append
# ════════════════════════════════════════════════════════════════════════════

def do_append(raw, cli_date=None):
    """append 的纯核心，selftest 直接调用（不经 stdin、不依赖 git）。"""
    blk = parse_single_block(raw)
    validate(blk.fields)
    if not blk.fields.get("head"):
        h = git_head()
        if h:
            blk.fields["head"] = h
    d    = resolve_date(cli_date, blk.fields.get("date"))
    path = resolve_day_path(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_append(path, render_block(blk), d)
    _clear_session_notes()  # 注记已被消费，清空暂存区
    return path, blk


def cmd_append(args):
    if sys.stdin.isatty():
        die("append: 需从 stdin 喂入一个完整 session 块（用管道）")
    raw = sys.stdin.read()
    if not raw.strip():
        die("append: stdin 为空")
    try:
        path, blk = do_append(raw, args.date)
    except ValidationError as e:
        die(f"校验失败：{e}")
    threads = ", ".join(blk.fields.get("threads") or []) or "（无）"
    print(f"已写入 {path}\nthreads: {threads}", file=sys.stderr)


# ════════════════════════════════════════════════════════════════════════════
# 子命令：note
# session 过程中随手记，供下次 collect 自动带入 JSON 的 session_notes 字段
# ════════════════════════════════════════════════════════════════════════════

def cmd_note(args):
    text = (getattr(args, "message", None) or "").strip()
    if not text:
        if sys.stdin.isatty():
            die("note: 用 -m '想法' 或管道传入内容")
        text = sys.stdin.read().strip()
    if not text:
        die("note: 内容为空")
    scratch = _scratch_path()
    scratch.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%H:%M")
    with open(scratch, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"- [{now}] {text}\n")
    print(f"已记录（{scratch}）", file=sys.stderr)


# ════════════════════════════════════════════════════════════════════════════
# 子命令：snapshot
# SessionEnd hook 调用：把当前 git delta 保存成 pending 文件
# 下次 collect 若 cwd 匹配且不超 24h，直接用该快照
# ════════════════════════════════════════════════════════════════════════════

def cmd_snapshot(args):
    git_data = _do_collect()
    # 非 git 目录、空仓库不值得保存
    if not git_data.get("head"):
        return
    _save_pending_snapshot(git_data)
    # snapshot 设计为无声运行（hook 场景），仅在 stderr 留一行供排查
    print(f"snapshot saved → {_pending_path()}", file=sys.stderr)


# ════════════════════════════════════════════════════════════════════════════
# 子命令：threads
# ════════════════════════════════════════════════════════════════════════════

def _disp_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, w):
    return s + " " * max(0, w - _disp_width(s))


def cmd_threads(args):
    today = date.today()
    agg   = {}
    for path in iter_day_files():
        for blk in split_session_blocks(path.read_text(encoding="utf-8")):
            d = blk.fields.get("date")
            if not d:
                continue
            for t in (blk.fields.get("threads") or []):
                rec = agg.setdefault(t, {"count": 0, "first": d, "last": d})
                rec["count"] += 1
                rec["first"]  = min(rec["first"], d)
                rec["last"]   = max(rec["last"],  d)
    rows = []
    for name, rec in agg.items():
        try:
            last = datetime.strptime(rec["last"], "%Y-%m-%d").date()
            days = (today - last).days
        except ValueError:
            days = -1
        stale = "STALE" if days > args.stale_days else ""
        rows.append([name, str(rec["count"]), rec["first"], rec["last"],
                     str(days), stale])
    rows.sort(key=lambda r: r[3], reverse=True)

    header = ["thread", "出现", "首次", "最近", "距今", "状态"]
    cols   = [max(_disp_width(r[i]) for r in [header] + rows) for i in range(6)]
    for r in [header] + rows:
        print("  ".join(_pad(r[i], cols[i]) for i in range(6)).rstrip())
    if not rows:
        print("（暂无 thread 记录）")


# ════════════════════════════════════════════════════════════════════════════
# 子命令：rollup
# 两种模式：
#   无 --save  → 收集本周所有日条目，输出到 stdout 供模型 synthesize
#   --save     → 从 stdin 读模型生成的 rollup，写入 YYYY/week-NN.md
# ════════════════════════════════════════════════════════════════════════════

def _get_week_bounds(week_num=None, year=None):
    """返回 (start, end, week_num, year)，符合 ISO 8601 周定义。"""
    today = date.today()
    iso   = today.isocalendar()          # (year, week, weekday) in Python 3.8
    if week_num is None:
        week_num = iso[1]
    if year is None:
        year = iso[0]
    # ISO week 1 的 Monday = Jan 4 当周的 Monday
    jan4          = date(year, 1, 4)
    week1_monday  = jan4 - timedelta(days=jan4.weekday())
    start         = week1_monday + timedelta(weeks=week_num - 1)
    end           = start + timedelta(days=6)
    return start, end, week_num, year


def cmd_rollup(args):
    if getattr(args, "save", False):
        _rollup_save(args)
    else:
        _rollup_collect(args)


def _rollup_collect(args):
    """收集本周日条目，格式化为 LLM 可直接阅读的 markdown 素材。"""
    start, end, week_num, year = _get_week_bounds(
        getattr(args, "week", None), getattr(args, "year", None))

    out = [f"# 第 {week_num} 周日记原文（{year}）：{start} ~ {end}\n\n"]
    out.append(
        "以下是本周所有 session 块的完整内容，供你蒸馏成周 rollup。\n"
        "关注：本周推进了哪些 thread、最重要的决策与权衡、"
        "还悬着的 blocker、下周最值得先做的事。\n\n"
    )

    found = 0
    for path in iter_day_files():
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= day <= end):
            continue
        out.append(f"---\n\n")
        out.append(path.read_text(encoding="utf-8").strip())
        out.append("\n\n")
        found += 1

    if found == 0:
        out.append("（本周暂无日记条目）\n")
    else:
        out.append(f"---\n\n共收录 {found} 个日文件（{start} ~ {end}）。\n")

    sys.stdout.write("".join(out))


def _rollup_save(args):
    """从 stdin 读 rollup 内容，写入 YYYY/week-NN.md。"""
    if sys.stdin.isatty():
        die("rollup --save: 需从 stdin 读入 rollup 内容（管道或重定向）")
    content = sys.stdin.read().strip()
    if not content:
        die("rollup --save: stdin 为空")

    start, end, week_num, year = _get_week_bounds(
        getattr(args, "week", None), getattr(args, "year", None))

    path = journal_root() / str(year) / f"week-{week_num:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(path, content + "\n")
    print(f"rollup 已写入 {path}", file=sys.stderr)


# ════════════════════════════════════════════════════════════════════════════
# 子命令：path
# ════════════════════════════════════════════════════════════════════════════

def cmd_path(args):
    d = resolve_date(args.date, None)
    print(resolve_day_path(d))


# ════════════════════════════════════════════════════════════════════════════
# 子命令：selftest（hermetic，覆盖 §10 第 3-6 条）
# ════════════════════════════════════════════════════════════════════════════

def cmd_selftest(args):
    results = []

    def check(ok, label):
        results.append((ok, label))

    def raises(fn):
        try:
            fn()
            return False
        except ValidationError:
            return True

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["JOURNAL_ROOT"] = tmp
        b1 = _sample_block("2026-06-29", "demo", ["t1", "t2"], "12:00")
        b2 = _sample_block("2026-06-29", "demo", ["t2"], "13:00")
        do_append(b1)
        do_append(b2)
        path   = resolve_day_path(date(2026, 6, 29))
        text   = path.read_text(encoding="utf-8")
        blocks = split_session_blocks(text)

        check(len(blocks) == 2,                        "连续两次 append 拆成两个独立块")
        check(blocks[0].fields["threads"] == ["t1", "t2"], "列表字段 round-trip")
        check(blocks[0].fields["project"] == "demo",   "标量字段 round-trip")
        check(text.count("# 2026-06-29") == 1,         "文件头只写一次")
        check(raises(lambda: validate({"project": "x", "threads": []})),
              "缺 date 被拒")
        check(raises(lambda: validate(
              {"date": "2026-06-29", "project": "x", "threads": "nope"})),
              "threads 非 list 被拒")

        # note → session_notes 随 collect 输出
        cmd_note(type("A", (), {"message": "测试随手记"})())
        notes = _read_session_notes()
        check(notes is not None and "测试随手记" in notes, "note 写入暂存区")
        do_append(_sample_block("2026-06-29", "demo", [], "14:00"))
        check(_read_session_notes() is None or _read_session_notes() == "",
              "append 后暂存区被清空")

    ok = all(r[0] for r in results)
    for passed, label in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
    print("selftest:", "全绿 ✅" if ok else "有失败 ❌")
    sys.exit(0 if ok else 1)


def _sample_block(d, project, threads, hhmm):
    return (
        f"## {hhmm} · session\n\n"
        f"---\ndate: {d}\nproject: {project}\n"
        f"threads: {json.dumps(threads, ensure_ascii=False)}\n---\n\n"
        f"**做成了什么**\n- demo {hhmm}\n"
    )


# ════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ════════════════════════════════════════════════════════════════════════════

def build_parser():
    p   = argparse.ArgumentParser(
              prog="journal.py",
              description="Claude Code 日记 skill 的确定性引擎（纯标准库）")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="采集 git 原料 → JSON")
    c.add_argument("--since", help="起点 ref；缺省按今天最后 head → midnight 回退")
    c.add_argument("--fresh", action="store_true",
                   help="忽略 pending snapshot，强制实时采集")

    a = sub.add_parser("append", help="从 stdin 读 session 块落盘")
    a.add_argument("--date", help="覆盖日期（默认取 frontmatter / today）")

    n = sub.add_parser("note", help="记录 session 中的随手想法到暂存区")
    n.add_argument("-m", "--message", help="想法内容（也可从 stdin 管道传入）")

    sub.add_parser("snapshot",
                   help="保存当前 git delta 快照（SessionEnd hook 调用）")

    t = sub.add_parser("threads", help="聚合所有 thread 活跃度")
    t.add_argument("--stale-days", type=int, default=7)

    r = sub.add_parser("rollup",
                       help="周蒸馏：无 --save 时输出本周素材，--save 时写入 week 文件")
    r.add_argument("--week", type=int, help="ISO 周号（默认本周）")
    r.add_argument("--year", type=int, help="年份（默认今年）")
    r.add_argument("--save", action="store_true",
                   help="从 stdin 读模型生成的 rollup，写入 week-NN.md")

    pa = sub.add_parser("path", help="打印日文件路径（调试用）")
    pa.add_argument("--date")

    sub.add_parser("selftest", help="hermetic 自检")

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    {
        "collect":  cmd_collect,
        "append":   cmd_append,
        "note":     cmd_note,
        "snapshot": cmd_snapshot,
        "threads":  cmd_threads,
        "rollup":   cmd_rollup,
        "path":     cmd_path,
        "selftest": cmd_selftest,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
