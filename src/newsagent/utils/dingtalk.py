"""钉钉工作通知推送（企业内部应用）：发送文件/卡片给指定个人。

说明：钉钉群机器人不支持发文件、也发不了个人；给个人发文件必须走
开放平台"企业内部应用"的工作通知（oapi.dingtalk.com）。

流程：gettoken → media/upload（type=file 上传 HTML/docx）→
topapi/message/corpconversation/asyncsend_v2（msgtype=file）。
token 带缓存（expires_in 到期自动刷新）。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from ..utils.config import Config

_API = "https://oapi.dingtalk.com"


class DingTalkError(Exception):
    pass


class DingTalkClient:
    def __init__(self, app_key: str, app_secret: str, timeout: float = 20.0):
        self.app_key = app_key
        self.app_secret = app_secret
        self.timeout = timeout
        self._token: Optional[str] = None
        self._token_expire_at: float = 0.0

    # ---------- token ----------
    def get_token(self) -> str:
        if self._token and time.time() < self._token_expire_at - 60:
            return self._token
        resp = httpx.post(f"{_API}/gettoken", params={
            "appkey": self.app_key, "appsecret": self.app_secret,
        }, timeout=self.timeout)
        data = _check(resp, "gettoken")
        self._token = data["access_token"]
        self._token_expire_at = time.time() + int(data.get("expires_in", 7200))
        return self._token

    # ---------- 文件 ----------
    def upload_file(self, path: Path) -> str:
        """上传文件（type=file，10MB 内），返回 media_id。"""
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{_API}/media/upload",
                params={"access_token": self.get_token(), "type": "file"},
                files={"media": (path.name, path.read_bytes())},
            )
        data = _check(resp, f"media/upload({path.name})")
        return str(data.get("media_id") or data.get("mediaId") or "")

    # ---------- 发送 ----------
    def send_file_message(self, agent_id: str, userids: list[str], media_id: str) -> dict:
        return self._send(agent_id, userids, {
            "msgtype": "file", "file": {"media_id": media_id},
        })

    def send_markdown_message(self, agent_id: str, userids: list[str],
                              title: str, text: str) -> dict:
        return self._send(agent_id, userids, {
            "msgtype": "markdown", "markdown": {"title": title, "text": text},
        })

    def _send(self, agent_id: str, userids: list[str], msg: dict) -> dict:
        resp = httpx.post(
            f"{_API}/topapi/message/corpconversation/asyncsend_v2",
            params={"access_token": self.get_token()},
            json={
                "agent_id": int(agent_id),
                "userid_list": ",".join(userids),
                "msg": json.dumps(msg, ensure_ascii=False),
            },
            timeout=self.timeout,
        )
        return _check(resp, f"asyncsend_v2({msg.get('msgtype')})")


def _check(resp: httpx.Response, action: str) -> dict:
    try:
        data = resp.json()
    except Exception as exc:
        raise DingTalkError(f"{action} 响应非 JSON: {resp.text[:200]}") from exc
    if data.get("errcode", 0) != 0:
        raise DingTalkError(f"{action} 失败 errcode={data.get('errcode')} "
                            f"errmsg={data.get('errmsg')}")
    return data


# ---------- 配置读取与顶层封装 ----------
def dingtalk_config(cfg: Config) -> dict:
    sec = cfg.notify.get("dingtalk", {}) or {}
    return {
        "app_key": os.environ.get(str(sec.get("app_key_env", "DINGTALK_APP_KEY")), ""),
        "app_secret": os.environ.get(str(sec.get("app_secret_env", "DINGTALK_APP_SECRET")), ""),
        "agent_id": os.environ.get(
            str(sec.get("agent_id_env", "DINGTALK_AGENT_ID")),
            str(sec.get("agent_id", "") or "")),
        "userids": [
            u.strip() for u in os.environ.get(
                str(sec.get("userids_env", "DINGTALK_USERIDS")),
                "").split(",") if u.strip()
        ] or [str(u).strip() for u in (sec.get("userids") or []) if str(u).strip()],
    }


def push_weekly_report(cfg: Config, html_path: Path, title: str = "",
                       subtitle: str = "") -> bool:
    """推送周报 HTML 文件到钉钉指定个人；失败降级为 markdown 卡片。"""
    conf = dingtalk_config(cfg)
    missing = [k for k, v in conf.items() if not v]
    if missing:
        logger.warning("钉钉推送配置缺失: {}（请在 .env 配置）", ",".join(missing))
        return False
    try:
        client = DingTalkClient(conf["app_key"], conf["app_secret"])
        media_id = client.upload_file(Path(html_path))
        client.send_file_message(conf["agent_id"], conf["userids"], media_id)
        logger.info("钉钉推送成功：{}（{} 字节）→ {}",
                    Path(html_path).name, Path(html_path).stat().st_size,
                    ",".join(conf["userids"]))
        return True
    except Exception as exc:
        logger.warning("钉钉文件推送失败，降级为卡片: {}", exc)
        try:
            client = DingTalkClient(conf["app_key"], conf["app_secret"])
            text = (f"### {title}\n{subtitle}\n\n"
                    "完整周报（HTML）请在群/共享盘打开，"
                    "或稍后本机查看 data/reports 目录。")
            client.send_markdown_message(conf["agent_id"], conf["userids"], title, text)
            logger.info("钉钉卡片推送成功（降级）")
            return True
        except Exception as exc2:
            logger.error("钉钉卡片推送也失败: {}", exc2)
            return False
