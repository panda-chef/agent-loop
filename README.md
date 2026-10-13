# agent-loop

**把「会用 AI 写代码」变成「编排多个 AI Agent 完成一次完整交付」。**

一个 600 行左右的 DAG 编排器：让 Claude Code 写代码、另一个模型独立审查、机器跑构建门禁、通过后自动推分支，最后一步合并留给人工。

> 这不是又一个 prompt 合集，而是一套**可复现、可断点续跑、可审计**的协作骨架。

---

## 它解决什么问题

大多数团队的「AI 辅助开发」卡在同一个地方：一次会话里让模型改代码，改完你自己看、自己跑、自己判断能不能提交。

这套流程有三个问题：

- **不可复现** —— 上次能跑通，这次顺序不一样就崩了；换个同事，环境不同就废了
- **不可审计** —— 模型为什么这么改、中间试错了几轮，会话一关全没了
- **自己审自己** —— 写代码的和判断「写得好不好」的是同一个模型，盲区完全重合

`agent-loop` 把这条链路拆成 5 个独立节点，每个节点是一次独立的进程调用，节点之间只通过一份 JSON 状态通信。

```
START → develop → verify ──pass──→ push → review ──approve──→ merge
            ↑          │                     │
            └──fail────┘                     │
            └──────────────reject────────────┘
```

---

## 关键设计

### 1. 节点之间没有共享内存，只读写 State

每个节点是一个独立 Python 进程，通过环境变量拿到任务上下文，把结果以 JSON 打到 stdout。编排器只做路由。

这样做的好处是**节点可以被任意替换**：想换个模型写代码，只改 `nodes/develop.py` 一个文件；想把构建门禁从 Maven 换成 npm，只改任务 JSON 里的命令。

### 2. 每个节点完成后落 checkpoint，崩溃可续跑

状态写在 `state/<task_id>.json`。`python dag.py resume <task>` 会从断点继续，而不是从头再来。

Agent 任务动辄跑几十分钟，一次网络抖动就重头开始是不可接受的。

### 3. 制作者与检查者必须是不同的模型

`develop` 节点用 Claude Code，`review` 节点用 Codex，且审查节点被明确告知：

> 你与开发者是不同 Agent——你的职责是质疑，不是认可。

审查节点独立拉取 `git diff`、独立读项目规范文件，按五个轴给结论：正确性、可读性、架构、安全、性能。每条意见必须指出**文件 + 行号 + 原因**，不接受「建议优化一下」这种模糊评价。

同一个模型写和审，盲区完全重合，等于没审。

### 4. 条件边带重试上限，防止 agent 无限循环

`verify` 失败回开发、`review` 打回回开发，都是回边。回边是必要的（agent 一次写不对很正常），但**必须有上限**——超过 `max_retries` 直接判失败并落盘原因。

没有上限的 agent 循环是账单黑洞。

### 5. 验证必须是机器判的

`verify` 节点跑的是**真实构建命令**，不是让模型说「我觉得没问题」。

一件事算不算做完了，由可执行命令的退出码决定，不由模型的自评决定。判断标准一旦写成机器可判的，agent 才真正跑得起来。

### 6. 合并闸门默认关闭

```json
{ "auto_merge": false }
```

整套流程里，`develop`、`verify`、`review`、`push` 全自动，**唯独最后一步合并默认留给人**。

理由很直接：前面几步出错，代价是重跑；合并出错，代价是污染主干分支。风险不对称的地方，就不该对称地自动化。

想全自动，把这一项显式打开即可——但这个决定应该是你做的，不是默认替你做的。

---

## 快速开始

```bash
git clone <this-repo>
cd agent-loop

# 一份任务描述
cat > tasks/my-task.json <<'EOF'
{
  "id": "task-001",
  "repo": "/path/to/your/repo",
  "base": "main",
  "branch": "feature/add-source-field",
  "description": "给实体新增 source 字段，同步更新 Mapper 与 DTO，保持接口兼容",
  "acceptance": ["编译通过", "新增字段有默认值，老数据不受影响"],
  "verify_cmd": ["mvn", "compile", "-DskipTests"],
  "max_retries": 3,
  "auto_merge": false
}
EOF

python dag.py run   tasks/my-task.json   # 跑
python dag.py resume tasks/my-task.json  # 崩了之后续跑
python dag.py status tasks/my-task.json  # 看状态
```

环境要求：

- Python 3.9+（只用标准库，无第三方依赖）
- `claude` 命令可用（或用 `CLAUDE_BIN` 指定路径）
- `codex` 命令可用（审查节点）
- 构建工具按 `verify_cmd` 配置（或用 `MVN_BIN` 指定路径）

---

## 一些实现细节

**为什么不用现成的编排框架？**

试过把这类流程塞进单进程的图框架，但这里要的是「跨进程、可断点、可审计」——每一步都是独立命令行调用，输出要能落盘复盘。用编排框架反而要在框架里再包一层 subprocess，不如直接用 200 行写完路由逻辑。

**Windows 上的两个坑（代码里保留了修正逻辑）**

- 从 MSYS/Git-Bash 传进来的 `JAVA_HOME` 可能是 `/d/tools/jdk` 这种风格，原生程序读不懂，需要在节点里转成 `D:\tools\jdk`
- 原生程序的 `PATH` 里不含 MSYS 目录，得显式补

这类问题不解决，第一晚就卡在「命令找不到」上。

**节点失败怎么处理？**

`develop` 之外任何节点报错，都会回到 `develop` 重做（并计入重试次数）。`develop` 自己失败则直接判失败——因为已经没有可以回退的地方了。

---

## 目录结构

```
agent-loop/
├── dag.py                 # 编排器：状态机 + 条件边路由 + checkpoint
├── nodes/
│   ├── develop.py         # 开发（Claude Code）
│   ├── verify.py          # 构建门禁（任意命令）
│   ├── review.py          # 独立审查（Codex，制作者-检查者分离）
│   ├── push.py            # 推送分支
│   └── merge.py           # 合并闸门（默认人工）
├── tasks/                 # 任务描述（每任务一个 JSON）
├── state/                 # 运行时 checkpoint（已 gitignore）
└── examples/
    └── task.example.json
```

---

## 已知边界

- 目前是**线性 + 回边**的 DAG，不支持并行分支（同一任务里多节点同时跑）
- 审查结论依赖模型输出可解析的 JSON，偶尔会解析失败——失败时保守处理为「需人工确认」，不放过
- 没有内置成本统计。跑长任务前建议自己在节点里加 token 计数，这类流程最容易失控的就是账单

---

## License

MIT
