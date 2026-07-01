#!/usr/bin/env python3
"""
端到端集成测试 v2 — 固定 git 数据 + 固定 session 上下文，调用真实 Mimo LLM。

与 v1 的区别：
  v1 用 build_entry() 生成确定性的日记条目（不调 LLM）。
  v2 把固定的 git delta + 固定的 session 叙事喂给真实 LLM，
    验证 LLM 能否产出符合格式约束的有效条目，以及整条管道是否稳定。

验证层次：
  机器侧（确定性）：frontmatter 合法、五槽位齐全、head 正确、--since 回退链、
                    跨天独立、threads 聚合、错误恢复、纯文本。
  内容侧（宽松）：  commit hash 出现在正文（不贴 diff）、threads 是列表。

运行方式：
  py test_e2e.py

依赖：
  Python 3.8 标准库（urllib）+ env.txt（第1行 base_url，第2行 api_key）
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

# ── 全链路 UTF-8 ──────────────────────────────────────────────────────────
for _s in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

SKILL_DIR = Path(__file__).resolve().parent / "dist" / "skills" / "journal"
SCRIPT    = SKILL_DIR / "journal.py"
PYTHON    = sys.executable

_spec = importlib.util.spec_from_file_location("journal", SCRIPT)
j = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(j)


# ════════════════════════════════════════════════════════════════════════════
# 读取 API 凭据
# ════════════════════════════════════════════════════════════════════════════

def load_credentials():
    env_path = Path(__file__).resolve().parent / "env.txt"
    lines = env_path.read_text(encoding="utf-8").splitlines()
    base_url = lines[0].strip().rstrip("/")
    api_key  = lines[1].strip()
    return base_url, api_key

BASE_URL, API_KEY = load_credentials()
LLM_MODEL   = "mimo-v2.5"
LLM_TIMEOUT = 90   # 推理模型冷启动慢，留足余量


# ════════════════════════════════════════════════════════════════════════════
# Mimo 客户端（纯 stdlib）
# ════════════════════════════════════════════════════════════════════════════

class LLMError(Exception):
    pass


def llm_chat(messages, max_tokens=4096, retries=2):
    """调用 Mimo chat completions，返回 content 字符串。失败可重试。"""
    payload = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
    )
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if not content.strip():
                raise LLMError(
                    f"LLM 返回空 content（finish_reason="
                    f"{data['choices'][0].get('finish_reason')}，"
                    f"reasoning_tokens="
                    f"{data.get('usage',{}).get('completion_tokens_details',{}).get('reasoning_tokens')}）"
                    f"——可能 max_tokens 不够"
                )
            return content
        except LLMError:
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise LLMError(f"LLM 调用失败（{retries+1} 次）：{last_err}")


# ════════════════════════════════════════════════════════════════════════════
# Prompt 构造
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
你是一个工程日记记录助手，帮助工程师把一次 coding session 记录成高信号的工作日记。

核心立意（必须内化）：
日记记的是 git 丢掉的东西，不是 git 已经记下的东西。
git log 已经说了"改了什么"；你只补 git 拿不到的推理层：为什么这么改、试过哪条死路、
卡在哪、下次第一步、还有哪些 open thread。
把 commit message 换行抄一遍当正文 = 失败。

输出格式（必须严格遵守，只输出此 markdown 块，不要任何解释）：

## HH:MM · session

---
date: YYYY-MM-DD
project: <项目名>
threads: ["<kebab-case 短标签>"]
blockers: ["<卡点标签，可为 []>"]
head: <COMMIT_SHA_会在请求里给你，原样复制>
next: ["<下次第一步，越具体越好>"]
---

### 做成了什么
- <一句话成果 + commit hash（如 abc1234），引 hash 不贴 diff；无提交写"无">

### 关键决策 / 为什么
- <为什么这么改、放弃了哪条路——git 拿不到的就记这里>

### 卡点 / 困惑
- <卡在哪、还没想明白什么；没有写"无">

### 下次 TODO
- <下次第一步，具体可执行>

### 碰到的 open thread
- <thread 名：当前状态 / 还差什么；没有写"无">

约束：
1. threads / blockers / next 的值必须是 JSON 数组（["a", "b"]），不是裸文本
2. head 字段原样复制请求中提供的值，不要修改
3. commit 只引 7 位 hash + 文件名，绝不贴 diff 或完整 commit message
4. 只输出上面格式的 markdown 块，不要前言、不要结尾说明
5. 五个槽位标题严格用上面的 `### ` 三级标题，不要改成 `**加粗**`；正文别堆加粗/emoji
""".strip()


