"""失败告警：企业微信/钉钉/飞书群机器人 webhook（默认关闭，可选开启）。"""

from __future__ import annotations

import os

import httpx
from loguru import logger

from ..utils.config import Config


def send_webhook(url: str, content: str, fmt: str = "wecom") -> bool:
    """发送文本消息到群机器人。fmt: wecom | dingtalk | feishu。"""
    if fmt == "feishu":
        payload = {"msg_type": "text", "content": {"text": content}}
    else:  # wecom / dingtalk 均为 {"msgtype":"text","text":{"content":...}}
        payload = {"msgtype": "text", "text": {"content": content}}
    try:
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        logger.info("告警已发送（HTTP {}）", resp.status_code)
        return True
    except Exception as exc:
        logger.warning("告警发送失败: {}", exc)
        return False


def notify_failure(cfg: Config, title: str, content: str) -> bool:
    """按配置发送失败告警；未启用/未配置 URL 时静默跳过。"""
    if not cfg.notify.get("enabled"):
        return False
    url = os.environ.get(str(cfg.notify.get("webhook_url_env", "NOTIFY_WEBHOOK_URL")))
    if not url:
        logger.warning("notify.enabled 但未配置 {}，跳过告警",
                       cfg.notify.get("webhook_url_env"))
        return False
    return send_webhook(url, f"{title}\n{content}",
                        fmt=str(cfg.notify.get("format", "wecom")))
