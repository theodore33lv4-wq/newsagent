"""采集层公共定义：Article 数据类、HTTP 抓取助手、Collector 基类。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

from ..utils.config import Config, D


@dataclass
class Article:
    """采集到的候选条目（尚未下载正文）。"""

    url: str
    title: str
    source_id: str
    source_name: str
    published_at: Optional[datetime] = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:  # 日志友好
        return f"<Article {self.source_id}:{self.title[:24]!r}>"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_http_client(cfg: Config) -> httpx.Client:
    """统一 HTTP 客户端（UA、超时、连接池）。"""
    c = cfg.collect
    ua = str(c.get("user_agent",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"))
    return httpx.Client(
        timeout=httpx.Timeout(float(c.get("fetch_timeout_seconds", 20)), connect=10.0),
        headers={"User-Agent": ua, "Accept-Language": "zh-CN,zh;q=0.9"},
        follow_redirects=True,
    )


def fetch(url: str, cfg: Config, *, client: httpx.Client | None = None) -> httpx.Response:
    """带重试的 GET；重试耗尽后抛出 httpx.HTTPError。"""
    c = cfg.collect
    retries = int(c.get("retries", 2))
    own = client is None
    if own:
        client = new_http_client(cfg)
    try:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = client.get(url)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                    logger.warning("HTTP {} @ {}，重试 {}/{}",
                                   resp.status_code, url, attempt + 1, retries)
                    time.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except httpx.HTTPError as exc:
                last = exc
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
        raise last  # type: ignore[misc]
    finally:
        if own:
            client.close()


def polite_sleep(cfg: Config) -> None:
    """源间节流，降低被反爬风险。"""
    interval = float(cfg.collect.get("request_interval_seconds", 1.0))
    if interval > 0:
        time.sleep(interval)


class Collector(ABC):
    """单源采集器。collect() 返回候选 Article 列表；失败时应记日志但不抛出。"""

    def __init__(self, cfg: Config, source: dict):
        self.cfg = cfg
        self.source = D(source)

    @property
    def source_id(self) -> str:
        return str(self.source.get("id", "unknown"))

    @property
    def source_name(self) -> str:
        return str(self.source.get("name", self.source_id))

    @property
    def limit(self) -> int:
        return int(self.source.get("limit") or self.cfg.collect.get("per_source_limit", 40))

    @abstractmethod
    def collect(self) -> list[Article]:
        raise NotImplementedError
