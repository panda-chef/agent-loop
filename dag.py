#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agent-agent-loop DAG 编排器 — 借鉴 LangGraph 的「开发 → 推送 → 审查」工作流

设计原则（Loop Engineering）:
- 节点间无共享内存，只读写 State（LangGraph State 模式）
- 每节点完成后写 checkpoint（state/<task_id>.json），崩溃可恢复续跑
- 条件边：审查通过 → 合并；打回 → 开发（带 max_retries 防死循环）
- 制作者-检查者分离：开发用 Claude Code，审查用 Codex

用法:
    python dag.py run tasks/sample.json          # 执行任务 DAG
    python dag.py resume tasks/sample.json       # 从断点恢复
    python dag.py status tasks/sample.json       # 查看状态
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
NODES_DIR = os.path.join(BASE_DIR, "nodes")

# 默认 DAG 结构（与任务 JSON 的 nodes/edges 合并）
DEFAULT_DAG = {
    "nodes": ["develop", "verify", "push", "review", "merge"],
    "edges": [
        {"from": "START", "to": "develop"},
        {"from": "develop", "to": "verify"},
        {"from": "verify", "to": "push", "on": "pass"},
        {"from": "verify", "to": "develop", "on": "fail"},       # 验证失败回开发
        {"from": "push", "to": "review"},
        {"from": "review", "to": "merge", "on": "approve"},      # 审查通过 → 合并
        {"from": "review", "to": "develop", "on": "reject"},     # 打回 → 修复 Loop
    ],
    "end": "merge",
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_task(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        task = json.load(f)
    task.setdefault("dag", DEFAULT_DAG)
    task.setdefault("state", {"status": "pending", "nodes": {}, "retries": 0})
    return task


def state_path(task: dict) -> str:
    return os.path.join(STATE_DIR, f"{task['id']}.json")


def save_state(task: dict):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(task), "w", encoding="utf-8") as f:
        json.dump({"task_id": task["id"], "updated": now(), "state": task["state"]},
                  f, ensure_ascii=False, indent=2)


def run_node(node: str, task: dict, ctx: dict) -> dict:
    """调用 nodes/<node>.py，传入任务上下文，返回 {status, output, ...}"""
    script = os.path.join(NODES_DIR, f"{node}.py")
    if not os.path.exists(script):
        return {"status": "skipped", "output": f"[{node}] 节点脚本不存在，跳过（骨架阶段）"}

    payload = json.dumps({"task": task, "ctx": ctx}, ensure_ascii=False)
    env = dict(os.environ)
    env["AGENT_LOOP_TASK"] = payload
    try:
        r = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=3600, env=env, encoding="utf-8"
        )
        if r.returncode != 0:
            return {"status": "error", "output": r.stdout + r.stderr[-2000:]}
        try:
            return json.loads(r.stdout.strip().split("\n")[-1])
        except json.JSONDecodeError:
            return {"status": "done", "output": r.stdout}
    except subprocess.TimeoutExpired:
        return {"status": "error", "output": f"[{node}] 节点超时(3600s)"}


def next_nodes(node: str, result: dict, dag: dict) -> list:
    """根据节点结果和条件边，决定下一跳节点（LangGraph 条件边）"""
    on = result.get("status")  # pass/fail/approve/reject/done/error
    targets = []
    for e in dag.get("edges", []):
        if e["from"] != node:
            continue
        if "on" in e:
            if on == e["on"]:
                targets.append(e["to"])
        else:
            targets.append(e["to"])
    return targets


