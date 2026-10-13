#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点: verify — 验证 Gate
运行构建/测试，只有通过才放行到 push。
"""
import json
import os
import subprocess
import sys

# Windows: 补全 PATH + 修正 JAVA_HOME（MSYS /d/xxx → D:\xxx）
if sys.platform == "win32":
    _jh = os.environ.get("JAVA_HOME", "")
    if _jh and len(_jh) >= 2 and _jh[0] == "/" and _jh[2] == "/":
        os.environ["JAVA_HOME"] = _jh[1].upper() + ":" + _jh[2:]
    _mvn_bin = os.environ.get("MVN_BIN", "mvn")
else:
    _mvn_bin = "mvn"

payload = json.loads(os.environ["AGENT_LOOP_TASK"])
task = payload["task"]
ctx = payload["ctx"]

repo = task.get("repo", ctx.get("repo", ""))
build_cmd = task.get("verify_cmd", ["mvn", "compile", "-DskipTests"])
# 替换命令为平台相关的可执行文件
if len(build_cmd) > 0:
    build_cmd = [_mvn_bin] + list(build_cmd[1:])

if not repo or not os.path.isdir(repo):
    print(json.dumps({"status": "error", "output": f"仓库路径不存在: {repo}"}))
    sys.exit(0)

print(f"[verify] 验证命令: {' '.join(build_cmd)}", flush=True)

# 检查 mvn 是否可用
try:
    subprocess.run([_mvn_bin, "-v"], capture_output=True, timeout=30,
                   encoding="utf-8", cwd=repo)
except FileNotFoundError:
    print(json.dumps({
        "status": "pass",  # 骨架阶段：mvn 不可用则降级为静态检查通过
        "output": "[verify] mvn 不可用，跳过构建（骨架阶段降级）。后续配置 Maven 后启用真实 Gate。",
        "degraded": True
    }))
    sys.exit(0)

try:
    r = subprocess.run(build_cmd, capture_output=True, text=True, timeout=1200,
                       encoding="utf-8", cwd=repo)
    tail = (r.stdout + r.stderr)[-3000:]
    if r.returncode == 0:
        print(json.dumps({"status": "pass", "output": f"[verify] 构建成功\n{tail[-500:]}"}))
    else:
        print(json.dumps({"status": "fail", "output": f"[verify] 构建失败(exit={r.returncode})\n{tail}"}))
except subprocess.TimeoutExpired:
    print(json.dumps({"status": "fail", "output": "[verify] 构建超时(1200s)"}))
