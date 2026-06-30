# journal — 跨 session 工作日记 skill

一个 [Claude Code](https://claude.com/claude-code) skill：把一次开发 session 写成**高信号工作日记**，
记 `git` 留不住、session 一结束就蒸发的推理层信息。

> **立意：日记记的是 git 丢掉的东西，不是 git 已经记下的东西。**
> `git log` 已经说清"改了什么"；这个 skill 只补 git 拿不到的部分——**为什么这么改、试过哪条死路、
> 卡在哪、下次第一步、还有哪些 open thread**。把 commit 标题换行抄成正文，就是失败。

## 设计约束

- **纯 Python 3 标准库**，零 pip 依赖（含 frontmatter 解析也自控，不引 PyYAML）。
- **存储全是纯 markdown**：不引数据库、不引私有 JSON。脱离 skill 也能 `grep` / `diff` / 直接读。
- 确定性的活（日期→路径、frontmatter 读写、git delta、thread 聚合）沉进 `journal.py`；
  需要判断力的活（把 session 写成高信号条目）留给运行时模型。

## 仓库结构

```
.
├── dist/                       # 可安装产物（install.py 会把它复制到 ~/.claude/）
│   ├── skills/journal/
│   │   ├── SKILL.md            # skill 触发条件 + capture 工作流
│   │   ├── journal.py          # 确定性引擎（单入口 + 子命令）
│   │   ├── templates/entry.md  # 条目模板
│   │   └── test_journal.py     # 引擎单元测试
│   └── commands/journal.md     # /journal slash command
├── install.py                  # 安装脚本（复制 dist → ~/.claude，可选配 SessionEnd hook）
├── test_e2e.py                 # 端到端集成测试（固定 git + 真实 LLM 跑通管道）
├── benchmark/                  # 评测套件：12 个真 git 夹具 + 端到端 LLM 评测（详见其 README）
├── journal-skill-spec.md       # 完整规格说明书
└── CLAUDE.md                   # 给 Claude Code 的项目指引
```

## 安装

需要 Python 3.8+。在仓库根目录：

```
py install.py            # 复制 dist/ → ~/.claude/，验证 selftest，并提示 hook 配置
py install.py --hook     # 同上，并自动写入 SessionEnd（Stop）hook 实现自动 snapshot
py install.py --check    # 只验证已安装文件是否健康，不改任何东西
```

安装后产物落在 Claude Code 的发现路径：

- skill → `~/.claude/skills/journal/`
- command → `~/.claude/commands/journal.md`
- 日记数据 → `~/.claude/journal/YYYY/MM/YYYY-MM-DD.md`

## 用法

```
/journal             记录本次 session（capture）
/journal threads     查看 open / 停滞的 thread
/journal rollup      生成本周蒸馏
! py ~/.claude/skills/journal/journal.py note -m "随手记一句"
```

`journal.py` 子命令：`collect`（采 git 原料）、`append`（入库一个 session 块，带校验）、
`note` / `snapshot` / `threads` / `rollup` / `path` / `selftest`。
根目录默认 `~/.claude/journal/`，可用环境变量 `JOURNAL_ROOT` 覆盖（测试用临时目录靠它）。

## 测试与评测

```
py dist/skills/journal/test_journal.py   # 引擎单元测试（确定性层，零依赖）
py benchmark/build_case.py --all         # 12 个真 git 夹具的确定性层自检
py benchmark/run_bench.py --all --model <id>   # 端到端 LLM 评测（机器层 + 裁判层）
```

端到端评测需要 LLM 端点凭据：环境变量 `BENCH_BASE_URL` / `BENCH_API_KEY`，或在仓库根写 `env.txt`
（第一行 base_url、第二行 api_key）。**`env.txt` 已被 `.gitignore` 排除，绝不入库。**
评测设计与 12 个 case 的覆盖矩阵见 [`benchmark/README.md`](benchmark/README.md)。

## 许可

[MIT License](LICENSE)。