def make_user_prompt(collect_json, session_context, today_str, session_time):
    head = collect_json.get("head") or ""
    commits = collect_json.get("commits", [])
    files   = collect_json.get("files_changed", [])

    commit_list = "\n".join(
        f"  - {c['sha']}: {c['subject']}" for c in commits
    ) or "  （无新 commit）"
    file_list = ", ".join(files) or "（无）"

    return f"""\
请根据以下信息生成今天 {session_time} 这次 session 的日记块。

== git delta ==
head（必须原样填入 frontmatter head 字段）: {head}
commits:
{commit_list}
files_changed: {file_list}
stats: {json.dumps(collect_json.get('stats', {}), ensure_ascii=False)}

== session 叙事（今天这次 session 里讨论和决定的事，这是 git 看不到的部分）==
{session_context}

== 元信息 ==
date: {today_str}
session_time: {session_time}
""".strip()


# ════════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════════

def strip_fence(text):
    """去掉 LLM 有时会加的 ```markdown ... ``` 包裹。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n", "", text)
        text = re.sub(r"\n```$", "", text.rstrip())
    return text.strip()


def call_llm_for_entry(collect_json, session_context, today_str, session_time):
    """调用 LLM 生成 session 块，去掉 markdown fence 后返回。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": make_user_prompt(
            collect_json, session_context, today_str, session_time)},
    ]
    raw = llm_chat(messages, max_tokens=4096)
    return strip_fence(raw)


# ── 测试框架 ──────────────────────────────────────────────────────────────

_results = []
_current_scenario = ""


def scenario(name):
    global _current_scenario
    _current_scenario = name
    print(f"\n[{name}]")


def check(ok, label, detail=""):
    _results.append((ok, f"[{_current_scenario}] {label}", detail))
    status = "PASS" if ok else "FAIL"
    line = f"  [{status}] {label}"
    if detail and not ok:
        line += f"\n         {detail}"
    print(line)


# ── git repo 工厂 ─────────────────────────────────────────────────────────

class GitRepo:
    def __init__(self):
        self._dir = tempfile.mkdtemp()
        self._run("init", "-q")
        self._run("config", "user.email", "e2e@journal")
        self._run("config", "user.name", "E2E")

    @property
    def path(self):
        return self._dir

    def _run(self, *args):
        subprocess.run(
            ["git", *args], cwd=self._dir, capture_output=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": self._dir},
        )

    def commit(self, filename, content, message):
        fpath = Path(self._dir) / filename
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
        self._run("add", ".")
        self._run("commit", "-qm", message)
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self._dir,
            capture_output=True, text=True, encoding="utf-8",
        )
        return r.stdout.strip()

    def cleanup(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)


def cli(*args, stdin_text=None, env_extra=None, cwd=None):
    env = {**os.environ, **(env_extra or {})}
    result = subprocess.run(
        [PYTHON, str(SCRIPT), *args],
        input=stdin_text, capture_output=True,
        text=True, encoding="utf-8", env=env,
        cwd=str(cwd) if cwd else None,
    )
    return result.returncode, result.stdout, result.stderr


# ════════════════════════════════════════════════════════════════════════════
# 固定 session 叙事（这是测试中"固定"的部分，git delta 来自真实 git）
# ════════════════════════════════════════════════════════════════════════════