def execute(task: dict, resume: bool = False) -> dict:
    st = task["state"]
    if not resume or st.get("status") in ("pending",):
        st = {"status": "running", "nodes": {}, "retries": 0}
        task["state"] = st

    # 当前节点：未完成序列中的第一个
    dag = task["dag"]
    # 开发节点 = START 边指向的第一个节点（打回/修复的目标）
    dev_node = "develop"
    starts = [e["to"] for e in dag.get("edges", []) if e["from"] == "START"]
    if starts:
        dev_node = starts[0]
    current = st.get("current") or "START"
    visited = set(st["nodes"].keys())

    ctx = {"task_id": task["id"], "repo": task.get("repo", ""),
           "branch": task.get("branch", "master"), "started": now()}

    while True:
        if current in visited:
            # 找下一个可达节点
            nxt = None
            for n in dag["nodes"]:
                if n not in visited:
                    nxt = n
                    break
            if nxt is None:
                st["status"] = "completed"
                save_state(task)
                print(json.dumps({"final": "completed", "nodes": st["nodes"]}, ensure_ascii=False, indent=2))
                return st
            current = nxt

        if current == "START":
            st["nodes"]["START"] = {"status": "done", "at": now()}
            visited.add("START")
            # 起始节点 = START 边指向的第一个节点（LangGraph: START edge）
            starts = [e["to"] for e in dag.get("edges", []) if e["from"] == "START"]
            first = starts[0] if starts else dag["nodes"][0]
            st["current"] = first
            current = first
            continue

        print(f"[{now()}] ▶ 执行节点: {current}", flush=True)
        result = run_node(current, task, ctx)
        st["nodes"][current] = {"status": result["status"], "at": now(),
                                "output": result.get("output", "")[-500:]}
        st["current"] = current
        save_state(task)

        if result["status"] == "error":
            # 错误处理：verify/push/review 失败 → 回到开发节点修复（带重试上限）
            if current != dev_node:
                st["retries"] += 1
                if st["retries"] > task.get("max_retries", 3):
                    st["status"] = "failed"
                    st["reason"] = f"重试超过上限({task.get('max_retries', 3)})"
                    save_state(task)
                    print(json.dumps({"final": "failed", "reason": st["reason"]}, ensure_ascii=False))
                    return st
                print(f"[{now()}] ⚠ {current} 失败(第{st['retries']}次) → 回到 {dev_node} 修复", flush=True)
                visited.discard(dev_node)
                visited.discard(current)
                current = dev_node
                continue
            else:
                st["status"] = "failed"
                st["reason"] = f"{current} 节点错误: {result['output'][-300:]}"
                save_state(task)
                print(json.dumps({"final": "failed", "reason": st["reason"]}, ensure_ascii=False))
                return st

        visited.add(current)
        nxt = next_nodes(current, result, dag)
        if not nxt:
            st["status"] = "completed"
            st["current"] = current
            save_state(task)
            print(json.dumps({"final": "completed", "nodes": st["nodes"]}, ensure_ascii=False, indent=2))
            return st

        # 条件边路由：目标是已访问节点 → 回边（打回重做）；否则前进
        target = nxt[0]
        if target in visited and target != current:
            if result["status"] == "reject":
                st["retries"] += 1
                print(f"[{now()}] ⚠ 审查打回 → 修复 Loop #{st['retries']}", flush=True)
                if st["retries"] > task.get("max_retries", 3):
                    st["status"] = "failed"
                    st["reason"] = f"循环超过上限({task.get('max_retries', 3)})"
                    save_state(task)
                    print(json.dumps({"final": "failed", "reason": st["reason"]}, ensure_ascii=False))
                    return st
            else:
                print(f"[{now()}] ↩ 回边: {current} → {target}（重做）", flush=True)
            visited.discard(target)
            visited.discard(current)
            current = target
        else:
            current = target


def main():
    ap = argparse.ArgumentParser(description="agent-agent-loop DAG 编排器")
    ap.add_argument("action", choices=["run", "resume", "status"], help="执行动作")
    ap.add_argument("task_file", help="任务 JSON 路径 (tasks/*.json)")
    args = ap.parse_args()

    task = load_task(args.task_file)

    if args.action == "status":
        sp = state_path(task)
        if os.path.exists(sp):
            with open(sp, "r", encoding="utf-8") as f:
                print(f.read())
        else:
            print(json.dumps({"task_id": task["id"], "status": "not_started"}, ensure_ascii=False, indent=2))
        return

    if args.action == "run":
        # 清理旧状态
        if os.path.exists(state_path(task)):
            os.remove(state_path(task))
        execute(task, resume=False)
    else:
        execute(task, resume=True)


if __name__ == "__main__":
    main()
