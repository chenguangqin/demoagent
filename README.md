# 智能电子邮件代理 (demoagent)

基于 **Python + Strands Agents SDK** 的智能电子邮件代理，采用 **确定性工作流 (Workflow) + ReAct Agent** 混合架构。

## 功能

- 📥 **侦听邮箱**：IMAP IDLE / Gmail Push / Webhook（本仓库含 IMAP 实现）
- 🗂 **垃圾/真实邮件排序**：规则前置过滤 + LLM 分类（`spam / simple / complex / urgent`）
- 🤖 **自动回复简单问题**：ReAct Agent 检索知识库 → 起草 → 回复（默认人工审核门）
- 🚨 **上报紧急/复杂问题**：生成摘要 + 草稿，开工单 / 通知负责人
- 🧵 **线程跟踪**：Event Sourcing + 物化视图（SQLite），状态机 + 定时跟进 (SLA / 催单)

## 架构

```
Listener(常驻) → 队列(幂等去重) → Worker → Workflow
  thread_ctx → triage → {spam | auto_reply(ReAct) | escalate} → persist
                                        ↑
                          Thread Store (events + threads + 索引)
                                        ↓
                          Follow-up Sweeper (SLA / 催单，定时)
```

详见 [`email-agent-design.md`](./email-agent-design.md)。

## 线程持久化（核心）

- `events` 表：**append-only**，审计 / 可重放的事实来源。
- `threads` 表：**物化状态**，UPSERT 原地更新，供高频检索。
- sweep 检索走 `(status, sla_due_at)` **partial 复合索引**，范围扫描而非全表 O(n)。
- 起步用 SQLite（一个文件 + B-tree 索引）；生产可换 DynamoDB + GSI / PostgreSQL，接口不变。

## 运行

```bash
pip install -r email_agent/requirements.txt   # 生产依赖 (Strands)
```

离线端到端演示（无需真实邮箱 / Bedrock，用桩分类器 + Fake 适配器）：

```bash
python -m email_agent.demo
```

## 配置（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `EMAIL_AGENT_USE_LLM` | `false` | 是否真正调用 Strands/Bedrock（否则用桩分类器） |
| `EMAIL_AGENT_HITL` | `true` | 人在环：自动回复只产草稿送审 |
| `EMAIL_AGENT_MODEL` | `global.anthropic.claude-sonnet-4-6` | 模型 ID |
| `EMAIL_AGENT_SLA` | `86400` | 首次响应 SLA（秒） |
| `EMAIL_AGENT_FOLLOWUP_IDLE` | `259200` | 客户静默多久后催单（秒） |
| `EMAIL_AGENT_DB` | `:memory:` | SQLite 路径 |

## 目录

```
email_agent/
├── listener/     # 邮件监听（IMAP）+ 解析
├── workflow/     # 编排器 + triage + reply(ReAct) + escalate
├── thread/       # 线程存储 / 归一化 / 定时跟进
├── tools/        # Mailer / KB / Escalator 适配器 (+ Fake)
├── common/       # 数据模型 + 安全净化
├── config.py     # 配置开关
├── worker.py     # 队列消费
└── demo.py       # 离线端到端演示入口
```

> ⚠️ 本仓库为设计演示：默认开启人工审核门，上线前请完善提示注入防护、附件沙箱、真实发信/工单集成与评测。
