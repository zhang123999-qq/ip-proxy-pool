"""
SQLite 存储层 — 数据访问对象（DAO）
==================================

ProxyDAO 负责 ProxyItem 与 SQLite 表之间的读写转换。

设计原则：
- 所有写操作走 execute()（同步，由 asyncio 线程池包装）
- 增量 upsert（不是全量覆盖，性能高）
- 查询走索引（score / last_used / source）
"""
import json
import sqlite3
import time
from typing import List, Optional

from .db import db
from models import ProxyItem, ProxyProtocol


# ============================================================
# 行 → ProxyItem / ProxyItem → 行
# ============================================================
def _row_to_item(row) -> ProxyItem:
    """sqlite3.Row → ProxyItem"""
    return ProxyItem(
        ip=row["ip"],
        port=int(row["port"]),
        protocol=ProxyProtocol(row["protocol"]),
        score=int(row["score"]),
        response_time=float(row["response_time"]),
        success_count=int(row["success_count"]),
        fail_count=int(row["fail_count"]),
        last_check=float(row["last_check"]),
        created_at=float(row["created_at"]),
        source=row["source"],
        last_used_for_crawl=float(row["last_used_for_crawl"]),
    )


def _item_to_row(item: ProxyItem) -> tuple:
    """ProxyItem → 插入参数元组"""
    return (
        item.ip,
        int(item.port),
        item.protocol.value,
        int(item.score),
        float(item.response_time),
        int(item.success_count),
        int(item.fail_count),
        float(item.last_check),
        float(item.created_at),
        item.source,
        float(item.last_used_for_crawl),
    )


