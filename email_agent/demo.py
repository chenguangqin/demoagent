"""离线端到端演示（无需真实邮箱 / Bedrock）。

运行：  python -m email_agent.demo
展示：垃圾过滤、简单问题自动回复(草稿)、复杂/紧急升级、线程后续、幂等、定时跟进。
"""
from __future__ import annotations

import time

from .common.models import EmailEvent
from .thread.store import ThreadStore
from .thread.followup import follow_up_sweep
from .tools.adapters import FakeMailer, FakeKnowledgeBase, FakeEscalator
from .workflow.orchestrator import EmailWorkflow
from . import config


def _evt(mid, frm, subj, body, **kw) -> EmailEvent:
    return EmailEvent(message_id=mid, thread_id="", from_addr=frm,
                      to_addrs=["support@acme.com"], subject=subj, body_text=body, **kw)


def main() -> None:
    config.HUMAN_IN_THE_LOOP = True          # 演示用草稿模式
    config.USE_LLM = False                    # 离线：桩分类器 + Fake 适配器
    store = ThreadStore(":memory:")
    mailer, kb, esc = FakeMailer(), FakeKnowledgeBase(), FakeEscalator()
    wf = EmailWorkflow(store, mailer, kb, esc)

    print("=== 1) 垃圾邮件 ===")
    print(wf.run(_evt("<s1@x>", "spam@x.com", "限时优惠 免费领取", "点击领取大奖")))

    print("\n=== 2) 简单问题（KB 命中 → 草稿）===")
    print(wf.run(_evt("<s2@x>", "alice@x.com", "请问退款流程", "你好，我想了解退款要多久？")))

    print("\n=== 3) 紧急问题（→ 升级 high）===")
    print(wf.run(_evt("<s3@x>", "bob@x.com", "系统无法登录 紧急", "线上故障，客户全部无法登录！")))

    print("\n=== 4) 复杂问题（→ 升级 normal）===")
    print(wf.run(_evt("<s4@x>", "carol@x.com", "合作提案", "我们想探讨一个定制化的企业合作方案。")))

    print("\n=== 5) 线程后续（同一线程再来信，带 References 头）===")
    r5 = wf.run(_evt("<s5@x>", "alice@x.com", "Re: 请问退款流程",
                     "还是没退到账，怎么回事？", in_reply_to="<s2@x>", references=["<s2@x>"]))
    print(r5, "-> 归到线程:", r5["thread_id"])

    print("\n=== 6) 幂等（重复 Message-ID）===")
    print(wf.run(_evt("<s2@x>", "alice@x.com", "请问退款流程", "重复投递")))

    print("\n=== 7) 定时跟进 sweep（模拟 SLA 已到期）===")
    stats = follow_up_sweep(store, mailer, esc, now=time.time() + config.SLA_SECONDS + 10)
    print("sweep:", stats)

    print("\n--- 汇总 ---")
    print("已发/草稿邮件:", len(mailer.sent), "| 工单:", len(esc.tickets))
    store.close()


if __name__ == "__main__":
    main()
