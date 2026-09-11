"""Worker：消费队列中的 EmailEvent，逐封触发工作流。"""
from __future__ import annotations

import queue

from .common.models import EmailEvent
from .thread.store import ThreadStore
from .tools.adapters import Mailer, KnowledgeBase, Escalator
from .workflow.orchestrator import EmailWorkflow


class Worker:
    def __init__(self, store: ThreadStore, mailer: Mailer, kb: KnowledgeBase, escalator: Escalator):
        self.wf = EmailWorkflow(store, mailer, kb, escalator)
        self.queue: "queue.Queue[EmailEvent]" = queue.Queue()

    def enqueue(self, evt: EmailEvent) -> None:
        self.queue.put(evt)

    def process_one(self, evt: EmailEvent) -> dict:
        return self.wf.run(evt)

    def run_forever(self) -> None:
        while True:
            evt = self.queue.get()
            try:
                self.process_one(evt)
            finally:
                self.queue.task_done()