SESSION_CONTEXTS = {
    "s1": """\
今天上午着手实现 journal skill 的 frontmatter 解析器。

核心决策：
- 列表字段（threads/blockers/next）落盘用规范 JSON 数组，读取时宽容兼容裸词（如 [a, b]）。
  这样做的原因：round-trip 绝对可靠，中文、逗号、引号都不会裂开；
  代价是和规格示例的裸词外观略有出入，但一致性更重要。
- validate() 在入库前强制校验 date 合法、project 非空、threads 是 list；
  不合法直接 stderr 报错 + 非零退出，这是内置 verify。

卡点：
- CJK 字符（中文/日文/韩文）在表格对齐时，str.ljust 按码点数计，全角字符算 1 宽，
  实际显示是 2 宽，导致 threads 表格错位。方案选 unicodedata.east_asian_width，
  但这段还没实现。

遗留：
- frontmatter parser 和 dump 完成，validate 完成。
- CJK 对齐问题待解，留到实现 threads 命令时一并处理。
""",
    "s2": """\
下午继续，把 append 和 collect 两个核心子命令做完了。

关键决策：
- append 在写入时强制保证块间有空行：如果两个 session 块之间没有空行，
  split_session_blocks() 会把第二块的 H2 标题误当第一块的正文，导致解析错误。
  验证方式：用临时 JOURNAL_ROOT 连续 append 两次，再解析，断言得到 2 个独立块。
- collect 的 --since 回退优先级：
    今天日文件最后一块的 head → 否则 git log --since=midnight
  如果 since 不是 HEAD 的祖先（rebase 后），用 merge-base --is-ancestor 检测，
  退回 midnight，避免给出误导性 delta。
- 非 git 目录：collect 输出 commits:[] branch:null，退出码 0（预期情况不是错误）。

无卡点。
""",
    "s3": """\
第二天，把 threads 命令和完整测试套件做完了。

关键决策：
- threads 表格对齐用 unicodedata.east_asian_width：全角字符（W/F）计 2，
  其余计 1，手工补空格。这解决了前天遗留的 CJK 对齐问题。
- 测试套件发现一个隐蔽 bug：test_journal.py 里 SCRIPT 用了相对路径 journal.py，
  在以 cwd=<其他目录> 运行子进程时，Python 在那个目录找 journal.py，
  找不到就报错退出（exit 2，无 stderr 提示）。修复：Path(__file__).resolve().parent 强制绝对路径。
- Python 3.8 不支持 list[tuple] 类型注解（3.9+ 才有），改用 # 注释代替。

遗留：
- 84/84 全绿。dist/ 已可安装到 ~/.claude/，端到端真实 /journal 待实跑。
""",
}


# ════════════════════════════════════════════════════════════════════════════
# 主测试函数
# ════════════════════════════════════════════════════════════════════════════

