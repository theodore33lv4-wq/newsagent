"""存储层：文件落盘（HTML 快照 / 正文 JSON）+ SQLite 索引与查询接口。

目录结构（data_dir 下）：
  raw/{week}/{guid}.html         原始网页快照
  articles/{week}/{guid}.json    正文与元数据
  index.sqlite3                  检索索引（未来 Web 站直接复用 query() 接口）
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from ..collect.base import Article
from ..collect.dedup import content_key, title_key, url_key
from .downloader import MAX_TEXT_CHARS, FetchedContent

_STATUS_NEW = "archived"          # 已存档，待分类
_STATUS_CLASSIFIED = "classified" # 已分类索引
_STATUS_FILTERED = "filtered"     # 已过滤（非新闻页 / 不在目标周 / 无日期）
_STATUS_RETRY = "retryable"       # 快照已保存，正文待重新下载
_STATUS_DATE_PENDING = "date_pending"


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

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

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

    @staticmethod
    def seen_urls(data_dir: Path) -> list[str]:
        """只读加载去重 URL。首次预览时无需创建数据库。"""
        path = (data_dir / "index.sqlite3").resolve()
        if not path.is_file():
            return []
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            rows = conn.execute("SELECT url_key FROM articles WHERE status<>?", (_STATUS_RETRY,))
            return [row[0] for row in rows]

    # ---------- 保存 ----------
    def save_article(self, week: str, article: Article,
                     content: FetchedContent, *, status: str = _STATUS_NEW,
                     note: str | None = None,
                     published_iso: str | None = None) -> Optional[dict]:
        """落盘快照/正文并写入索引。

        - status：archived（待分类）/ filtered（页面体检未通过）/ retryable（提取失败）
        - published_iso：页面抽取或列表层解析出的发布日期（优先于采集层）
        URL 已存在且无需重试 → 返回 None。
        """
        ukey = url_key(article.url)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT guid, status FROM articles WHERE url_key=?", (ukey,)).fetchone()
            if existing and (existing["status"] != _STATUS_RETRY or not content.html):
                return None
            guid = existing["guid"] if existing else guid_for(article.url)
            record = self._archive(guid, week, article, content, status, note, published_iso)
            cols = list(record)
            if existing:
                fields = [c for c in cols if c != "guid"]
                assignments = ",".join(f"{c}=?" for c in fields)
                conn.execute(f"UPDATE articles SET {assignments} WHERE guid=?",
                             [record[c] for c in fields] + [guid])
            else:
                conn.execute(
                    f"INSERT INTO articles ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [record[c] for c in cols],
                )
        return record

    def _archive(self, guid: str, week: str, article: Article, content: FetchedContent,
                 status: str, note: str | None, published_iso: str | None) -> dict:
        ukey = url_key(article.url)
        week_dir_raw = self.data_dir / "raw" / week
        week_dir_art = self.data_dir / "articles" / week
        week_dir_raw.mkdir(parents=True, exist_ok=True)
        week_dir_art.mkdir(parents=True, exist_ok=True)

        html_file = week_dir_raw / f"{guid}.html"
        if content.html:
            html_file.write_text(content.html, encoding="utf-8", errors="replace")

        text = content.text
        chash = _full_text_hash(text)
        fetched_at = datetime.now(timezone.utc).isoformat()
        published_at = published_iso or _to_iso(article.published_at)
        title = _clean_title(content.meta_title) or article.title

        record = {
            "guid": guid, "week": week,
            "url": article.url, "url_key": ukey,
            "title": title,
            "title_key": title_key(title),
            "source_id": article.source_id, "source_name": article.source_name,
            "published_at": published_at,
            "fetched_at": fetched_at,
            "html_file": str(Path("raw") / week / html_file.name) if content.html else None,
            "text_file": None,
            "text_chars": len(text) if text else 0,
            "content_hash": chash,
            "extractor": content.extractor,
            "status": status,
            "note": note or (None if text else "正文待人工（提取失败，保留快照）"),
        }

        art_json = {
            **{k: v for k, v in record.items() if k not in ("html_file", "text_file")},
            "text": text,
        }
        json_file = week_dir_art / f"{guid}.json"
        json_file.write_text(json.dumps(art_json, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        record["text_file"] = str(Path("articles") / week / json_file.name)

        return record

    # ---------- 更新 ----------
    def update_classification(self, guid: str, *, relevance: int,
                              tags: list[str], summary: str | None,
                              keywords: list[str] | None,
                              companies: list[str] | None,
                              importance: int | None, note: str | None = None,
                              published_iso: str | None = None, week: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE articles SET status=?, relevance=?, tags_json=?, summary=?,
                   keywords_json=?, companies_json=?, importance=?, note=COALESCE(?, note),
                   published_at=COALESCE(?, published_at), week=COALESCE(?, week)
                   WHERE guid=?""",
                (_STATUS_CLASSIFIED, relevance,
                 json.dumps(tags or [], ensure_ascii=False),
                 summary,
                 json.dumps(keywords or [], ensure_ascii=False),
                 json.dumps(companies or [], ensure_ascii=False),
                 importance, note, published_iso, week, guid),
            )

    def mark_filtered(self, guid: str, reason: str) -> None:
        """标记为已过滤（页面体检未通过 / 日期不在目标周 / 无日期）。

        仍保留记录与快照，便于审计；relevance=0 保证其不会进入周报。
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE articles SET status=?, relevance=0, note=? WHERE guid=?",
                (_STATUS_FILTERED, f"已过滤：{reason}", guid),
            )

    def set_published(self, guid: str, published_iso: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE articles SET published_at=? WHERE guid=?",
                         (published_iso, guid))

    def set_week(self, guid: str, week: str) -> None:
        """调整条目归属周（日期兜底判定出更晚的周时使用）。"""
        with self._connect() as conn:
            conn.execute("UPDATE articles SET week=? WHERE guid=?", (week, guid))

    def list_filtered(self, week: str) -> list[dict]:
        """列出某一周被过滤的条目（用于核对后再恢复）。"""
        return self.query(week=week, status=_STATUS_FILTERED,
                          relevant_only=False, limit=-1)

    def recover_filtered(self, week: str, guids: list[str]) -> int:
        """恢复指定条目。空正文条目恢复为待重新下载。"""
        with self._connect() as conn:
            cur = conn.executemany(
                "UPDATE articles SET status=CASE WHEN text_chars>0 THEN ? ELSE ? END, "
                "relevance=NULL, note=? WHERE week=? AND status=? AND guid=?",
                [(_STATUS_NEW, _STATUS_RETRY, "已恢复待处理（此前被过滤）", week,
                  _STATUS_FILTERED, guid) for guid in dict.fromkeys(guids)],
            )
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def upsert_article(self, record: dict) -> str:
        """从存档导入。SQLite 已有条目的日期、归属周和处理状态优先。"""
        guid = str(record.get("guid") or "").strip()
        if not guid:
            raise ValueError("record 缺少 guid")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            exists = conn.execute("SELECT guid FROM articles WHERE guid=? OR url_key=?",
                                  (guid, record["url_key"])).fetchone()
            if exists:
                conn.execute(
                    "UPDATE articles SET html_file=COALESCE(html_file, ?), "
                    "text_file=COALESCE(text_file, ?) WHERE guid=?",
                    (record.get("html_file"), record.get("text_file"), exists["guid"]),
                )
                return "updated"
            cols = ["guid", "week", "url", "url_key", "title", "title_key",
                    "source_id", "source_name", "published_at", "fetched_at",
                    "html_file", "text_file", "text_chars", "content_hash",
                    "extractor", "status", "note"]
            status = record.get("status")
            if status not in (_STATUS_FILTERED, _STATUS_RETRY):
                status = _STATUS_NEW
            values = [guid] + [record.get(k) for k in cols[1:-2]] + [status, record.get("note")]
            conn.execute(
                f"INSERT INTO articles ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", values)
            return "inserted"

    def reset_week(self, week: str) -> int:
        """只重置指定归属周已分类的条目。"""
        with self._connect() as conn:
            result = conn.execute(
                "UPDATE articles SET status=?, relevance=NULL, tags_json=NULL, summary=NULL, "
                "keywords_json=NULL, companies_json=NULL, importance=NULL, note=? "
                "WHERE week=? AND status=?",
                (_STATUS_NEW, "已重置待分类", week, _STATUS_CLASSIFIED),
            )
        return result.rowcount

    def set_note(self, guid: str, note: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE articles SET note=? WHERE guid=?", (note, guid))

    def mark_date_pending(self, guid: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE articles SET status=?, note=? WHERE guid=?",
                         (_STATUS_DATE_PENDING, "发布日期未确定，保留待核验", guid))

    def article_text(self, row: dict) -> str | None:
        """读取存档的正文文本（从 text_file 的 JSON）。"""
        text_file = row.get("text_file")
        if not text_file:
            return None
        try:
            data = json.loads((self.data_dir / text_file).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        text = data.get("text") if isinstance(data, dict) else None
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
        rows = self.query(week=week, relevant_only=classified_only, limit=-1,
                          status=_STATUS_CLASSIFIED if classified_only else None)
        for row in rows:
            if not (row.get("content_hash") or "").startswith("v2:"):
                body = self.article_text(row)
                row["content_hash"] = _full_text_hash(body)
        return rows

    def close(self) -> None:
        pass  # 每次操作独立连接，无需常驻句柄

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ---------- 辅助 ----------
def _full_text_hash(text: str | None) -> str | None:
    # 达到提取上限时，正文可能已经截断。
    return content_key(text) if text and len(text) < MAX_TEXT_CHARS else None


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
