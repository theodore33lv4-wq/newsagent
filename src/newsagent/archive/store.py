"""存储层：文件落盘（HTML 快照 / 正文 JSON）+ SQLite 索引与查询接口。

目录结构（data_dir 下）：
  raw/{week}/{guid}.html         原始网页快照
  articles/{week}/{guid}.json    正文与元数据
  index.sqlite3                  检索索引（未来 Web 站直接复用 query() 接口）
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ..collect.base import Article
from ..collect.dedup import content_key, title_key, url_key
from ..utils.dates import iso_week, tz_for
from ..utils.config import Config
from .downloader import FetchedContent

_STATUS_NEW = "archived"          # 已存档，待分类
_STATUS_CLASSIFIED = "classified" # 已分类索引


def guid_for(url: str) -> str:
    """文件与行主键：规范化 URL 的 sha1 前 16 位。"""
    return url_key(url)[:16]


class Store:
    """SQLite 索引 + 文件系统存档。线程安全（线程内自建连接）。"""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(exist_ok=True)
        (self.data_dir / "articles").mkdir(exist_ok=True)
        self.db_path = self.data_dir / "index.sqlite3"
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    guid         TEXT PRIMARY KEY,
                    week         TEXT NOT NULL,
                    url          TEXT NOT NULL,
                    url_key      TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    title_key    TEXT NOT NULL,
                    source_id    TEXT NOT NULL,
                    source_name  TEXT NOT NULL,
                    published_at TEXT,
                    fetched_at   TEXT NOT NULL,
                    html_file    TEXT,
                    text_file    TEXT,
                    text_chars   INTEGER,
                    content_hash TEXT,
                    extractor    TEXT,
                    status       TEXT NOT NULL DEFAULT 'archived',
                    relevance    INTEGER,
                    tags_json    TEXT,
                    summary      TEXT,
                    keywords_json TEXT,
                    companies_json TEXT,
                    importance   INTEGER,
                    note         TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_week   ON articles(week);
                CREATE INDEX IF NOT EXISTS idx_status ON articles(status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_url_key ON articles(url_key);
                CREATE INDEX IF NOT EXISTS idx_title_key ON articles(title_key);
                """
            )

    # ---------- 去重装载 ----------
    def seen_keys(self) -> tuple[list[str], list[str]]:
        """历史 URL key / 标题 key（供 DedupChecker 装载）。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT url_key, title_key FROM articles").fetchall()
        return [r["url_key"] for r in rows], [r["title_key"] for r in rows]

    # ---------- 保存 ----------
    def save_article(self, week: str, article: Article,
                     content: FetchedContent) -> Optional[dict]:
        """落盘快照/正文并写入索引。URL 已存在 → 返回 None（调用方视为重复）。"""
        ukey = url_key(article.url)
        with self._connect() as conn:
            dup = conn.execute("SELECT 1 FROM articles WHERE url_key=?", (ukey,)).fetchone()
            if dup:
                return None

        guid = guid_for(article.url)
        week_dir_raw = self.data_dir / "raw" / week
        week_dir_art = self.data_dir / "articles" / week
        week_dir_raw.mkdir(parents=True, exist_ok=True)
        week_dir_art.mkdir(parents=True, exist_ok=True)

        html_file = week_dir_raw / f"{guid}.html"
        html_file.write_text(content.html, encoding="utf-8", errors="replace")

        text = content.text
        chash = content_key(text) if text else None
        fetched_at = datetime.now(timezone.utc).isoformat()
        published_at = _to_iso(article.published_at)

        record = {
            "guid": guid, "week": week,
            "url": article.url, "url_key": ukey,
            "title": _clean_title(content.meta_title) or article.title,
            "title_key": title_key(_clean_title(content.meta_title) or article.title),
            "source_id": article.source_id, "source_name": article.source_name,
            "published_at": published_at,
            "fetched_at": fetched_at,
            "html_file": str(Path("raw") / week / html_file.name),
            "text_file": None,
            "text_chars": len(text) if text else 0,
            "content_hash": chash,
            "extractor": content.extractor,
            "status": _STATUS_NEW,
            "note": None if text else "正文待人工（提取失败，保留快照）",
        }

        art_json = {
            **{k: v for k, v in record.items() if k not in ("html_file", "text_file")},
            "text": text,
        }
        json_file = week_dir_art / f"{guid}.json"
        json_file.write_text(json.dumps(art_json, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        record["text_file"] = str(Path("articles") / week / json_file.name)

        with self._connect() as conn:
            cols = list(record.keys())
            conn.execute(
                f"INSERT INTO articles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [record[c] for c in cols],
            )
        return record

    # ---------- 更新 ----------
    def update_classification(self, guid: str, *, relevance: int,
                              tags: list[str], summary: str | None,
                              keywords: list[str] | None,
                              companies: list[str] | None,
                              importance: int | None, note: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE articles SET status=?, relevance=?, tags_json=?, summary=?,
                   keywords_json=?, companies_json=?, importance=?, note=COALESCE(?, note)
                   WHERE guid=?""",
                (_STATUS_CLASSIFIED, relevance,
                 json.dumps(tags or [], ensure_ascii=False),
                 summary,
                 json.dumps(keywords or [], ensure_ascii=False),
                 json.dumps(companies or [], ensure_ascii=False),
                 importance, note, guid),
            )

    def set_note(self, guid: str, note: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE articles SET note=? WHERE guid=?", (note, guid))

    def article_text(self, row: dict) -> str | None:
        """读取存档的正文文本（从 text_file 的 JSON）。"""
        text_file = row.get("text_file")
        if not text_file:
            return None
        try:
            data = json.loads((self.data_dir / text_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        text = data.get("text")
        return text if isinstance(text, str) and text else None

    # ---------- 查询（未来 Web 站复用的检索接口） ----------
    def query(self, *, week: str | None = None, tag: str | None = None,
              keyword: str | None = None, company: str | None = None,
              importance: int | None = None, status: str | None = None,
              relevant_only: bool = True, limit: int = 200) -> list[dict]:
        """按周/标签/关键词/公司/重要度/状态检索。

        - tag：精确匹配标签路径（如 '厂商动态/集成商动态'，子串匹配实现二级包容）
        - keyword：标题或摘要 LIKE
        - company：companies_json 子串
        - relevant_only：默认只返回 LLM 判定相关的条目
        """
        where, params = [], []
        if week:
            where.append("week=?")
            params.append(week)
        if status:
            where.append("status=?")
            params.append(status)
        if relevant_only:
            where.append("relevance=1")
        if tag:
            where.append("tags_json LIKE ?")
            params.append(f'%"{tag}"%')
        if company:
            where.append("companies_json LIKE ?")
            params.append(f'%"{company}"%')
        if keyword:
            where.append("(title LIKE ? OR summary LIKE ? OR keywords_json LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like, like])
        if importance is not None:
            where.append("importance=?")
            params.append(int(importance))

        sql = "SELECT * FROM articles"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += (" ORDER BY importance DESC NULLS LAST, "
                "COALESCE(published_at, fetched_at) DESC LIMIT ?")
        params.append(int(limit))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_week(self, week: str, *, classified_only: bool = True) -> list[dict]:
        return self.query(week=week, relevant_only=classified_only, limit=500)

    def close(self) -> None:
        pass  # 每次操作独立连接，无需常驻句柄

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------- 辅助 ----------
def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("tags_json", "keywords_json", "companies_json"):
        try:
            d[k[:-5]] = json.loads(d.pop(k)) if d.get(k) else []
        except Exception:
            d[k[:-5]] = []
    return d


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    t = title.strip()
    return t or None


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()
