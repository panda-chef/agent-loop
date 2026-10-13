#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点: develop — 开发（制作者）
调用 Claude Code 实现任务。
"""
import json
import os
import subprocess
import sys
import tempfile

payload = json.loads(os.environ["AGENT_LOOP_TASK"])
task = payload["task"]
ctx = payload["ctx"]

repo = task.get("repo", ctx.get("repo", ""))
branch = task.get("branch", ctx.get("branch", "feature/agent-task"))
description = task.get("description", "")
acceptance = task.get("acceptance", [])
base = task.get("base", "master")

if not repo or not os.path.isdir(repo):
    print(json.dumps({"status": "error", "output": f"仓库路径不存在: {repo}"}))
    sys.exit(0)

# 构建验收提示
acceptance_hint = "\n".join(f"- {a}" for a in acceptance) if acceptance else ""

# 精简 prompt，核心任务描述
prompt = f"""在仓库 {repo} 完成以下任务：

{description}

验收标准：
{acceptance_hint}

工作流程：
1. 先读取 AGENTS.md 了解项目规范
2. 找到相关文件，理解现有代码模式
3. 做最小改动，保持接口兼容
4. 完成后按任务里的 verify_cmd 验证构建
5. git checkout -b {branch} 创建分支，git add + git commit（遵循仓库既有提交规范）
6. 最后用 JSON 格式输出结果

输出要求（最后一行必须是以下 JSON）：
{{"status": "pass", "commit": "<hash>", "summary": "<一句话>"}}
或
{{"status": "fail", "summary": "<原因>"}}
"""

# Windows 环境修正
if sys.platform == "win32":
    _jh = os.environ.get("JAVA_HOME", "")
    if _jh and len(_jh) >= 2 and _jh[0] == "/" and _jh[2] == "/":
        os.environ["JAVA_HOME"] = _jh[1].upper() + ":" + _jh[2:]
    _claude_bin = os.environ.get("CLAUDE_BIN", "claude")
else:
    _claude_bin = "claude"

# 将 prompt 写入临时文件，避免命令行参数过长/转义问题
with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", encoding="utf-8", delete=False) as f:
    f.write(prompt)
    prompt_file = f.name

print(f"[develop] 调用 Claude Code 开发: {repo}", flush=True)
print(f"[develop] 任务: {description[:100]}...", flush=True)

try:
    # 通过 stdin 传递 prompt，避免命令行转义问题
    with open(prompt_file, "r", encoding="utf-8") as pf:
        r = subprocess.run(
            [_claude_bin, "-p",
             "--allowedTools", "Read,Edit,Write,Bash,Glob,Grep",
             "--permission-mode", "bypassPermissions",
             "--max-turns", "40",
             "--output-format", "json"],
            stdin=pf,
            capture_output=True, text=True, timeout=1800,
            encoding="utf-8", cwd=repo)
    out = r.stdout
    if r.returncode != 0:
        print(json.dumps({"status": "error", "output": f"Claude 退出码 {r.returncode}: {out[-1500:]}{r.stderr[-500:]}"}))
        sys.exit(0)

    # 从 Claude JSON 输出中解析 result
    try:
        data = json.loads(out)
        result_text = data.get("result", out)
    except json.JSONDecodeError:
        result_text = out

    # 提取最后一行 JSON 状态
    lines = [l.strip() for l in result_text.split("\n") if l.strip()]
    final_line = None
    for l in reversed(lines):
        if l.startswith("{") and l.endswith("}"):
            try:
                final_line = json.loads(l)
                break
            except json.JSONDecodeError:
                continue
    if final_line:
        print(json.dumps(final_line, ensure_ascii=False))
    else:
        print(json.dumps({"status": "done", "output": result_text[-800:],
                          "summary": "完成（未返回结构化结果，需人工确认）"}))
except subprocess.TimeoutExpired:
    print(json.dumps({"status": "error", "output": "develop 节点超时(1800s)"}))
except FileNotFoundError:
    print(json.dumps({"status": "error", "output": "claude 命令不存在，检查 PATH"}))
finally:
    try:
        os.unlink(prompt_file)
    except:
        pass
