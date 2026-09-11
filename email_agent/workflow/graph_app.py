"""邮件工作流的 Strands Graph 落地。

拓扑：
    thread_ctx ──► triage ──┬─(spam)────► archive_spam ─┐
                            ├─(simple)──► auto_reply ────┼─► persist
                            └─(complex/urgent)─► escalate┘

- 所有节点都是确定性 FunctionNode（triage/auto_reply 内部按需调 LLM）。
- 分支用条件边（condition 读 triage 的分类结果）实现。
- persist 是汇聚点：Python 的 OR 语义下，任一分支完成即触发它一次。

需要 strands SDK。无 SDK 环境请用 orchestrator.EmailWorkflow（等价的纯 Python 回退）。
"""
from __future__ import annotations

from ..common.models import EmailEvent, Category
from ..thread.store import ThreadStore
from ..tools.adapters import Mailer, KnowledgeBase, Escalator
from .nodes import RunContext, FunctionNode, make_nodes


def _cat_is(ctx: RunContext, *cats: str):
    """生成一个条件：triage 判定的类别属于 cats 才放行该边。"""
    def cond(state) -> bool:  # state: GraphState（此处不依赖它，读共享 ctx）
        return (not ctx.duplicate) and ctx.verdict.get("category") in cats
    return cond


def build_email_graph(ctx: RunContext):
    """用 GraphBuilder 组装邮件处理图。返回可执行 graph。"""
    from strands.multiagent import GraphBuilder

    fns = make_nodes(ctx)
    nodes = {name: FunctionNode(fn, name).as_executor() for name, fn in fns.items()}

    b = GraphBuilder()
    for name, ex in nodes.items():
        b.add_node(ex, name)

    b.set_entry_point("thread_ctx")
    b.add_edge("thread_ctx", "triage")

    # 条件分支（读 ctx.verdict）
    b.add_edge("triage", "archive_spam", condition=_cat_is(ctx, Category.SPAM.value))
    b.add_edge("triage", "auto_reply", condition=_cat_is(ctx, Category.SIMPLE.value))
    b.add_edge("triage", "escalate",
               condition=_cat_is(ctx, Category.COMPLEX.value, Category.URGENT.value))

    # 汇聚到 persist
    b.add_edge("archive_spam", "persist")
    b.add_edge("auto_reply", "persist")
    b.add_edge("escalate", "persist")

    b.set_execution_timeout(120)
    return b.build()


class GraphEmailWorkflow:
    """基于 Strands Graph 的邮件工作流入口，接口对齐 orchestrator.EmailWorkflow。"""

    def __init__(self, store: ThreadStore, mailer: Mailer, kb: KnowledgeBase, escalator: Escalator):
        self.store, self.mailer, self.kb, self.escalator = store, mailer, kb, escalator

    def run(self, evt: EmailEvent) -> dict:
        ctx = RunContext(evt=evt, store=self.store, mailer=self.mailer,
                         kb=self.kb, escalator=self.escalator)
        graph = build_email_graph(ctx)
        graph(f"处理邮件 {evt.message_id}")   # 同步执行；或 await graph.invoke_async(...)
        if ctx.duplicate:
            return ctx.result
        return {"thread_id": evt.thread_id, "category": ctx.verdict.get("category"),
                "confidence": ctx.verdict.get("confidence"),
                "status": ctx.final_status.value, **ctx.result}