# ============================================================
# ProxyDAO
# ============================================================
class ProxyDAO:
    """
    代理数据访问对象

    用同步 sqlite3（DAO 内部不加锁，调用方负责并发控制）。
    所有方法抛异常时由调用方兜底。
    """

    # ----- 增 -----
    def insert(self, item: ProxyItem) -> bool:
        """插入单条（已存在则忽略）"""
        conn = db.get_conn()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO proxies
            (ip, port, protocol, score, response_time, success_count,
             fail_count, last_check, created_at, source, last_used_for_crawl)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            _item_to_row(item),
        )
        conn.commit()
        return cur.rowcount > 0

    def upsert(self, item: ProxyItem) -> None:
        """插入或更新（按 ip+port 主键）"""
        conn = db.get_conn()
        conn.execute(
            """
            INSERT INTO proxies
            (ip, port, protocol, score, response_time, success_count,
             fail_count, last_check, created_at, source, last_used_for_crawl)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ip, port) DO UPDATE SET
                protocol=excluded.protocol,
                score=excluded.score,
                response_time=excluded.response_time,
                success_count=excluded.success_count,
                fail_count=excluded.fail_count,
                last_check=excluded.last_check,
                created_at=excluded.created_at,
                source=excluded.source,
                last_used_for_crawl=excluded.last_used_for_crawl
            """,
            _item_to_row(item),
        )
        conn.commit()

    def upsert_many(self, items: List[ProxyItem]) -> int:
        """批量 upsert（事务内，性能最高）"""
        if not items:
            return 0
        conn = db.get_conn()
        try:
            conn.execute("BEGIN")
            conn.executemany(
                """
                INSERT INTO proxies
                (ip, port, protocol, score, response_time, success_count,
                 fail_count, last_check, created_at, source, last_used_for_crawl)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(ip, port) DO UPDATE SET
                    protocol=excluded.protocol,
                    score=excluded.score,
                    response_time=excluded.response_time,
                    success_count=excluded.success_count,
                    fail_count=excluded.fail_count,
                    last_check=excluded.last_check,
                    created_at=excluded.created_at,
                    source=excluded.source,
                    last_used_for_crawl=excluded.last_used_for_crawl
                """,
                [_item_to_row(it) for it in items],
            )
            conn.commit()
            return len(items)
        except Exception:
            conn.rollback()
            raise

    # ----- 删 -----
    def delete(self, ip: str, port: int) -> bool:
        """删除单条"""
        conn = db.get_conn()
        cur = conn.execute(
            "DELETE FROM proxies WHERE ip=? AND port=?", (ip, port)
        )
        conn.commit()
        return cur.rowcount > 0

    def delete_all(self) -> None:
        """清空代理表"""
        conn = db.get_conn()
        conn.execute("DELETE FROM proxies")
        conn.commit()

    # ----- 查 -----
    def get(self, ip: str, port: int) -> Optional[ProxyItem]:
        """查单条"""
        row = db.get_conn().execute(
            "SELECT * FROM proxies WHERE ip=? AND port=?",
            (ip, port),
        ).fetchone()
        return _row_to_item(row) if row else None

    def get_all(self) -> List[ProxyItem]:
        """查全部"""
        rows = db.get_conn().execute("SELECT * FROM proxies").fetchall()
        return [_row_to_item(r) for r in rows]

    def get_by_score(self, min_score: int = 0, limit: int = 100) -> List[ProxyItem]:
        """按分数降序取（top_n 用）"""
        rows = db.get_conn().execute(
            "SELECT * FROM proxies WHERE score>=? ORDER BY score DESC LIMIT ?",
            (min_score, limit),
        ).fetchall()
        return [_row_to_item(r) for r in rows]

    def get_crawl_candidates(
        self,
        limit: int,
        min_score: int,
        cooldown_sec: int,
    ) -> List[ProxyItem]:
        """
        选取爬源候选（走索引，性能高）

        条件：
        - score >= min_score
        - last_used_for_crawl == 0（从未用过）
          或 距离上次使用 >= cooldown_sec
        """
        now = time.time()
        rows = db.get_conn().execute(
            """
            SELECT * FROM proxies
            WHERE score >= ?
              AND (last_used_for_crawl = 0
                   OR (? - last_used_for_crawl) >= ?)
            ORDER BY score DESC
            LIMIT ?
            """,
            (min_score, now, cooldown_sec, limit),
        ).fetchall()
        return [_row_to_item(r) for r in rows]

    def count(self) -> int:
        """总数"""
        return db.get_conn().execute(
            "SELECT COUNT(*) FROM proxies"
        ).fetchone()[0]

    def stats(self) -> dict:
        """统计（平均/最高/最低分 + 协议分布）"""
        conn = db.get_conn()
        row = conn.execute(
            """
            SELECT COUNT(*) AS size,
                   COALESCE(AVG(score),0) AS avg,
                   COALESCE(MAX(score),0) AS maxs,
                   COALESCE(MIN(score),0) AS mins
            FROM proxies
            """
        ).fetchone()
        proto = {}
        for r in conn.execute(
            "SELECT protocol, COUNT(*) AS c FROM proxies GROUP BY protocol"
        ).fetchall():
            proto[r["protocol"]] = r["c"]
        return {
            "size": row["size"],
            "avg_score": round(row["avg"], 2),
            "max_score": row["maxs"],
            "min_score": row["mins"],
            "http": proto.get("http", 0),
            "https": proto.get("https", 0),
            "both": proto.get("both", 0),
        }

    # ----- 爬源历史 -----
    def log_crawl(self, ip: str, port: int, source: str,
                  success: bool, duration_ms: int = 0) -> None:
        """记录一次爬源行为"""
        conn = db.get_conn()
        conn.execute(
            """
            INSERT INTO crawl_history
            (ip, port, source, success, duration_ms, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (ip, port, source, 1 if success else 0, duration_ms, time.time()),
        )
        conn.commit()

    def get_crawl_history(self, limit: int = 50) -> List[dict]:
        """查最近的爬源行为"""
        rows = db.get_conn().execute(
            """
            SELECT * FROM crawl_history ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# 全局单例
# ============================================================
dao = ProxyDAO()


# ============================================================
# 自定义源数据模型
# ============================================================
class CustomSource:
    """用户通过 API 配置的代理源（运行时注册到爬虫管理器）"""

    __slots__ = (
        "id", "name", "type", "url", "proto", "config",
        "enabled", "created_at", "updated_at",
    )

    def __init__(
        self,
        name: str,
        type_: str,
        url: str,
        proto: str = "http",
        config: Optional[dict] = None,
        enabled: bool = True,
        id: Optional[int] = None,
        created_at: float = 0.0,
        updated_at: float = 0.0,
    ):
        self.id = id
        self.name = name
        self.type = type_
        self.url = url
        self.proto = proto
        self.config = config or {}
        self.enabled = enabled
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "proto": self.proto,
            "config": self.config,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row) -> "CustomSource":
        cfg_raw = row["config_json"]
        try:
            config = json.loads(cfg_raw) if cfg_raw else {}
        except Exception:
            config = {}
        return cls(
            id=row["id"],
            name=row["name"],
            type_=row["type"],
            url=row["url"],
            proto=row["proto"],
            config=config,
            enabled=bool(row["enabled"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )


# ============================================================
# CustomSourceDAO
# ============================================================
class CustomSourceDAO:
    """自定义代理源的 CRUD

    表结构：
        custom_sources(id, name, type, url, proto, config_json,
                       enabled, created_at, updated_at)
    """

    _ALLOWED_TYPES = {"text", "table", "api"}
    _ALLOWED_PROTOS = {"http", "https"}

    def create(self, source: CustomSource) -> CustomSource:
        """插入新源（name 重复抛 ValueError）"""
        if source.type not in self._ALLOWED_TYPES:
            raise ValueError(
                f"type 必须是 {self._ALLOWED_TYPES} 之一，得到: {source.type}"
            )
        if source.proto not in self._ALLOWED_PROTOS:
            raise ValueError(
                f"proto 必须是 {self._ALLOWED_PROTOS} 之一，得到: {source.proto}"
            )
        conn = db.get_conn()
        now = time.time()
        try:
            cur = conn.execute(
                """
                INSERT INTO custom_sources
                (name, type, url, proto, config_json,
                 enabled, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    source.name,
                    source.type,
                    source.url,
                    source.proto,
                    json.dumps(source.config, ensure_ascii=False),
                    1 if source.enabled else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            source.id = cur.lastrowid
            source.created_at = now
            source.updated_at = now
            return source
        except sqlite3.IntegrityError as e:
            # name 唯一约束冲突
            raise ValueError(f"源名 '{source.name}' 已存在") from e

    def get(self, name: str) -> Optional[CustomSource]:
        """按 name 查单条"""
        row = db.get_conn().execute(
            "SELECT * FROM custom_sources WHERE name=?", (name,)
        ).fetchone()
        return CustomSource.from_row(row) if row else None

    def list_all(self, enabled_only: bool = False) -> List[CustomSource]:
        """列全部（默认含禁用的）"""
        sql = "SELECT * FROM custom_sources"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY id ASC"
        rows = db.get_conn().execute(sql).fetchall()
        return [CustomSource.from_row(r) for r in rows]

    def delete(self, name: str) -> bool:
        """按 name 删除"""
        cur = db.get_conn().execute(
            "DELETE FROM custom_sources WHERE name=?", (name,)
        )
        db.get_conn().commit()
        return cur.rowcount > 0

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """启停某个源"""
        cur = db.get_conn().execute(
            """
            UPDATE custom_sources
            SET enabled=?, updated_at=?
            WHERE name=?
            """,
            (1 if enabled else 0, time.time(), name),
        )
        db.get_conn().commit()
        return cur.rowcount > 0

    def count(self) -> int:
        """总数"""
        return db.get_conn().execute(
            "SELECT COUNT(*) FROM custom_sources"
        ).fetchone()[0]


# 全局单例
custom_source_dao = CustomSourceDAO()