def run_e2e():
    repo         = GitRepo()
    journal_root = tempfile.mkdtemp()
    ENV          = {"JOURNAL_ROOT": journal_root}
    today        = date.today()
    today_str    = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        # ════════════════════════════════════════════════════════════════
        # S1: Day1/上午 — 首次 capture，无 prior head
        # ════════════════════════════════════════════════════════════════
        scenario("S1: Day1/上午 LLM 首次 capture")

        sha_parser   = repo.commit("journal.py", "# stub\n",
                                   "add frontmatter parser skeleton")
        sha_validate = repo.commit("journal.py", "# +validate\n",
                                   "implement validate (date/project/threads)")

        rc, out, err = cli("collect", cwd=repo.path, env_extra=ENV)
        check(rc == 0, "collect 退出码 0", err.strip())
        c1 = json.loads(out) if rc == 0 else {}

        shas_s1 = {c["sha"] for c in c1.get("commits", [])}
        check(sha_parser[:7]   in shas_s1, "parser commit 在 S1 范围内")
        check(sha_validate[:7] in shas_s1, "validate commit 在 S1 范围内")

        print("  [LLM] 调用 Mimo 生成 S1 日记条目…", flush=True)
        t0 = time.time()
        entry_s1 = call_llm_for_entry(c1, SESSION_CONTEXTS["s1"],
                                       today_str, "10:15")
        print(f"  [LLM] 完成，耗时 {time.time()-t0:.1f}s")

        rc, _, err = cli("append", stdin_text=entry_s1, env_extra=ENV)
        check(rc == 0, "append 退出码 0（LLM 输出通过 frontmatter 校验）", err.strip())

        day1_path = Path(journal_root) / today_str[:4] / today_str[5:7] / f"{today_str}.md"
        check(day1_path.exists(), "日文件已创建")

        if day1_path.exists():
            day1_text = day1_path.read_text(encoding="utf-8")
            blocks    = j.split_session_blocks(day1_text)
            check(len(blocks) == 1, "S1 后日文件有 1 块", f"got {len(blocks)}")
            b1 = blocks[0] if blocks else None

            check(b1 is not None and b1.fields.get("head") == c1.get("head"),
                  "head 字段与 collect JSON 一致（LLM 原样复制）",
                  f"entry head={b1.fields.get('head') if b1 else 'N/A'}, "
                  f"expected={c1.get('head')}")
            check(b1 is not None and isinstance(b1.fields.get("threads"), list),
                  "threads 是 list（LLM 遵守格式约束）")

            # 内容约束：commit hash 应出现在正文，不应出现完整 diff 关键词
            check(sha_validate[:7] in day1_text,
                  "validate commit hash 在正文中（LLM 引用了 hash）")
            check("@@" not in day1_text and "diff --git" not in day1_text,
                  "正文不含 diff 标记（LLM 没有贴 diff）")

            # 五槽位
            for slot in ("### 做成了什么", "### 关键决策 / 为什么",
                         "### 卡点 / 困惑", "### 下次 TODO", "### 碰到的 open thread"):
                check(slot in day1_text, f"槽位 '{slot}' 存在")

        # ════════════════════════════════════════════════════════════════
        # S2: Day1/下午 — --since 回退链（自动从 S1 head 出发）
        # ════════════════════════════════════════════════════════════════
        scenario("S2: Day1/下午 --since 回退链")

        sha_append  = repo.commit("journal.py", "# +append\n",
                                  "implement append + _write_append")
        sha_collect = repo.commit("journal.py", "# +collect\n",
                                  "implement collect (git delta, --since fallback)")

        rc, out, err = cli("collect", cwd=repo.path, env_extra=ENV)
        check(rc == 0, "collect 退出码 0", err.strip())
        c2 = json.loads(out) if rc == 0 else {}

        shas_s2 = {c["sha"] for c in c2.get("commits", [])}
        check(sha_append[:7]   in shas_s2, "append commit 在 S2 范围内",
              f"commits: {shas_s2}")
        check(sha_collect[:7]  in shas_s2, "collect commit 在 S2 范围内",
              f"commits: {shas_s2}")
        check(sha_validate[:7] not in shas_s2,
              "validate commit 不在 S2 范围（--since 回退链生效）",
              f"commits: {shas_s2}")
        check(len(c2.get("commits", [])) == 2,
              "S2 恰好只看到 2 个新 commit（append + collect）")

        print("  [LLM] 调用 Mimo 生成 S2 日记条目…", flush=True)
        t0 = time.time()
        entry_s2 = call_llm_for_entry(c2, SESSION_CONTEXTS["s2"],
                                       today_str, "15:40")
        print(f"  [LLM] 完成，耗时 {time.time()-t0:.1f}s")

        rc, _, err = cli("append", stdin_text=entry_s2, env_extra=ENV)
        check(rc == 0, "append 退出码 0", err.strip())

        if day1_path.exists():
            day1_text = day1_path.read_text(encoding="utf-8")
            blocks    = j.split_session_blocks(day1_text)
            check(len(blocks) == 2, "Day1 文件有 2 块（S1+S2）", f"got {len(blocks)}")
            check("\r\n" not in day1_text, "两次追加后无 CRLF")

            if len(blocks) >= 2:
                b2 = blocks[1]
                check(isinstance(b2.fields.get("threads"), list),
                      "S2 threads 是 list")
                check(sha_collect[:7] in day1_text,
                      "collect commit hash 出现在 Day1 文件")

        # ════════════════════════════════════════════════════════════════
        # S3: Day2（隔天补记）— 新日文件，LLM 生成
        # ════════════════════════════════════════════════════════════════
        scenario("S3: Day2 隔天补记（--date）")

        sha_threads  = repo.commit("journal.py",      "# +threads\n",
                                   "add threads command with CJK align")
        sha_selftest = repo.commit("test_journal.py", "# tests\n",
                                   "add selftest + integration tests (84/84)")

        rc, out, err = cli("collect", "--since", sha_collect,
                           cwd=repo.path, env_extra=ENV)
        check(rc == 0, "collect 退出码 0", err.strip())
        c3 = json.loads(out) if rc == 0 else {}

        shas_s3 = {c["sha"] for c in c3.get("commits", [])}
        check(sha_threads[:7]  in shas_s3, "threads commit 在 S3 范围内")
        check(sha_selftest[:7] in shas_s3, "selftest commit 在 S3 范围内")

        print("  [LLM] 调用 Mimo 生成 S3 日记条目…", flush=True)
        t0 = time.time()
        entry_s3 = call_llm_for_entry(c3, SESSION_CONTEXTS["s3"],
                                       yesterday_str, "09:30")
        print(f"  [LLM] 完成，耗时 {time.time()-t0:.1f}s")

        rc, _, err = cli("append", "--date", yesterday_str,
                         stdin_text=entry_s3, env_extra=ENV)
        check(rc == 0, "append --date 退出码 0", err.strip())

        yesterday_path = (Path(journal_root)
                         / yesterday_str[:4] / yesterday_str[5:7]
                         / f"{yesterday_str}.md")
        check(yesterday_path.exists(), "昨天日文件已创建")
        check(day1_path.exists(),      "今天日文件未被污染")

        if yesterday_path.exists():
            y_text  = yesterday_path.read_text(encoding="utf-8")
            y_blocks = j.split_session_blocks(y_text)
            check(len(y_blocks) == 1, "昨天日文件有 1 块")
            check(y_text.startswith(f"# {yesterday_str}"), "昨天文件头正确")

        # ════════════════════════════════════════════════════════════════
        # S4: threads 跨 session 聚合
        # ════════════════════════════════════════════════════════════════
        scenario("S4: threads 跨 session 聚合")

        rc, out, err = cli("threads", "--stale-days", "0", env_extra=ENV)
        check(rc == 0, "threads 退出码 0", err.strip())
        check("STALE" in out, "存在 STALE 标记（--stale-days=0）")

        # LLM 选择 thread 名有自由度，只验证有 thread 被收录
        thread_lines = [l for l in out.splitlines()
                        if l.strip() and "thread" not in l.lower()
                        and "出现" not in l and "首次" not in l]
        check(len(thread_lines) >= 2, "threads 表格至少 2 行（有实质内容）",
              f"output:\n{out}")

        # ════════════════════════════════════════════════════════════════
        # S5: 错误恢复 — 手工构造非法 frontmatter
        # ════════════════════════════════════════════════════════════════
        scenario("S5: 错误恢复（非法 frontmatter → 修正重试）")

        bad_entry = (
            f"## 16:00 · session\n\n"
            f"---\ndate: {today_str}\nproject: journal-skill\n"
            f"threads: forgot-list-syntax\n---\n\n"
            f"### 做成了什么\n- 无\n\n"
            f"### 关键决策 / 为什么\n- 无\n\n"
            f"### 卡点 / 困惑\n- 无\n\n"
            f"### 下次 TODO\n- 无\n\n"
            f"### 碰到的 open thread\n- 无\n"
        )
        rc, _, err = cli("append", stdin_text=bad_entry, env_extra=ENV)
        check(rc != 0, "非法 frontmatter 被拒（非零退出）")
        check(bool(err.strip()), "stderr 给出拒绝原因", f"err: {err.strip()}")

        # 日文件块数不变
        current_blocks = j.split_session_blocks(
            day1_path.read_text(encoding="utf-8")) if day1_path.exists() else []
        check(len(current_blocks) == 2, "append 失败后日文件块数不变（原子性）",
              f"got {len(current_blocks)}")

        # 修正后重试
        fixed_entry = bad_entry.replace(
            "threads: forgot-list-syntax",
            'threads: ["error-recovery"]'
        )
        rc, _, err = cli("append", stdin_text=fixed_entry, env_extra=ENV)
        check(rc == 0, "修正后 append 成功", err.strip())

        updated_blocks = j.split_session_blocks(
            day1_path.read_text(encoding="utf-8")) if day1_path.exists() else []
        check(len(updated_blocks) == 3,
              "修正后日文件有 3 块（S1+S2+修复块）", f"got {len(updated_blocks)}")

        # ════════════════════════════════════════════════════════════════
        # S6: 数据完整性
        # ════════════════════════════════════════════════════════════════
        scenario("S6: 数据完整性")

        all_md = list(Path(journal_root).rglob("*.md"))
        check(len(all_md) == 2, "共 2 个日文件（今天 + 昨天）",
              f"found: {[p.name for p in all_md]}")

        all_text = "\n".join(p.read_text(encoding="utf-8") for p in all_md)
        check("\r\n" not in all_text, "所有文件无 CRLF")
        check(all(b"\x00" not in p.read_bytes() for p in all_md),
              "所有文件无 null 字节（纯文本）")

        # sha_selftest 应出现在昨天文件里（LLM 引用了它）
        if yesterday_path.exists():
            check(sha_selftest[:7] in yesterday_path.read_text(encoding="utf-8"),
                  "selftest commit hash 出现在昨天日文件（LLM 正确引用 hash）")

    finally:
        repo.cleanup()

    # ════════════════════════════════════════════════════════════════════
    # 汇总
    # ════════════════════════════════════════════════════════════════════
    total  = len(_results)
    passed = sum(1 for ok, *_ in _results if ok)
    failed = total - passed

    print(f"\n{'═'*60}")
    print(f"端到端测试结果: {passed}/{total} 通过", end="")
    if failed:
        print(f"  ({failed} 失败)\n")
        for ok, label, detail in _results:
            if not ok:
                print(f"  ✗ {label}")
                if detail:
                    print(f"      {detail}")
    else:
        print("  全绿 ✅")
    print(f"{'═'*60}")
    return failed


if __name__ == "__main__":
    sys.exit(run_e2e())
