"""搜狐号采集器（ITS114 等核心源）。

搜狐号主页 (mp.sohu.com) 是 SPA，正文以内置接口/页面 JS 数据取文章列表。
本实现采用多策略，任一成功即返回：
  A) 若干候选 API（猜名可调，见 _API_CANDIDATES）
  B) 主页 HTML 内嵌 JSON（window.__INITIAL_STATE__ 或 articleList 片段）

提示：真实接口与字段在“真实源探测验证”阶段实测后在此固化。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

from loguru import logger

from .base import Article, Collector, fetch, new_http_client

# 候选接口（按顺序尝试；xpt 为搜狐号标识）
_API_CANDIDATES = [
    "https://api.mp.sohu.com/mp/v2/article/list?xpt={xpt}&pageNo=1&pageSize={limit}",
    "https://v2.sohu.com/author-page-api/author-articles/pc/{xpt}?page=1&size={limit}",
    "https://mp.sohu.com/api_v3/profile/v2/profileInfo?xpt={xpt}",
]

_TIME_KEYS = ("publicTime", "publishTime", "createTime", "releaseTime", "time")
_URL_KEYS = ("url", "link", "articleUrl", "mpUrl", "profileUrl")
_ID_KEYS = ("originalId", "articleId", "id")


class SohuAccountCollector(Collector):
    """type: sohu_account 的源。"""

    def collect(self) -> list[Article]:
        xpt = str(self.source.get("xpt", "")).strip()
        profile_url = str(self.source.get("profile_url") or "").strip()
        if not xpt and not profile_url:
            logger.error("[{}] 缺少 xpt/profile_url 配置", self.source_id)
            return []

        # 策略 A：候选 API
        for tmpl in _API_CANDIDATES:
            try:
                url = tmpl.format(xpt=xpt, limit=self.limit)
                resp = fetch(url, self.cfg)
                data = json.loads(resp.text)
                items = self._extract_items(data)
                if items:
                    filtered = self._drop_blacklisted(items)
                    articles = [self._to_article(it) for it in filtered]
                    articles = [a for a in articles if a.url and a.title]
                    articles = articles[: self.limit]
                    logger.info("[{}] 策略A(API {}) 采集 {} 条", self.source_id,
                                url.split("?")[0], len(articles))
                    return articles
            except Exception as exc:
                logger.debug("[{}] 策略A 失败 {}: {}", self.source_id, tmpl.split("?")[0], exc)

        # 策略 B：主页内嵌 JSON
        if profile_url:
            try:
                articles = self._collect_from_page(profile_url)
                if articles:
                    logger.info("[{}] 策略B(页面JSON) 采集 {} 条", self.source_id, len(articles))
                    return articles
                logger.debug("[{}] 策略B 未在页面中发现文章数据", self.source_id)
            except Exception as exc:
                logger.debug("[{}] 策略B 失败: {}", self.source_id, exc)

        logger.warning("[{}] 所有采集策略均未取得文章（{}）", self.source_id, xpt)
        return []

    # ---- 数据提取 ----
    @staticmethod
    def _extract_items(data: Any) -> list[dict]:
        """自任意嵌套结构递归挖出「含 title 且有链接/ID 的文章字典」。"""
        hits: list[dict] = []

        def walk(node: Any) -> None:
            nonlocal hits
            if isinstance(node, dict):
                if ("title" in node or "titleDesc" in node) and (
                        any(k in node for k in _URL_KEYS + _ID_KEYS)):
                    hits.append(node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        return hits

    @staticmethod
    def _drop_blacklisted(items: list[dict]) -> list[dict]:
        """过滤掉非文章项（如普通媒体卡片）。"""
        out = []
        for it in items:
            t = str(it.get("title") or it.get("titleDesc") or "")
            if t in ("", "最新", "全部", "视频", "图集"):
                continue
            out.append(it)
        return out

    def _to_article(self, it: dict) -> Article:
        title = str(it.get("title") or it.get("titleDesc") or "").strip()
        url = ""
        for k in _URL_KEYS:
            v = str(it.get(k) or "").strip()
            if v:
                url = v
                break
        if not url:
            for k in _ID_KEYS:
                v = str(it.get(k) or "").strip()
                if v:
                    url = f"https://www.sohu.com/a/{v}"
                    break
        url = urljoin("https://www.sohu.com/", url)

        published = self._parse_time(it)
        return Article(
            url=url, title=title,
            source_id=self.source_id, source_name=self.source_name,
            published_at=published,
        )

    @staticmethod
    def _parse_time(it: dict) -> Optional[datetime]:
        for k in _TIME_KEYS:
            v = it.get(k)
            if v is None:
                continue
            # 毫秒/秒时间戳
            if isinstance(v, (int, float)):
                ts = float(v)
                if ts > 1e12:
                    ts /= 1000.0
                try:
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                except (OverflowError, OSError, ValueError):
                    continue
            s = str(v).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
                try:
                    return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
        return None

    def _collect_from_page(self, profile_url: str) -> list[Article]:
        with new_http_client(self.cfg) as client:
            resp = fetch(profile_url, self.cfg, client=client)
            text = resp.text
            candidates: list[str] = []
            m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;", text, re.S)
            if m:
                candidates.append(m.group(1))
            m = re.search(r'"articleList"\s*:\s*(\[.*?\])', text, re.S)
            if m:
                candidates.append(m.group(1))
            items: list[dict] = []
            for blob in candidates:
                try:
                    data = json.loads(blob)
                except Exception:
                    continue
                items.extend(self._extract_items(data))
            articles = [self._to_article(it) for it in self._drop_blacklisted(items)]
            return [a for a in articles if a.url and a.title][: self.limit]
