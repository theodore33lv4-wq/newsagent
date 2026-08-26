"""告警发送测试（未启用/无 URL → 静默；坏 URL → False 不抛出）。"""

from newsagent.utils.notify import notify_failure, send_webhook


def test_notify_disabled(cfg):
    cfg.notify["enabled"] = False
    assert notify_failure(cfg, "t", "c") is False


def test_notify_no_url(cfg, monkeypatch):
    cfg.notify["enabled"] = True
    monkeypatch.delenv("NOTIFY_WEBHOOK_URL", raising=False)
    assert notify_failure(cfg, "t", "c") is False


def test_send_webhook_bad_url():
    assert send_webhook("http://127.0.0.1:1/", "内容", fmt="wecom") is False
