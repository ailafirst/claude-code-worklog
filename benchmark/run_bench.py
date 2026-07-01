#!/usr/bin/env python3
"""journal 端到端评测：collect+context → 被测模型写日记 → 机器校验 + 裁判评分。

每个 case 跑三段：
  1. 取料   —— build_case 重放真实 git 仓 + 跑真 collect（含 session_notes / --since）
  2. 被测   —— 把 SKILL 写作指南 + collect JSON + context 喂给 subject 模型，让它产出一个
               session 块（## 标题 + frontmatter + 五槽位）
  3. 评分   —— 机器层（用真 journal 引擎校验：能否解析成单块、frontmatter 是否合法、
               threads 是否命中、是否引 hash 不贴 diff）+ 裁判层（judge 模型对照
               expected-entry.md 的金标准与评分要点，输出 JSON 打分）

凭据：优先环境变量 BENCH_BASE_URL / BENCH_API_KEY / BENCH_MODEL，否则回退读 ../env.txt
      （第一行 base_url、第二行 api_key）。**API key 全程不打印、不写入任何报告。**

用法：
  py run_bench.py --list-models                 探测端点可用模型
  py run_bench.py 02-dead-ends --dry-run        只构造并打印 subject prompt，不调 API
  py run_bench.py 02-dead-ends --model <id>     单 case 端到端
  py run_bench.py --all --model <id>            全量评测，输出 .work/report-*.{json,md}
  py run_bench.py --all --model <id> --no-judge 只跑机器层（省 token）
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

BENCH      = Path(__file__).resolve().parent
REPO_ROOT  = BENCH.parent
SKILL_DIR  = REPO_ROOT / "dist" / "skills" / "journal"
SKILL_MD   = SKILL_DIR / "SKILL.md"
TEMPLATE   = SKILL_DIR / "templates" / "entry.md"

# 复用夹具构建器与真正的 journal 引擎（机器校验直接用线上解析器，避免另写一套）
sys.path.insert(0, str(BENCH))
sys.path.insert(0, str(SKILL_DIR))
import build_case as bc          # noqa: E402
import journal as J              # noqa: E402


# ════════════════════════════════════════════════════════════════════════════
# 凭据 + OpenAI 兼容客户端（stdlib，零依赖）
# ════════════════════════════════════════════════════════════════════════════

def load_creds(cli_base=None):
    base = cli_base or os.environ.get("BENCH_BASE_URL")
    key  = os.environ.get("BENCH_API_KEY")
    if not (base and key):
        envf = REPO_ROOT / "env.txt"
        if envf.exists():
            lines = [l.strip() for l in envf.read_text(encoding="utf-8").splitlines()
                     if l.strip()]
            if len(lines) >= 2:
                base = base or lines[0]
                key  = key  or lines[1]
    if not (base and key):
        sys.exit("缺少端点/密钥：设 BENCH_BASE_URL + BENCH_API_KEY，或在 ../env.txt 写两行")
    return base.rstrip("/"), key


def _http(url, key, payload=None, method="GET", timeout=180):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req  = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def list_models(base, key):
    try:
        data = _http(f"{base}/models", key)
    except urllib.error.HTTPError as e:
        sys.exit(f"列模型失败 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except Exception as e:
        sys.exit(f"列模型失败：{e}")
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    return ids


def _downgrade_rf(rf):
    """端点拒绝某档 response_format 时逐级降档：json_schema → json_object → 去掉。"""
    if rf and rf.get("type") == "json_schema":
        return {"type": "json_object"}
    return None


def chat(base, key, model, system, user, temperature=0.0, retries=2,
         max_tokens=2048, examples=None, response_format=None):
    """调一次 chat/completions，返回正文字符串；失败抛 RuntimeError。

    examples: 可选的 few-shot 消息列表（[{role,content}, ...]），插在 system 与最终 user 之间。
    response_format: 可选的结构化输出约束（json_schema/json_object）；端点 400 时自动降档。
    """
    msgs = [{"role": "system", "content": system}]
    if examples:
        msgs.extend(examples)
    msgs.append({"role": "user", "content": user})
    payload = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format
    last = None
    for attempt in range(retries + 1):
        try:
            data    = _http(f"{base}/chat/completions", key, payload, method="POST")
            choice  = data["choices"][0]
            msg     = choice.get("message", {})
            content = (msg.get("content") or "").strip()
            if not content:
                # 推理模型：content 为空时正文可能落在 reasoning_content；
                # 多半是 max_tokens 把预算耗在思考上（finish_reason=length）
                content = (msg.get("reasoning_content") or "").strip()
            if not content:
                fr = choice.get("finish_reason")
                raise RuntimeError(f"空响应（finish_reason={fr}，"
                                   f"可能 max_tokens={max_tokens} 不够推理模型用）")
            return content
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code}: {body}"
            # 端点不认这档 response_format → 降档后立刻重试，不浪费退避预算
            if e.code == 400 and "response_format" in payload:
                dg = _downgrade_rf(payload["response_format"])
                if dg:
                    payload["response_format"] = dg
                else:
                    payload.pop("response_format")
                continue
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            break
        except Exception as e:
            last = str(e)
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            break
    raise RuntimeError(last or "chat 调用失败")


# ════════════════════════════════════════════════════════════════════════════
# prompt 构造
# ════════════════════════════════════════════════════════════════════════════

def subject_system():
    """被测模型的系统提示 = 真·SKILL 写作指南 + 真·模板，保证测的是线上 skill。"""
    skill = SKILL_MD.read_text(encoding="utf-8") if SKILL_MD.exists() else ""
    tmpl  = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else ""
    return (
        "你是 Claude Code 的 journal skill 在 capture 模式下的执行者。下面是该 skill 的完整"
        "说明书与条目模板。请严格按其『怎么写出高信号条目』的要求工作。\n\n"
        "本次任务：根据给到的 collect JSON 与 session 上下文，产出**一个** session 块，"
        "可直接交给 append 落盘。\n"
        "硬性输出约定：\n"
        "  - 只输出 session 块本身（`## HH:MM · session` 起，含 frontmatter 与五个槽位），"
        "不要任何前后解释、不要代码围栏、不要执行任何命令。\n"
        "  - frontmatter 的 date 用今天；head 留空（append 会回填）；threads 用稳定短标签。\n"
        "  - commit 只引 7 位 hash + 文件名，绝不贴 diff 或复述 stats。\n\n"
        "==== SKILL.md ====\n" + skill +
        "\n\n==== templates/entry.md ====\n" + tmpl
    )


def subject_user(collect, context, today):
    return (
        f"今天是 {today}。\n\n"
        "## collect 输出（git 原料，确定性层已采好）\n```json\n"
        + json.dumps(collect, ensure_ascii=False, indent=2) +
        "\n```\n\n"
        "## session 上下文（git 看不到的推理，务必据此写出增量信息）\n"
        + context +
        "\n\n现在产出该 session 块。"
    )


JUDGE_SYSTEM = (
    "你是严格、苛刻、可被审计的评测裁判，给一份『工作日记条目』打分。日记的唯一立意是：记 git "
    "拿不到、session 结束就蒸发的推理——为什么这么改、试过哪条死路、卡在哪、下次第一步、碰到的 "
    "open thread。**绝不是**把 commit 标题 / diff / stats 换行转写。\n\n"
    "输入给你：本 case 考点、金标准条目与评分要点（含『必中项』与『anti-pattern』，有的标了"
    "『命门/直接判失败』）、以及被测条目。\n\n"
    "## 强制评审顺序（必须按此产出，不得跳步）\n"
    "1. must_hit：逐条核对评分要点里的每个必中项。命中则 hit=true 且在 evidence 引被测条目原文片段；"
    "未命中则 hit=false 且在 evidence 点出缺口。**不许凭印象，必须逐条列全。**\n"
    "2. anti_patterns：逐条核对每个 anti-pattern（贴 diff、复述 +/- 行数等 stats、把 commit 标题原样"
    "转写成正文、正文相对 git log 无任何增量信息）。triggered + evidence。\n"
    "3. thread_label_quality：单列一维。threads 是否非空、且是稳定可跨 session 复用的短标签（与本 case "
    "既定线索名一致或等价）。为空、含糊（如 cursor-stuff/misc）、写成一句话、或明显漂移成近义新名 → "
    "stable_reusable=false，并在 note 说明为何会断跨 session 聚合。\n"
    "4. deductions：把上面每一处不足都换算成一条扣分账 {reason, points}。一条问题一条账，写清扣几分。\n"
    "5. score = 10 − Σ(deductions.points)，下限 0。**score 必须等于这个算式，禁止脱离扣分账自由打分。**\n"
    "6. verdict：命门项未命中 → fail；score≤4 → fail；否则 pass。\n\n"
    "## 扣分标度（严格执行）\n"
    "- 满分 10 是稀缺的：仅当①全部必中项命中 ②零 anti-pattern ③thread 标签稳定可复用 ④你确实指不出"
    "任何一处可改进。**只要你能说出哪怕一句『这里本可更好』，就必须落成一条 deduction；deductions 为空"
    "才允许 10 分。**\n"
    "- 每漏 1 个普通必中项：−2~−3。\n"
    "- 每触发 1 个 anti-pattern：−3~−4。\n"
    "- thread 标签为空或不稳定（会断跨 session 聚合）：−2~−3。\n"
    "- 命门项未命中：直接 verdict=fail 且把对应 deduction 扣到 score≤3。\n"
    "- 正文相对 git log 零增量（只复述 commit 已记录的内容）：这是总闸 anti-pattern，judge=fail。\n\n"
    "## 输出格式硬约束\n"
    "只输出一个 JSON 对象，符合给定 schema，不要任何解释性前后文、不要代码围栏。"
    "evidence/note/rationale 里引用原文时**只摘短片段、不要带双引号和换行**（必要时改述或用「」"
    "代替），确保整段 JSON 合法可解析。"
)

# 结构化输出：强约束裁判回 JSON，杜绝散文式回避。端点不支持时 chat() 自动降档到 json_object。
JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["must_hit", "anti_patterns", "thread_label_quality",
                 "deductions", "score", "verdict", "rationale"],
    "properties": {
        "must_hit": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["item", "hit", "evidence"],
                "properties": {
                    "item":     {"type": "string"},
                    "hit":      {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "anti_patterns": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["item", "triggered", "evidence"],
                "properties": {
                    "item":      {"type": "string"},
                    "triggered": {"type": "boolean"},
                    "evidence":  {"type": "string"},
                },
            },
        },
        "thread_label_quality": {
            "type": "object", "additionalProperties": False,
            "required": ["stable_reusable", "note"],
            "properties": {
                "stable_reusable": {"type": "boolean"},
                "note":            {"type": "string"},
            },
        },
        "deductions": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["reason", "points"],
                "properties": {
                    "reason": {"type": "string"},
                    "points": {"type": "number"},   # 允许 0.5 这类小扣分，不被 int() 抹平
                },
            },
        },
        "score":     {"type": "number", "minimum": 0, "maximum": 10},
        "verdict":   {"type": "string", "enum": ["pass", "fail"]},
        "rationale": {"type": "string"},
    },
}

JUDGE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "journal_judge", "strict": True, "schema": JUDGE_SCHEMA},
}


def judge_user(case, expected_md, entry):
    return (
        f"## 本 case 考点\n{case.get('tests','')}\n\n"
        f"## 金标准条目 + 评分要点\n{expected_md}\n\n"
        f"## 被测条目\n{entry}\n\n"
        "按强制评审顺序逐条核对，并按 schema 输出评分 JSON。"
    )


# ── few-shot：用合成（非真 case，杜绝泄题）样例锚定标度 ──────────────────────
# A：只复述 commit、threads 空、零推理 → 命门触发、扣到 0、fail。
# B：信号很高但 thread 标签含糊 → 仍扣 1 分得 9，示范『满分稀缺、必itemize 扣分』。
_FEWSHOT_A_USER = judge_user(
    {"tests": "WebSocket 重连退避的选型与踩坑"},
    "金标准要点（必中）：\n"
    "- 必中：写清为什么用指数退避而非固定间隔（固定间隔会在服务端抖动时同步重连放大风暴）\n"
    "- 必中：记录死路——先试固定 1s 重连被服务端限流\n"
    "- 必中：threads 含稳定标签 ws-reconnect\n"
    "- anti-pattern：把 commit 标题原样转写成正文；贴 diff/stats\n"
    "- 命门：正文相对 git log 无任何增量信息则直接 fail",
    "## 14:20 · session\n---\ndate: 2026-06-30\nproject: realtime\nthreads: []\n---\n"
    "## 做成了什么\n实现了重连逻辑，提交 a1b2c3d。\n## 关键决策·为什么\n无\n"
    "## 卡点·困惑\n无\n## 下次 TODO\n无\n## 碰到的 open thread\n无",
)
_FEWSHOT_A_ASSISTANT = json.dumps({
    "must_hit": [
        {"item": "为什么用指数退避而非固定间隔", "hit": False,
         "evidence": "『关键决策·为什么』写『无』，决策理由完全缺失"},
        {"item": "死路：固定 1s 重连被限流", "hit": False,
         "evidence": "『卡点·困惑』为『无』，未记录任何试错"},
        {"item": "threads 含稳定标签 ws-reconnect", "hit": False,
         "evidence": "threads: [] 为空"},
    ],
    "anti_patterns": [
        {"item": "把 commit 标题原样转写成正文", "triggered": True,
         "evidence": "『实现了重连逻辑，提交 a1b2c3d』即 commit 标题转写，相对 git log 零增量"},
        {"item": "贴 diff/stats", "triggered": False, "evidence": "未出现 diff 或行数统计"},
    ],
    "thread_label_quality": {"stable_reusable": False,
                             "note": "threads 为空，无法跨 session 聚合该工作线"},
    "deductions": [
        {"reason": "漏决策理由（为什么指数退避）", "points": 3},
        {"reason": "漏死路（固定 1s 被限流）", "points": 2},
        {"reason": "threads 为空，断跨 session 聚合", "points": 2},
        {"reason": "命门：正文只转写 commit、相对 git log 零增量", "points": 3},
    ],
    "score": 0, "verdict": "fail",
    "rationale": "条目只复述了 git 已记录的内容，为什么/死路/下一步等 git 拿不到的推理全缺，命门触发，判失败。",
}, ensure_ascii=False)

_FEWSHOT_B_USER = judge_user(
    {"tests": "给 API 加游标分页"},
    "金标准要点（必中）：\n"
    "- 必中：写清为什么用游标而非 offset 分页（offset 深翻页全表扫描、并发写入会漏/重数据）\n"
    "- 必中：open thread——游标签名密钥轮换还没定\n"
    "- 必中：threads 含稳定标签 api-pagination\n"
    "- anti-pattern：贴 diff/stats；commit 标题转写",
    "## 10:05 · session\n---\ndate: 2026-06-30\nproject: api\nthreads:\n  - cursor-stuff\n---\n"
    "## 做成了什么\n给 /items 加游标分页，提交 e4f5a6b（handlers.py, pagination.py）。\n"
    "## 关键决策·为什么\n用游标而非 offset——offset 深翻页要全表扫描，并发写入还会漏行/重复行；"
    "游标用 (created_at, id) 复合键稳定翻页。\n## 卡点·困惑\n无\n"
    "## 下次 TODO\n给游标加签名防客户端篡改\n## 碰到的 open thread\n游标签名密钥怎么轮换还没定。",
)
_FEWSHOT_B_ASSISTANT = json.dumps({
    "must_hit": [
        {"item": "为什么用游标而非 offset", "hit": True,
         "evidence": "『offset 深翻页要全表扫描，并发写入还会漏行/重复行』"},
        {"item": "open thread：游标签名密钥轮换未定", "hit": True,
         "evidence": "『游标签名密钥怎么轮换还没定』"},
        {"item": "threads 含稳定标签 api-pagination", "hit": False,
         "evidence": "实得 cursor-stuff，含糊、非金标准稳定标签"},
    ],
    "anti_patterns": [
        {"item": "贴 diff/stats", "triggered": False, "evidence": "只引 hash+文件名，无行数"},
        {"item": "commit 标题转写", "triggered": False, "evidence": "正文补足了 git 拿不到的决策理由"},
    ],
    "thread_label_quality": {"stable_reusable": False,
                             "note": "cursor-stuff 含糊，下次同线索易换名（如 pagination/cursor-fix）导致聚合断线，应统一为 api-pagination"},
    "deductions": [
        {"reason": "thread 标签 cursor-stuff 含糊、非稳定复用名", "points": 1},
    ],
    "score": 9, "verdict": "pass",
    "rationale": "决策理由、open thread、不贴 diff 都到位，信号很高；唯 thread 标签含糊，扣 1 分提醒复用稳定标签。",
}, ensure_ascii=False)

JUDGE_FEWSHOT = [
    {"role": "user",      "content": _FEWSHOT_A_USER},
    {"role": "assistant", "content": _FEWSHOT_A_ASSISTANT},
    {"role": "user",      "content": _FEWSHOT_B_USER},
    {"role": "assistant", "content": _FEWSHOT_B_ASSISTANT},
]


class JudgeParseError(Exception):
    """裁判回复无法解析成 JSON 时抛出，携带原始文本便于排查。"""
    def __init__(self, msg, raw):
        super().__init__(msg)
        self.raw = raw


def reconcile_score(v):
    """以扣分账为权威：score := 10 − Σ(deductions.points)，杜绝『列了一堆问题却仍打满分』。

    保留模型自报分到 score_self 以便审计两者是否一致。
    """
    pts = 0.0
    for d in v.get("deductions") or []:
        p = d.get("points")
        if isinstance(p, bool):           # bool 是 int 子类，单独排除
            continue
        if isinstance(p, (int, float)):
            pts += p                       # 保留 0.5 这类小扣分，不再 int() 抹平
    computed = round(max(0.0, 10 - pts), 1)
    if computed == int(computed):          # 整数就回退成 int，显示 9 而非 9.0
        computed = int(computed)
    v["score_self"] = v.get("score")
    v["score_from_deductions"] = computed
    v["score"] = computed
    if computed <= 4:
        v["verdict"] = "fail"
    return v


def run_judge(base, key, model, case, expected_md, entry,
              temperature=0.0, max_tokens=6000, parse_retries=2):
    """跑一次裁判：结构化输出 + few-shot + 扣分账对账。返回 (verdict_dict, raw)。

    推理模型偶发吐出非法 JSON（裸引号/控制符）；temp=0 下重采仍有差异，故解析失败重试。
    全部失败才抛 JudgeParseError（带最后一次 raw）。run_case 与 rejudge 共用此入口。
    """
    user = judge_user(case, split_expected(expected_md), entry)
    last_raw, last_err = "", None
    for attempt in range(parse_retries + 1):
        raw = chat(base, key, model, JUDGE_SYSTEM, user, temperature,
                   max_tokens=max_tokens, examples=JUDGE_FEWSHOT,
                   response_format=JUDGE_RESPONSE_FORMAT)
        last_raw = raw
        try:
            verdict = parse_judge_json(raw)
        except Exception as e:
            last_err = e
            continue
        reconcile_score(verdict)
        return verdict, raw
    raise JudgeParseError(str(last_err), last_raw)


# ════════════════════════════════════════════════════════════════════════════
# 机器层校验（复用真 journal 引擎）
# ════════════════════════════════════════════════════════════════════════════

def machine_checks(entry, case, collect):
    checks = []     # (name, passed, detail)

    def add(name, ok, detail=""):
        checks.append({"name": name, "pass": bool(ok), "detail": detail})

    # 1. 能解析成单一 session 块
    try:
        blk = J.parse_single_block(entry)
        n_heads = sum(1 for l in entry.splitlines() if l.startswith("## "))
        add("解析为单一 session 块", n_heads >= 1, f"H2 标题数={n_heads}")
    except Exception as e:
        add("解析为单一 session 块", False, str(e))
        return checks, None

    # 2. frontmatter 通过内置 verify（这是 append 真正的入库闸）
    try:
        J.validate(blk.fields)
        add("frontmatter 通过 append 校验", True)
    except J.ValidationError as e:
        add("frontmatter 通过 append 校验", False, str(e))

    # 3. threads：先看是否丢线（严重），再看是否精确命中（关乎跨 session 聚合，偏严）
    exp = set(case.get("expected_threads") or [])
    got = set(blk.fields.get("threads") or [])
    if exp:
        add("threads 非空（期望非空）", bool(got), f"实得 {sorted(got)}")
    add(f"threads 精确命中 {sorted(exp)}", got == exp,
        f"实得 {sorted(got)}（命名漂移不影响本次质量，但跨 session 聚合会断线）")

    # 4. 有 commit 时引用了某个真实 7 位 hash（不贴 diff 的正面证据）
    shas = [c["sha"] for c in collect.get("commits", [])]
    if shas:
        hit = any(s in entry for s in shas)
        add("引用真实 commit hash", hit, f"应含其一: {shas}")

    # 5. 没有粘贴 diff / stats（反面证据）
    diff_lines = len(re.findall(r"(?m)^\s*[+-]\d", entry)) + entry.count("diff --git")
    statish    = bool(re.search(r"\+\d+\s*[-/]\s*\d+", entry))
    add("未粘贴 diff/统计数字", diff_lines == 0 and not statish,
        f"疑似 diff 行={diff_lines} statish={statish}")

    return checks, blk


def critical_machine_ok(checks):
    """门槛：能解析 + 过校验。其余为质量信号，不一票否决。"""
    crit = {"解析为单一 session 块", "frontmatter 通过 append 校验"}
    return all(c["pass"] for c in checks if c["name"] in crit)


# ════════════════════════════════════════════════════════════════════════════
# 单 case 流程
# ════════════════════════════════════════════════════════════════════════════

def split_expected(expected_md):
    """expected-entry.md 已含金标准块 + 评分要点，整体喂裁判即可。"""
    return expected_md.strip()


def parse_judge_json(raw):
    """解析裁判回复。结构化输出下整段 content 就是 JSON，优先直接 loads（避免下面
    括号启发式在 evidence 引号/嵌套上踩坑）；失败再退回 extract_json 抠第一个对象。
    strict=False 容忍字符串里夹的裸换行/制表符（推理模型偶发）。"""
    t = raw.strip()
    try:
        return json.loads(t, strict=False)
    except Exception:
        return extract_json(t)


def extract_json(text):
    """从模型回复里抠出第一个平衡的 JSON 对象。"""
    s = text.find("{")
    if s < 0:
        raise ValueError("回复中无 JSON")
    depth, instr, esc = 0, False, False
    for i in range(s, len(text)):
        ch = text[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        else:
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[s:i + 1], strict=False)
    raise ValueError("JSON 不闭合")


def strip_fences(entry):
    """模型若误加 ```markdown 围栏，剥掉，保留纯块。"""
    t = entry.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def run_case(case_id, cfg):
    case = bc.load_case(case_id)
    ctx_path = bc.CASES_DIR / case_id / "context.md"
    exp_path = bc.CASES_DIR / case_id / "expected-entry.md"
    context  = ctx_path.read_text(encoding="utf-8") if ctx_path.exists() else ""
    expected = exp_path.read_text(encoding="utf-8") if exp_path.exists() else ""

    # 1. 取料：真重放 + 真 collect
    wd, repo, jroot, _ = bc.materialize(case)
    rc, out, err = bc.run_collect(repo, jroot)
    if rc != 0:
        return {"case": case_id, "title": case.get("title"),
                "error": f"collect 退出码 {rc}: {err.strip()}"}
    collect = json.loads(out)

    today = date.today().isoformat()
    rec = {"case": case_id, "title": case.get("title"),
           "collect": collect, "subject_model": cfg["subject_model"]}

    sys_prompt  = subject_system()
    user_prompt = subject_user(collect, context, today)

    if cfg["dry_run"]:
        rec["dry_run_prompt"] = user_prompt
        return rec

    # 2. 被测模型写条目
    try:
        raw = chat(cfg["base"], cfg["key"], cfg["subject_model"],
                   sys_prompt, user_prompt, cfg["temperature"],
                   max_tokens=cfg["max_tokens"])
    except Exception as e:
        rec["error"] = f"subject 调用失败：{e}"
        return rec
    entry = strip_fences(raw)
    rec["entry"] = entry

    # 3a. 机器层
    checks, _ = machine_checks(entry, case, collect)
    rec["machine_checks"] = checks
    rec["machine_pass"]   = sum(1 for c in checks if c["pass"])
    rec["machine_total"]  = len(checks)
    rec["machine_critical_ok"] = critical_machine_ok(checks)

    # 3b. 裁判层（结构化输出 + few-shot + 扣分账对账，统一走 run_judge）
    if cfg["no_judge"]:
        return rec
    rec["judge_model"] = cfg["judge_model"]
    try:
        verdict, _ = run_judge(cfg["base"], cfg["key"], cfg["judge_model"],
                               case, expected, entry,
                               cfg["temperature"], cfg["max_tokens"])
        rec["judge"] = verdict
        # 机器门槛未过 → 强制判负，无论裁判多宽容
        if not rec["machine_critical_ok"] and verdict.get("verdict") == "pass":
            verdict["verdict"] = "fail"
            verdict["rationale"] = "[机器门槛未过：解析/校验失败] " + verdict.get("rationale", "")
            verdict["score"] = min(int(verdict.get("score", 0)), 3)
    except JudgeParseError as e:
        rec["judge_error"] = f"judge JSON 解析失败：{e}"
        rec["judge_raw"]   = e.raw[:4000]   # 留底便于排查
    except Exception as e:
        rec["judge_error"] = f"judge 调用失败：{e}"
    return rec


# ════════════════════════════════════════════════════════════════════════════
# 报告
# ════════════════════════════════════════════════════════════════════════════

def write_reports(records, cfg, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d-%H%M%S")
    jpath = out_dir / f"report-{ts}.json"
    mpath = out_dir / f"report-{ts}.md"

    meta = {"timestamp": ts, "subject_model": cfg["subject_model"],
            "judge_model": cfg["judge_model"] if not cfg["no_judge"] else None,
            "base_url": cfg["base"], "temperature": cfg["temperature"]}
    jpath.write_text(json.dumps({"meta": meta, "records": records},
                                ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# journal benchmark 报告 — {ts}", "",
             f"- subject: `{cfg['subject_model']}`",
             f"- judge: `{cfg['judge_model'] if not cfg['no_judge'] else '(跳过)'}`",
             f"- endpoint: `{cfg['base']}`  temperature: {cfg['temperature']}", "",
             "| case | 机器 | 裁判分 | 判定 | 备注 |",
             "|------|------|--------|---------|------|"]
    scores, passes, judged = [], 0, 0
    for r in records:
        if r.get("error"):
            lines.append(f"| {r['case']} | — | — | 错误 | {r['error'][:60]} |")
            continue
        mc = f"{r.get('machine_pass','?')}/{r.get('machine_total','?')}"
        j  = r.get("judge") or {}
        sc = j.get("score", "—")
        vd = j.get("verdict", "—" if not r.get("judge_error") else "裁判出错")
        note = (j.get("rationale", "") or r.get("judge_error", ""))[:70]
        if isinstance(sc, (int, float)):
            scores.append(sc); judged += 1
            if vd == "pass":
                passes += 1
        lines.append(f"| {r['case']} | {mc} | {sc} | {vd} | {note} |")

    lines += ["", "## 汇总"]
    if judged:
        lines.append(f"- 通过率：{passes}/{judged}")
        lines.append(f"- 平均裁判分：{sum(scores)/len(scores):.1f}/10")
    lines.append(f"- 机器门槛通过："
                 f"{sum(1 for r in records if r.get('machine_critical_ok'))}/"
                 f"{sum(1 for r in records if 'machine_critical_ok' in r)}")

    # 逐 case 细节（条目 + 必中项核对）
    lines += ["", "## 逐 case 细节"]
    for r in records:
        lines += ["", f"### {r['case']} — {r.get('title','')}"]
        if r.get("error"):
            lines.append(f"错误: {r['error']}"); continue
        if r.get("dry_run_prompt"):
            lines.append("(dry-run，未调用模型)"); continue
        for c in r.get("machine_checks", []):
            mark = "✓" if c["pass"] else "✗"
            lines.append(f"- [{mark}] {c['name']} {('— ' + c['detail']) if c['detail'] else ''}")
        j = r.get("judge")
        if j:
            sc, self_sc = j.get("score"), j.get("score_self")
            drift = f"（模型自报 {self_sc}，已按扣分账对齐）" if (
                self_sc is not None and self_sc != sc) else ""
            lines.append(f"- 裁判 {sc}/10{drift} · {j.get('verdict')} — {j.get('rationale','')}")
            tlq = j.get("thread_label_quality") or {}
            if tlq:
                mark = "稳定可复用" if tlq.get("stable_reusable") else "不稳/含糊"
                lines.append(f"    - thread 标签：{mark} — {tlq.get('note','')}")
            for d in j.get("deductions", []) or []:
                lines.append(f"    - −{d.get('points')} {d.get('reason','')}")
            for m in j.get("must_hit", []):
                mark = "✓" if m.get("hit") else "✗"
                lines.append(f"    - 必中[{mark}] {m.get('item','')}")
            for a in j.get("anti_patterns", []):
                if a.get("triggered"):
                    lines.append(f"    - ⚠ 触发 anti-pattern: {a.get('item','')}")
        lines += ["", "<details><summary>被测条目</summary>", "",
                  "```markdown", r.get("entry", "").strip(), "```", "</details>"]

    mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jpath, mpath


# ════════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="journal 端到端评测")
    ap.add_argument("cases", nargs="*", help="case id（留空配 --all）")
    ap.add_argument("--all", action="store_true", help="跑全部 case")
    ap.add_argument("--model", help="同时设 subject 与 judge 模型")
    ap.add_argument("--subject-model", help="被测模型 id")
    ap.add_argument("--judge-model", help="裁判模型 id")
    ap.add_argument("--base-url", help="覆盖端点（默认环境变量/env.txt）")
    ap.add_argument("--temperature", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=6000,
                    help="单次 completion 上限；推理模型要留足思考预算（默认 6000）")
    ap.add_argument("--no-judge", action="store_true", help="只跑机器层，省 token")
    ap.add_argument("--dry-run", action="store_true", help="只构造 prompt 不调 API")
    ap.add_argument("--list-models", action="store_true", help="列端点可用模型后退出")
    ap.add_argument("--out", default=str(BENCH / ".work"), help="报告输出目录")
    args = ap.parse_args()

    base, key = (None, None)
    if not args.dry_run:
        base, key = load_creds(args.base_url)

    if args.list_models:
        for m in list_models(base, key):
            print(m)
        return

    ids = bc.list_case_ids() if args.all else args.cases
    if not ids:
        ap.error("指定 case id 或用 --all")

    subject_model = args.subject_model or args.model or os.environ.get("BENCH_MODEL")
    judge_model   = args.judge_model   or args.model or os.environ.get("BENCH_MODEL")
    if not args.dry_run and not subject_model:
        ap.error("需 --model 或 --subject-model（可先 --list-models 查询）")

    cfg = {"base": base, "key": key, "subject_model": subject_model,
           "judge_model": judge_model, "temperature": args.temperature,
           "max_tokens": args.max_tokens,
           "no_judge": args.no_judge, "dry_run": args.dry_run}

    print(f"\n=== 评测 {len(ids)} 个 case ===")
    if not args.dry_run:
        print(f"subject={subject_model}  judge={'(跳过)' if args.no_judge else judge_model}"
              f"  endpoint={base}  (api key 已加载，隐藏)\n")

    records = []
    for cid in ids:
        print(f"  ▶ {cid} ...", flush=True)
        r = run_case(cid, cfg)
        records.append(r)
        if r.get("error"):
            print(f"      错误: {r['error'][:80]}")
        elif args.dry_run:
            print("      [dry-run] prompt 已构造")
        else:
            mc = f"{r.get('machine_pass','?')}/{r.get('machine_total','?')}"
            j  = r.get("judge") or {}
            print(f"      机器 {mc}  裁判 {j.get('score','—')}/10 {j.get('verdict','')}"
                  + (f"  [裁判出错]" if r.get("judge_error") else ""))

    if args.dry_run:
        for r in records:
            print("\n" + "=" * 70 + f"\n# {r['case']} subject prompt\n" + "=" * 70)
            print(r.get("dry_run_prompt", "(无)"))
        return

    out_dir = Path(args.out)
    jpath, mpath = write_reports(records, cfg, out_dir)
    print(f"\n报告已写入：\n  {jpath}\n  {mpath}")


if __name__ == "__main__":
    main()
