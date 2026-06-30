#!/usr/bin/env python3
"""
journal skill 安装脚本。

将 dist/ 下的文件复制到 ~/.claude/，可选配置 SessionEnd hook（自动 snapshot）。
用法：  py install.py          # 安装 + 提示 hook 配置
        py install.py --hook   # 安装 + 自动写入 settings.json hook
        py install.py --check  # 只验证，不修改任何文件
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass

# ── 路径常量 ─────────────────────────────────────────────────────────────
REPO      = Path(__file__).parent.resolve()
DIST      = REPO / "dist"
SKILL_SRC = DIST / "skills" / "journal"
CMD_SRC   = DIST / "commands" / "journal.md"
CLAUDE    = Path.home() / ".claude"
SKILL_DST = CLAUDE / "skills" / "journal"
CMD_DST   = CLAUDE / "commands" / "journal.md"
PYTHON    = sys.executable  # 复用当前解释器（解决 Windows py/python3 歧义）


def step(msg):   print(f"  {msg}")
def ok(msg):     print(f"  [OK]   {msg}")
def warn(msg):   print(f"  [WARN] {msg}")
def fail(msg):   print(f"  [FAIL] {msg}"); sys.exit(1)


# ── 各步骤 ───────────────────────────────────────────────────────────────

def check_python():
    if sys.version_info < (3, 8):
        fail(f"需要 Python 3.8+，当前 {sys.version}")
    ok(f"Python {sys.version.split()[0]}")


def check_sources():
    missing = [p for p in (SKILL_SRC, CMD_SRC) if not p.exists()]
    if missing:
        fail(f"源文件缺失：{missing}（请在项目根目录运行）")
    ok(f"源目录确认：{SKILL_SRC.relative_to(REPO)}")


def copy_files():
    SKILL_DST.mkdir(parents=True, exist_ok=True)
    (CLAUDE / "commands").mkdir(parents=True, exist_ok=True)

    for src in sorted(SKILL_SRC.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts or src.suffix == ".pyc":
            continue
        rel = src.relative_to(SKILL_SRC)
        dst = SKILL_DST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        step(f"copied  skills/journal/{rel}")

    shutil.copy2(CMD_SRC, CMD_DST)
    step(f"copied  commands/journal.md")


def verify():
    r = subprocess.run([PYTHON, str(SKILL_DST / "journal.py"), "--help"],
                       capture_output=True)
    if r.returncode != 0:
        fail("journal.py --help 失败，安装可能不完整")
    r2 = subprocess.run([PYTHON, str(SKILL_DST / "journal.py"), "selftest"],
                        capture_output=True, text=True, encoding="utf-8")
    if r2.returncode != 0:
        warn("selftest 有失败：\n" + r2.stdout + r2.stderr)
    else:
        ok("journal.py selftest 全绿")


def configure_hook(auto=False):
    """向 ~/.claude/settings.json 写入 Stop hook（auto=True 时静默写入）。"""
    settings_path = CLAUDE / "settings.json"

    # 构造 hook 命令字符串（使用当前 Python 解释器绝对路径）
    hook_cmd = f'"{PYTHON}" "{SKILL_DST / "journal.py"}" snapshot'

    if not settings_path.exists():
        if auto:
            # 从头创建最小 settings.json
            settings_path.write_text(
                json.dumps({"hooks": {"Stop": [
                    {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]}
                ]}}, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            ok("settings.json 已创建并写入 Stop hook")
        else:
            warn("settings.json 不存在，跳过自动配置（手动配置见下）")
        return

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception as e:
        warn(f"settings.json 解析失败（{e}），跳过 hook 配置")
        return

    # 检查是否已配置
    stop_hooks = data.get("hooks", {}).get("Stop", [])
    for entry in stop_hooks:
        for h in entry.get("hooks", []):
            if "journal.py" in h.get("command", "") and "snapshot" in h.get("command", ""):
                ok("Stop hook 已配置，无需重复写入")
                return

    if not auto:
        return  # 非 --hook 模式，只提示

    data.setdefault("hooks", {}).setdefault("Stop", []).append(
        {"matcher": "", "hooks": [{"type": "command", "command": hook_cmd}]}
    )
    settings_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok("Stop hook 已写入 settings.json")


def print_hook_hint():
    hook_cmd = f'"{PYTHON}" "{SKILL_DST / "journal.py"}" snapshot'
    print(f"""
  如需 SessionEnd 自动 snapshot（推荐），手动在 ~/.claude/settings.json 加入：

    "hooks": {{
      "Stop": [{{
        "matcher": "",
        "hooks": [{{"type": "command", "command": "{hook_cmd}"}}]
      }}]
    }}

  或重新运行：  py install.py --hook
""")


def check_only():
    """只验证已安装的文件是否健康，不修改任何东西。"""
    ok_count = 0
    for p in [SKILL_DST / "journal.py", SKILL_DST / "SKILL.md", CMD_DST]:
        if p.exists():
            ok(f"存在 {p}")
            ok_count += 1
        else:
            warn(f"缺失 {p}")
    r = subprocess.run([PYTHON, str(SKILL_DST / "journal.py"), "selftest"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0:
        ok("selftest 全绿")
    else:
        warn("selftest 失败\n" + r.stdout)
    return ok_count == 3 and r.returncode == 0


# ── 主流程 ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="journal skill 安装脚本")
    ap.add_argument("--hook",  action="store_true", help="自动写入 SessionEnd hook")
    ap.add_argument("--check", action="store_true", help="只验证，不安装")
    args = ap.parse_args()

    print("\n=== journal skill 安装 ===\n")

    if args.check:
        print("── 验证模式 ──")
        ok = check_only()
        sys.exit(0 if ok else 1)

    print("1. 环境检查")
    check_python()
    check_sources()

    print("\n2. 复制文件")
    copy_files()

    print("\n3. 验证")
    verify()

    print("\n4. SessionEnd hook")
    configure_hook(auto=args.hook)
    if not args.hook:
        print_hook_hint()

    print(f"[OK] 安装完成\n")
    print(f"  skill:    {SKILL_DST}")
    print(f"  command:  {CMD_DST}")
    print(f"  日记目录: {Path.home() / '.claude' / 'journal'}")
    print(f"\n用法：")
    print(f"  /journal                          记录本次 session")
    print(f"  /journal threads                  查看 open thread")
    print(f"  /journal rollup                   生成本周蒸馏")
    print(f'  ! py ~/.claude/skills/journal/journal.py note -m "想法"')
    print()


if __name__ == "__main__":
    main()
