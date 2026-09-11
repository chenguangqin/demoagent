"""配置：模型、SLA、开关。可用环境变量覆盖。"""
from __future__ import annotations

import os


def _env_bool(key: str, default: bool) -> bool:
    v = os.getenv(key)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")


# 模型
MODEL_ID = os.getenv("EMAIL_AGENT_MODEL", "global.anthropic.claude-sonnet-4-6")
MODEL_REGION = os.getenv("EMAIL_AGENT_REGION", "us-west-2")

# 是否真正调用 Strands/Bedrock。默认 False → 用桩分类器，可离线跑通。
USE_LLM = _env_bool("EMAIL_AGENT_USE_LLM", False)

# 人在环：True 时自动回复只产出草稿送审，不外发。
HUMAN_IN_THE_LOOP = _env_bool("EMAIL_AGENT_HITL", True)

# 分类置信度阈值：低于此值转人工兜底。
MIN_CONFIDENCE = float(os.getenv("EMAIL_AGENT_MIN_CONF", "0.6"))

# SLA / 跟进（秒）
SLA_SECONDS = float(os.getenv("EMAIL_AGENT_SLA", str(24 * 3600)))          # 首次响应 SLA
FOLLOWUP_IDLE_SECONDS = float(os.getenv("EMAIL_AGENT_FOLLOWUP_IDLE", str(3 * 24 * 3600)))
MAX_FOLLOWUP = int(os.getenv("EMAIL_AGENT_MAX_FOLLOWUP", "2"))

# SQLite 路径
DB_PATH = os.getenv("EMAIL_AGENT_DB", ":memory:")
