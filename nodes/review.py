#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点: review — 审查（检查者）
用 Codex 独立审查 diff：制作者-检查者分离。
结合 codegraph 验证调用链 + 五轴审查（正确性/可读性/架构/安全/性能）。
"""
import json
import os
import subprocess
import sys

payload = json.loads(os.environ["AGENT_LOOP_TASK"])
task = payload["task"]
ctx = payload["ctx"]

repo = task.get("repo", ctx.get("repo", ""))
base = task.get("base", "master")
branch = task.get("branch", ctx.get("branch", "feature/agent-task"))

if not repo or not os.path.isdir(repo):
    print(json.dumps({"status": "error", "output": f"仓库路径不存在: {repo}"}))
    sys.exit(0)

# 与 base 的 diff
r = subprocess.run(["git", "fetch", "origin", base], capture_output=True,
                   text=True, timeout=120, encoding="utf-8", cwd=repo)
r2 = subprocess.run(["git", "diff", f"origin/{base}...{branch}", "--stat"],
                    capture_output=True, text=True, encoding="utf-8", cwd=repo)
diff_stat = r2.stdout.strip()
if not diff_stat:
    print(json.dumps({"status": "approve", "output": "[review] 无 diff（无变更），直接通过"}))
    sys.exit(0)

review_prompt = f"""你是独立代码审查员（检查者），审查仓库 {repo} 中分支 {branch} 相对 {base} 的变更。
你与开发者是不同 Agent——你的职责是质疑，不是认可。

## 审查流程
1. 运行: git diff origin/{base}...{branch} 获取完整 diff
2. 运行: git diff origin/{base}...{branch} --stat 查看变更范围
3. 阅读仓库根目录 AGENTS.md 的项目规范（技术栈/代码规范/红线）
4. 用代码索引工具（可选）验证关键符号的调用链，确认改动没有破坏上下游
5. 五轴审查:
   - 正确性: 逻辑/边界/空值/事务
   - 可读性: 命名/控制流/魔法数字
   - 架构: 分层遵循/跨模块依赖泄漏
   - 安全: 参数校验/SQL注入/敏感数据
   - 性能: N+1/无索引查询/循环查库

## 输出要求
- 如无 Critical/Important 问题: 输出 {{"status": "approve", "comments": ["..."], "summary": "..."}}
- 如有问题: 输出 {{"status": "reject", "comments": ["具体问题1", "具体问题2"], "summary": "..."}}
  - 每条评论必须指出文件+行号+原因，不要模糊评价
  - 严重度: Critical(阻塞) / Important(应当处理) / Nit(可选)

最后一行必须是上述 JSON。"""

cmd = [
    "codex", "exec", review_prompt,
    "--sandbox", "danger-full-access",   # 网关环境 sandbox 兼容性
]

print(f"[review] 调用 Codex 审查: {branch} vs {base}", flush=True)
print(f"[review] 变更文件:\n{diff_stat}", flush=True)

try:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                       encoding="utf-8", cwd=repo)
    out = r.stdout
    if r.returncode != 0:
        print(json.dumps({"status": "error",
                          "output": f"Codex 退出码 {r.returncode}: {out[-1500:]}{r.stderr[-500:]}"}))
        sys.exit(0)

    lines = [l.strip() for l in out.split("\n") if l.strip()]
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
        # 无法解析结构化结果 → 保守处理：要求人工确认
        print(json.dumps({"status": "reject",
                          "output": f"[review] 未能从 Codex 输出解析审查结论（需人工确认）:\n{out[-800:]}",
                          "comments": ["审查输出无法自动解析，请人工检查"]}))
except subprocess.TimeoutExpired:
    print(json.dumps({"status": "error", "output": "review 节点超时(1800s)"}))
except FileNotFoundError:
    print(json.dumps({"status": "error", "output": "codex 命令不存在，检查 PATH"}))
