#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点: push — 推送分支到远端
将开发分支推送到远端。MR/PR 的创建交由 Git 平台的 API/MCP（或人工）在审查通过后执行。
"""
import json
import os
import subprocess
import sys

payload = json.loads(os.environ["AGENT_LOOP_TASK"])
task = payload["task"]
ctx = payload["ctx"]

repo = task.get("repo", ctx.get("repo", ""))
branch = task.get("branch", ctx.get("branch", "feature/agent-task"))

if not repo or not os.path.isdir(repo):
    print(json.dumps({"status": "error", "output": f"仓库路径不存在: {repo}"}))
    sys.exit(0)

# 确认当前分支
try:
    cur = subprocess.run(["git", "branch", "--show-current"], capture_output=True,
                         text=True, encoding="utf-8", cwd=repo).stdout.strip()
except Exception as e:
    print(json.dumps({"status": "error", "output": f"git 检查失败: {e}"}))
    sys.exit(0)

if cur != branch:
    print(json.dumps({"status": "fail",
                      "output": f"[push] 当前分支 {cur} != 目标分支 {branch}，跳过推送"}))
    sys.exit(0)

# 推送到 origin
r = subprocess.run(["git", "push", "-u", "origin", branch],
                   capture_output=True, text=True, timeout=300, encoding="utf-8", cwd=repo)
if r.returncode == 0:
    print(json.dumps({"status": "pass", "output": f"[push] 已推送 {branch} → origin"}))
else:
    err = (r.stdout + r.stderr)[-1500:]
    # 已存在远端分支且无新提交 = 无需推送
    if "Everything up-to-date" in err:
        print(json.dumps({"status": "pass", "output": "[push] 分支已是最新，无需推送"}))
    else:
        print(json.dumps({"status": "fail", "output": f"[push] 推送失败: {err}"}))
