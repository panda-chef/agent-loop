#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点: merge — 合并（安全模式：默认不自动合并，仅提示人工操作）
审查通过后，合并动作默认保留给人工（Loop Engineering 风险控制原则）。
如需自动合并，设 task.json 中 "auto_merge": true。
"""
import json
import os
import subprocess
import sys

payload = json.loads(os.environ["AGENT_LOOP_TASK"])
task = payload["task"]
ctx = payload["ctx"]

repo = task.get("repo", ctx.get("repo", ""))
branch = task.get("branch", ctx.get("branch", ""))
base = task.get("base", "master")
auto_merge = task.get("auto_merge", False)

if not repo or not os.path.isdir(repo):
    print(json.dumps({"status": "error", "output": f"仓库路径不存在: {repo}"}))
    sys.exit(0)

if auto_merge:
    # 自动合并：切到 base，merge 分支，推回
    steps = [
        ["git", "checkout", base],
        ["git", "pull", "origin", base],
        ["git", "merge", branch],
        ["git", "push", "origin", base],
    ]
    for step in steps:
        r = subprocess.run(step, capture_output=True, text=True, timeout=300,
                           encoding="utf-8", cwd=repo)
        if r.returncode != 0:
            print(json.dumps({"status": "fail",
                              "output": f"[merge] {' '.join(step)} 失败: {(r.stdout+r.stderr)[-800:]}"}))
            sys.exit(0)
    print(json.dumps({"status": "done", "output": f"[merge] 已自动合并 {branch} → {base} 并推送"}))
else:
    print(json.dumps({
        "status": "done",
        "output": f"[merge] 审查通过。请人工在 Git 平台合并 MR/PR: {branch} → {base}（auto_merge=false 保留人工把关）"
    }))
