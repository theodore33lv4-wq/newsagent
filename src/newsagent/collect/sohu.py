"""搜狐号采集器（ITS114 等核心源）。

策略（按优先级）：
  A) 主页内嵌 JSON：mp.sohu.com 的 profile 页服务端渲染了
     window.blockRenderData = {...}（含文章列表：title/brief/url/postTime），
     用 json.JSONDecoder().raw_decode 解析后递归抽取文章条目 —— 实测可用（2026-08）；
  B) 若干候选 API（保留，作为页面结构变化时的备选）；
  C) 页面内嵌 articleList 片段兜底。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

from loguru import logger

from .base import Article, Collector, fetch, new_http_client, polite_sleep

# 候选接口（按顺序尝试；xpt 为搜狐号标识）
_API_CANDIDATES = [
    "https://api.mp.sohu.com/mp/v2/article/list?xpt={xpt}&pageNo={page}&pageSize={limit}",
    "https://v2.sohu.com/author-page-api/author-articles/pc/{xpt}?page={page}&size={limit}",
    "https://mp.sohu.com/api_v3/profile/v2/profileInfo?xpt={xpt}",
]

_TIME_KEYS = ("postTime", "publicTime", "publishTime", "createTime", "releaseTime", "time")
_URL_KEYS = ("url", "link", "articleUrl", "mpUrl", "profileUrl")
_ID_KEYS = ("originalId", "articleId", "id")
_BLOCK_SCRIPT_RE = re.compile(r"window\.blockRenderData\s*=\s*")


class SohuAccountCollector(Collector):
    """type: sohu_account 的源。"""

    def collect(self) -> list[Article]:
        xpt = str(self.source.get("xpt", "")).strip()
        profile_url = str(self.source.get("profile_url") or "").strip()
        if not profile_url:
            raise ValueError("缺少 profile_url 配置")

        # 策略 A：主页内嵌 JSON（实测有效主路径）
        articles: list[Article] = []
        try:
            articles = list({a.url: a for a in self._collect_from_page(profile_url)}.values())
        except Exception as exc:
            logger.debug("[{}] 策略A 失败: {}", self.source_id, exc)

        self.coverage_warning = "作者主页及候选接口的历史覆盖范围未核实为整周"
        seen = {a.url for a in articles}
        max_pages = max(1, int(self.source.get("max_pages", 3)))
        for tmpl in _API_CANDIDATES:
            if len(articles) >= self.limit or not xpt:
                break
            paged = "{page}" in tmpl
            visited: set[str] = set()
            for page in range(1, (max_pages if paged else 1) + 1):
                polite_sleep(self.cfg)
                try:
                    url = tmpl.format(xpt=xpt, limit=min(self.limit, 20), page=page)
                    items = self._extract_items(json.loads(fetch(url, self.cfg).text))
                except Exception as exc:
                    if visited:
                        self.errors.append(f"搜狐分页在第 {page} 页失败: {exc}")
                    logger.debug("[{}] 候选 API 失败: {}", self.source_id, exc)
                    break
                batch = [self._to_article(it) for it in self._drop_blacklisted(items)]
                urls = {a.url for a in batch if a.title}
                if not urls - visited:
                    break
                visited.update(urls)
                for article in batch:
                    if article.url not in seen and article.title:
                        articles.append(article)
                        seen.add(article.url)
                if len(articles) >= self.limit:
                    break
        if not articles:
            raise ValueError("所有搜狐采集策略均未取得文章")
        if len(articles) >= self.limit:
            self.coverage_warning = f"已达到单源上限 {self.limit}"
        return articles[:self.limit]

    # ---- 页面 JSON 提取 ----
    def _collect_from_page(self, profile_url: str) -> list[Article]:
        with new_http_client(self.cfg) as client:
            resp = fetch(profile_url, self.cfg, client=client)
            text = resp.text

            # 主路径：window.blockRenderData（raw_decode 只消费首个合法 JSON 值）
            data = None
            m = _BLOCK_SCRIPT_RE.search(text)
            if m:
                try:
                    data, _ = json.JSONDecoder().raw_decode(text[m.end():])
                except Exception as exc:
                    logger.debug("[{}] blockRenderData 解析失败: {}", self.source_id, exc)

            items: list[dict] = []
            if data is not None:
                items = self._extract_items(data)
            if not items:
                # 兜底：articleList 片段
                try:
                    m = re.search(r'"articleList"\s*:\s*(\[.*?\])', text, re.S)
                    if m:
                        items = self._extract_items(json.loads(m.group(1)))
                except Exception:
                    pass

            articles = [self._to_article(it) for it in self._drop_blacklisted(items)]
            return [a for a in articles if a.url and a.title]

    # ---- 数据提取 ----
    @staticmethod
    def _extract_items(data: Any) -> list[dict]:
        """自任意嵌套结构递归挖出「含 title/url 的搜狐文章字典」。"""
        hits: list[dict] = []

        def walk(node: Any) -> None:
            nonlocal hits
            if isinstance(node, dict):
                url = str(node.get("url") or "")
                if ("title" in node and "authorName" in node
                        and "/a/" in url and node.get("id")):
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
            try:
                return datetime.fromisoformat(s.replace("/", "-"))
            except ValueError:
                continue
        return None
