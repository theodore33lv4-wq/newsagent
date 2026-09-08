"""告警发送测试（未启用/无 URL → 静默；坏 URL → False 不抛出）；钉钉推送通道。"""

import json

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


# ---------- 钉钉工作通知 ----------
def test_dingtalk_config_reads_env(cfg, monkeypatch):
    from newsagent.utils.dingtalk import dingtalk_config
    cfg.notify["dingtalk"] = {"agent_id_env": "DINGTALK_AGENT_ID",
                              "app_key_env": "DINGTALK_APP_KEY",
                              "app_secret_env": "DINGTALK_APP_SECRET",
                              "userids_env": "DINGTALK_USERIDS"}
    monkeypatch.setenv("DINGTALK_APP_KEY", "k1")
    monkeypatch.setenv("DINGTALK_APP_SECRET", "s1")
    monkeypatch.setenv("DINGTALK_AGENT_ID", "123")
    monkeypatch.setenv("DINGTALK_USERIDS", "u1,u2")
    conf = dingtalk_config(cfg)
    assert conf["app_key"] == "k1" and conf["userids"] == ["u1", "u2"]


def test_dingtalk_push_missing_config(cfg, monkeypatch):
    from newsagent.utils.dingtalk import push_weekly_report
    cfg.notify["dingtalk"] = {}
    for k in ("DINGTALK_APP_KEY", "DINGTALK_APP_SECRET",
              "DINGTALK_AGENT_ID", "DINGTALK_USERIDS"):
        monkeypatch.delenv(k, raising=False)
    assert push_weekly_report(cfg, __import__("pathlib").Path("nope.html")) is False


def test_dingtalk_client_token_cache(monkeypatch):
    from newsagent.utils.dingtalk import DingTalkClient

    calls = {"n": 0}

    class FakeResp:
        def json(self):
            return {"errcode": 0, "access_token": "tok-1", "expires_in": 7200}

    def fake_post(url, **kw):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr("newsagent.utils.dingtalk.httpx.post", fake_post)
    client = DingTalkClient("k", "s")
    assert client.get_token() == "tok-1"
    assert client.get_token() == "tok-1"   # 命中缓存
    assert calls["n"] == 1
