"""
SQLite 存储层 — 连接管理
========================

负责：
1. 创建/管理 SQLite 连接（单例）
2. 开启 WAL 模式（读写并行，性能提升）
3. 建表 + 索引（幂等，重复调用不报错）
4. 提供 get_conn() 给 DAO 用

为什么用 SQLite？
- 单文件数据库，零部署成本（跟 JSON 一样是 1 个文件）
- 支持 SQL 索引查询（比内存扫 dict 快 N 倍）
- WAL 模式读写不互斥
- ACID 事务，断电安全
"""
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from config import SQLITE_FILE, SQLITE_WAL_MODE
from utils import logger


# ============================================================
# 建表 SQL（幂等：IF NOT EXISTS）
# ============================================================
SCHEMA_SQL = """
-- 代理主表（主键 ip+port）
CREATE TABLE IF NOT EXISTS proxies (
    ip                TEXT    NOT NULL,
    port              INTEGER NOT NULL,
    protocol          TEXT    NOT NULL DEFAULT 'http',
    score             INTEGER NOT NULL DEFAULT 10,
    response_time     REAL    NOT NULL DEFAULT 0.0,
    success_count     INTEGER NOT NULL DEFAULT 0,
    fail_count        INTEGER NOT NULL DEFAULT 0,
    last_check        REAL    NOT NULL DEFAULT 0.0,
    created_at        REAL    NOT NULL DEFAULT 0.0,
    source            TEXT    NOT NULL DEFAULT '',
    last_used_for_crawl REAL  NOT NULL DEFAULT 0.0,
    PRIMARY KEY (ip, port)
);

-- 分数索引（top_n / 加权随机加速）
CREATE INDEX IF NOT EXISTS idx_proxies_score ON proxies(score DESC);
-- 爬源冷却索引（get_crawl_candidates 加速）
CREATE INDEX IF NOT EXISTS idx_proxies_last_used ON proxies(last_used_for_crawl);
-- 来源索引（按源去重/统计）
CREATE INDEX IF NOT EXISTS idx_proxies_source ON proxies(source);

-- 爬源行为历史表（追溯代理爬源表现）
CREATE TABLE IF NOT EXISTS crawl_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ip          TEXT    NOT NULL,
    port        INTEGER NOT NULL,
    source      TEXT    NOT NULL,
    success     INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crawl_history_ip ON crawl_history(ip, port);
CREATE INDEX IF NOT EXISTS idx_crawl_history_time ON crawl_history(created_at);

-- 自定义代理源表（用户通过 API 动态添加的源，零代码接入）
-- type 可选：text / table / api
--   text  纯文本，每行一个 ip:port（GitHub raw 仓库、.txt 列表）
--   table HTML 表格，自动取首二列
--   api   JSON/文本 API（兼容 proxyscrape 那种）
CREATE TABLE IF NOT EXISTS custom_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    type        TEXT    NOT NULL,
    url         TEXT    NOT NULL,
    proto       TEXT    NOT NULL DEFAULT 'http',
    config_json TEXT    NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_custom_sources_enabled ON custom_sources(enabled);
"""


# ============================================================
# 数据库管理器（单例）
# ============================================================
class Database:
    """
    SQLite 连接管理器（单例）

    特点：
    - 每线程一个连接（sqlite3 连接非线程安全）
    - WAL 模式（读写并行）
    - check_same_thread=False 允许跨线程复用（配合 thread-local）
    """

    _instance: Optional["Database"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        # 每个线程独立连接（thread-local）
        self._local = threading.local()
        self._db_path: Path = SQLITE_FILE

    # ----- 获取当前线程的连接 -----
    def get_conn(self) -> sqlite3.Connection:
        """获取当前线程的 SQLite 连接（自动创建）"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._new_conn()
            self._local.conn = conn
        return conn

    def _new_conn(self) -> sqlite3.Connection:
        """新建一个连接并配置"""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        conn.row_factory = sqlite3.Row  # 查询结果可按列名访问
        if SQLITE_WAL_MODE:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        # 建表（幂等）
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        return conn

    # ----- 工具 -----
    def close(self):
        """关闭当前线程的连接"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def checkpoint(self):
        """WAL 检查点：把 WAL 数据刷回主文件（优雅停机用）"""
        try:
            conn = self.get_conn()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
        except Exception as e:
            logger.warning(f"WAL checkpoint 失败: {e}")

    @property
    def path(self) -> Path:
        return self._db_path


# ============================================================
# 全局单例
# ============================================================
db = Database()
