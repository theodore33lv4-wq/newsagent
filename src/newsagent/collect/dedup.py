"""三层去重：URL 规范化 → 标题归一化 → （内容哈希由存档层做二次校验）。

- URL 规范化：统一小写 host、去锚点、去跟踪参数（utm_*/spm）、去尾斜杠；
- 标题归一化：NFKC + 仅保留中英文与数字（去空白/标点/大小写差异）；
- 内容哈希：正文抽取后由存档层二次校验（见 archive.store）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .base import Article

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_NAMES = {"spm", "from", "share_token", "share_medium", "share_plat",
                   "share_source", "gsm", "bd_page_type", "_t"}
_KEEP_CHARS = re.compile(r"[^0-9a-zA-Z\u4e00-\u9fff]")


def normalize_url(url: str) -> str:
    """URL 规范化，用于 URL 层去重。"""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return url.strip()
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    path = p.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    keep = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
            if not k.lower().startswith(_TRACKING_PREFIXES)
            and k.lower() not in _TRACKING_NAMES]
    query = urlencode(keep)
    return urlunparse((scheme, host, path, p.params, query, ""))


def normalize_title(title: str) -> str:
    """标题归一化（跨源重发、大小写/标点差异）。"""
    text = unicodedata.normalize("NFKC", title or "")
    return _KEEP_CHARS.sub("", text).lower()


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def url_key(url: str) -> str:
    return _sha1(normalize_url(url))


def title_key(title: str) -> str:
    return _sha1(normalize_title(title))


def content_key(text: str, chars: int = 800) -> str:
    """正文内容哈希（存档层二次校验用）。"""
    norm = _KEEP_CHARS.sub("", unicodedata.normalize("NFKC", text or ""))[:chars]
    return _sha1(norm)


class DedupChecker:
    """基于历史 URL/标题 key 集合的去重检查器（纯内存，由调用方从 SQLite 装载）。"""

    def __init__(self, seen_url_keys: Iterable[str] = (),
                 seen_title_keys: Iterable[str] = ()):
        self._urls: set[str] = set(seen_url_keys)
        self._titles: set[str] = set(seen_title_keys)

    def seen_count(self) -> tuple[int, int]:
        return len(self._urls), len(self._titles)

    def is_new(self, article: Article) -> bool:
        """URL 与标题均未出现过 → 新条目。"""
        return (url_key(article.url) not in self._urls
                and title_key(article.title) not in self._titles)

    def add(self, article: Article) -> None:
        self._urls.add(url_key(article.url))
        self._titles.add(title_key(article.title))

    def dedupe(self, articles: Iterable[Article]) -> list[Article]:
        """就地过滤：仅返回新条目并登记。"""
        kept: list[Article] = []
        dropped = 0
        for a in articles:
            if self.is_new(a):
                self.add(a)
                kept.append(a)
            else:
                dropped += 1
        if dropped:
            from loguru import logger
            logger.debug("去重丢弃 {} 条（URL/标题已存在）", dropped)
        return kept
