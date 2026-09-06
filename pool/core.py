"""
核心代理池模块
==============

这是整个项目最核心的类 ProxyPool。
它就像一个「仓库管理员」，管理着所有可用代理。

核心职责：
1. 存代理：add / remove
2. 查代理：get / all / size
3. 打分：update_score（核心机制）
4. 分配：random_one / random_n / top_n（业务调用）
5. 持久化：save / load（SQLite，重启不丢数据）

并发安全：
- asyncio.Lock 保护整个 dict 的增删（防「迭代时被改」异常）
- 单条 ProxyItem 自带 RLock 保护字段读写（防「读到一半被改」）
- 这两层锁一起用 → 线程 + 协程双重安全

存储：SQLite（storage/dao.py），内存 dict 是唯一事实来源，
SQLite 是持久化备份。写操作走「线程池」（不阻塞事件循环）。
"""
import asyncio
import json
import random
import time
from typing import Dict, List, Optional, Tuple

from config import (
    SCORE_UP,             # 成功时 +分数
    SCORE_DOWN,           # 失败时 -分数
    SCORE_DOWN_CRAWL_FAIL,# 爬源专用软扣分
    MAX_SCORE,            # 分数上限
    MIN_SCORE,            # 分数下限（<=即删除）
    PROXY_MIN_SCORE_FOR_CRAWL,  # 爬源代理最低门槛
    PROXY_USE_COOLDOWN_SEC,     # 爬源代理冷却时间
    PROXY_POOL_FILE,            # 旧 JSON 路径（迁移用）
    SQLITE_AUTO_MIGRATE,        # 是否启动时迁移 JSON
)
from models import ProxyItem
from utils import logger
from storage import dao, db


# ============================================================
# 代理池（单例模式）
# ============================================================
# 「单例」是设计模式的一种：整个程序里只能有一个 ProxyPool 实例。
# 这样无论哪里 `from pool import pool` 拿到的都是同一个对象，
# 否则爬虫塞进来的代理和业务读出来的代理会不一致。
#
# 实现要点：
# - 重写 __new__：拦截「创建新实例」的动作
# - 第一次创建时 _instance 是 None，就创建
# - 之后都返回第一次创建的那个
class ProxyPool:
    """代理池（单例）

    用法：
        from pool import pool
        await pool.add(ProxyItem(ip="1.2.3.4", port=8080))
        p = pool.random_one(protocol="http")
    """

    # ----- 单例：进程级唯一实例 -----
    _instance: Optional["ProxyPool"] = None

    def __new__(cls):
        # 第一次调用时 cls._instance 是 None
        if cls._instance is None:
            # super().__new__ 才会真正「创建」实例
            cls._instance = super().__new__(cls)
            # _initialized 用作 __init__ 幂等标志
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        # __init__ 每次都会被调用，但我们用 _initialized 兜底只初始化一次
        if self._initialized:
            return
        # 核心数据结构：key = "ip:port"（字符串），value = ProxyItem 对象
        # 用字符串 key 比 (ip, port) tuple 更省内存
        self._pool: Dict[str, ProxyItem] = {}
        # 协程级锁，保护整个 dict 的增删（防「迭代时被改」异常）
        self._lock = asyncio.Lock()
        self._initialized = True

    # ====================================================
    # 基础查询（线程安全：dict 读是原子的，单线程安全）
    # ====================================================
    # 注意：同步方法（不加 async），调用方也别在 await 中反复调用
    # 它们的开销几乎为 0（单次 dict.get/dict.values()）
    def size(self) -> int:
        """当前代理数量"""
        return len(self._pool)

    def all(self) -> List[ProxyItem]:
        """获取所有代理的列表

        关键：返回的是 list(values()) 的副本，不是 view
        外部可以放心修改返回的 list，不会影响内部 dict
        """
        return list(self._pool.values())

    def get(self, ip: str, port: int) -> Optional[ProxyItem]:
        """按 ip:port 查单个代理（找不到返回 None）"""
        return self._pool.get(f"{ip}:{port}")

    def contains(self, ip: str, port: int) -> bool:
        """判断代理是否存在（比 get 快一点，省去取出对象开销）"""
        return f"{ip}:{port}" in self._pool

    def stats(self) -> dict:
        """
        池统计信息（监控/展示用）
        返回平均/最高/最低分 + 协议分布

        调用频率很低（API 一次请求），O(n) 可以接受
        """
        items = self.all()
        if not items:
            return {
                "size": 0,
                "avg_score": 0,
                "max_score": 0,
                "min_score": 0,
                "http": 0,
                "https": 0,
                "both": 0,
            }
        scores = [it.score for it in items]
        # 协议分布计数（用 dict.get 防 KeyError）
        proto_count = {"http": 0, "https": 0, "both": 0}
        for it in items:
            proto_count[it.protocol.value] = (
                proto_count.get(it.protocol.value, 0) + 1
            )
        return {
            "size": len(items),
            "avg_score": round(sum(scores) / len(scores), 2),
            "max_score": max(scores),
            "min_score": min(scores),
            **proto_count,  # 把协议计数展开到返回 dict 里
        }

    # ====================================================
    # 增删代理（async + lock 保证并发安全）
    # ====================================================
    async def add(self, item: ProxyItem) -> bool:
        """
        异步新增代理

        :return: True=真正新增；False=已存在 / IP非法 / 端口非法
        """
        # 入参校验
        if not ProxyItem.is_valid_ip(item.ip):
            logger.warning(f"非法 IP，丢弃: {item.ip}")
            return False
        if not (0 < item.port < 65536):
            logger.warning(f"非法端口，丢弃: {item.port}")
            return False
        # 加锁操作 dict（保证「判断+写入」原子性）
        async with self._lock:
            if item.key in self._pool:
                # 已存在不重复添加
                return False
            if item.created_at == 0.0:
                # 第一次入池，记时间
                item.created_at = time.time()
            self._pool[item.key] = item
            # 同步写 SQLite（不阻塞事件循环：丢到默认 thread pool）
            await self._db_insert(item)
            return True

    async def remove(self, ip: str, port: int) -> bool:
        """异步删除代理（不存在也返回 False）"""
        async with self._lock:
            key = f"{ip}:{port}"
            if key in self._pool:
                del self._pool[key]
                # 同步删 DB
                await self._db_delete(ip, port)
                return True
            return False

    # ====================================================
    # 打分引擎（核心机制）
    # ====================================================
    async def update_score(
        self, ip: str, port: int, success: bool
    ) -> Tuple[bool, int, str]:
        """
        更新分数（线程 + 协程双重安全）

        流程：
        1. 加协程锁
        2. 查 dict 拿到 item
        3. 计算新分数（封顶 20；下限 0）
        4. item.with_score（持 RLock）真正写入
        5. 分数 <= 0 → 从池删除
        6. 否则同步 DB

        :param ip: 代理 IP
        :param port: 代理端口
        :param success: True=可用 +SCORE_UP, False=不可用 -SCORE_DOWN
        :return: (是否更新成功, 新分数, 描述)
        """
        async with self._lock:
            item = self._pool.get(f"{ip}:{port}")
            if item is None:
                return False, 0, "代理不存在"

            # 计算新分数（封顶 / 保底 0）
            if success:
                new_score = min(MAX_SCORE, item.score + SCORE_UP)
            else:
                new_score = max(0, item.score - SCORE_DOWN)

            # 写回分数（带 RLock，保护 item 字段不被并发改）
            item.with_score(new_score)
            item.with_check_result(success, item.response_time)

            msg = f"{item.key} 分数 {item.score} (success={success})"

            # 分数 ≤ MIN_SCORE 自动删除（直接 del dict + DB 删）
            if item.score <= MIN_SCORE:
                del self._pool[item.key]
                await self._db_delete(item.ip, item.port)
                return True, item.score, f"{msg} -> 已删除"

            # 同步 DB 分数（增量 upsert 极快）
            await self._db_upsert(item)
            return True, item.score, msg

    async def update_score_soft(
        self, ip: str, port: int, success: bool, reason: str = "crawl"
    ) -> Tuple[bool, int, str]:
        """
        软扣分（爬源专用）

        与 update_score 区别：
        - 失败只扣 SCORE_DOWN_CRAWL_FAIL 分（默认 1 分，不是 3 分）
        - 永远不删除代理（爬源失败可能是源站问题，不能怪代理）

        :param reason: 扣分原因（日志用，便于排查哪个源老是失败）
        """
        async with self._lock:
            item = self._pool.get(f"{ip}:{port}")
            if item is None:
                return False, 0, "代理不存在"

            if success:
                new_score = min(MAX_SCORE, item.score + SCORE_UP)
            else:
                new_score = max(
                    0,
                    item.score - SCORE_DOWN_CRAWL_FAIL,
                )

            item.with_score(new_score)
            # 软扣分成功才计入 success_count（不影响 fail_count）
            if success:
                item.with_check_result(True, item.response_time)
            msg = (
                f"{item.key} 软扣分 {item.score} "
                f"(reason={reason}, success={success})"
            )
            # 同步 DB
            await self._db_upsert(item)
            return True, item.score, msg

    # ====================================================
    # 代理分配（业务调用，同步，因为不修改状态）
    # ====================================================
    def random_one(
        self,
        protocol: Optional[str] = None,
        min_score: int = 1,
    ) -> Optional[ProxyItem]:
        """
        加权随机获取一个代理

        加权原理（直觉版）：
        - 每个代理有「权重」= max(1, score)
        - 分数越高被选中的概率越大
        - 比如 1 个 20 分 vs 1 个 1 分 → 20:1 概率

        :param protocol: 可选，过滤协议（http/https/both）
        :param min_score: 最低分数门槛（默认 1 避免拿到刚入池的）
        :return: ProxyItem 或 None（池空 / 不符合）
        """
        items = [
            it for it in self._pool.values()
            if it.score >= min_score
            and (protocol is None or it.protocol.value == protocol)
        ]
        if not items:
            return None
        # 权重 = max(1, score)，最低 1 防止 0 权重被 random.choices 跳过
        weights = [max(1, it.score) for it in items]
        # random.choices 按权重抽样（内部用累积分布+二分查找，O(log n)）
        return random.choices(items, weights=weights, k=1)[0]

    def random_n(
        self,
        n: int,
        protocol: Optional[str] = None,
        min_score: int = 1,
    ) -> List[ProxyItem]:
        """随机获取 n 个不重复的代理

        这里用 random.sample 而不是 choices —— 因为我们要求不重复
        （外部业务可能要把不同代理分给不同请求）
        """
        items = [
            it for it in self._pool.values()
            if it.score >= min_score
            and (protocol is None or it.protocol.value == protocol)
        ]
        if not items:
            return []
        k = min(n, len(items))
        return random.sample(items, k)

    def top_n(self, n: int = 10) -> List[ProxyItem]:
        """分数最高的 n 个代理（监控/展示用）

        O(n log n) —— 100k 条代理的池约 30ms，可接受
        如果池大到 GB 级再考虑用 nlargest 堆优化
        """
        return sorted(
            self._pool.values(),
            key=lambda x: x.score,
            reverse=True,
        )[:n]

    # ====================================================
    # 爬源代理选取（核心创新：代理养代理）
    # ====================================================
    def get_crawl_candidates(
        self,
        limit: int = 4,
        min_score: int = None,
        cooldown_sec: int = None,
    ) -> List[ProxyItem]:
        """
        选取用于爬源的高质量代理

        过滤条件（同时满足）：
        1. score ≥ min_score（默认 PROXY_MIN_SCORE_FOR_CRAWL=10）
           → 保证质量，别用刚入池的代理去爬
        2. 距离上次爬源超过 cooldown_sec 秒（默认 300s = 5 分钟）
           → 防止同一代理被反复用（被目标站识别）
        3. 按分数倒序，取前 limit 个

        :param limit: 最多返回几个
        """
        if min_score is None:
            min_score = PROXY_MIN_SCORE_FOR_CRAWL
        if cooldown_sec is None:
            cooldown_sec = PROXY_USE_COOLDOWN_SEC
        now = time.time()
        candidates = []
        for it in self._pool.values():
            # 分数门槛
            if it.score < min_score:
                continue
            # 冷却期检查：last_used_for_crawl=0 表示从来没用过，OK
            # 否则要求「距离上次用 > cooldown_sec」
            if it.last_used_for_crawl > 0 and \
               (now - it.last_used_for_crawl) < cooldown_sec:
                continue
            candidates.append(it)
        # 分数倒序
        candidates.sort(key=lambda x: x.score, reverse=True)
        return candidates[:limit]

    def mark_used_for_crawl(self, ip: str, port: int) -> bool:
        """标记某个代理刚被用于爬源（更新冷却时间戳）"""
        item = self.get(ip, port)
        if item is None:
            return False
        item.mark_used_for_crawl()  # 内部持 RLock
        return True

    def get_usage(self) -> List[dict]:
        """返回所有代理的爬源使用情况（监控用）

        输出按「冷却剩余时间」排序——即将解锁的代理在前
        让用户能直观看到「现在可用」 vs 「还差多久」
        """
        now = time.time()
        result = []
        for it in self._pool.values():
            if it.last_used_for_crawl > 0:
                # 冷却剩余 = 总冷却 - (现在 - 上次用)
                remaining = max(
                    0,
                    PROXY_USE_COOLDOWN_SEC - (now - it.last_used_for_crawl),
                )
                result.append({
                    "ip": it.ip,
                    "port": it.port,
                    "score": it.score,
                    "last_used_for_crawl": it.last_used_for_crawl,
                    "cooldown_remaining_sec": round(remaining, 1),
                })
        # 冷却剩余短的排前面
        result.sort(key=lambda x: x["cooldown_remaining_sec"])
        return result

    # ====================================================
    # 持久化（SQLite 增量写入）
    # ====================================================
    # 为什么 SQLite 比 JSON 好？
    # - 增量 upsert：改 1 条只写 1 条，不用全量序列化
    # - WAL 模式：读写并行（验证任务读 DB，爬虫写 DB 互不阻塞）
    # - ACID：断电安全（不会写到一半崩了剩半截 JSON）
    # - 索引查询：O(log n)（全表扫描是 O(n)）

    # ----- 底层 DB 辅助（在线程池跑，避免阻塞 asyncio 事件循环）-----
    async def _db_insert(self, item: ProxyItem) -> None:
        """异步插入（走线程池）"""
        try:
            # asyncio.to_thread = 把同步函数丢到默认 thread pool 执行
            await asyncio.to_thread(dao.insert, item)
        except Exception as e:
            logger.warning(f"DB insert 失败: {e}")

    async def _db_upsert(self, item: ProxyItem) -> None:
        """异步 upsert（走线程池）"""
        try:
            await asyncio.to_thread(dao.upsert, item)
        except Exception as e:
            logger.warning(f"DB upsert 失败: {e}")

    async def _db_delete(self, ip: str, port: int) -> None:
        """异步删除（走线程池）"""
        try:
            await asyncio.to_thread(dao.delete, ip, port)
        except Exception as e:
            logger.warning(f"DB delete 失败: {e}")

    async def save(self) -> int:
        """
        全量持久化（把整个内存池的 ProxyItem 一次性 upsert 到 SQLite）
        正常情况下不需要手动调（add/update_score 已自动同步），
        但保留这个接口用于：
        - 定时兜底（scheduler/jobs.py 每小时调一次）
        - 停机前完整刷盘（lifespan 关闭时）
        """
        try:
            # 拍快照（避免迭代时字典被改）
            items = list(self._pool.values())
            n = await asyncio.to_thread(dao.upsert_many, items)
            logger.info(f"代理池持久化成功: {n} 条 -> SQLite")
            return n
        except Exception as e:
            logger.error(f"代理池持久化失败: {e}")
            return 0

    async def load(self) -> int:
        """
        从 SQLite 加载（启动时调用，在 lifespan 里跑）
        流程：
        1. 读 SQLite → items
        2. SQLite 为空 + 启用了迁移 → 尝试从旧 JSON 迁
        3. items 填充内存 dict
        """
        try:
            # 1. 从 SQLite 读
            items = await asyncio.to_thread(dao.get_all)
            # 2. SQLite 为空 → 尝试迁移旧 JSON（一次性）
            if not items and SQLITE_AUTO_MIGRATE:
                migrated = await self._migrate_from_json()
                if migrated:
                    items = await asyncio.to_thread(dao.get_all)
            # 3. 填充内存
            async with self._lock:
                self._pool.clear()
                for item in items:
                    self._pool[item.key] = item
            logger.info(f"代理池加载成功: {len(items)} 条 (SQLite)")
            return len(items)
        except Exception as e:
            logger.error(f"代理池加载失败: {e}")
            return 0

    async def _migrate_from_json(self) -> bool:
        """一次性把旧 JSON 数据导入 SQLite（然后删掉 JSON）

        背景：1.0 版本用 JSON 存，2.0 改成 SQLite。
        这个函数让老用户无痛升级。
        """
        path = PROXY_POOL_FILE
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = [ProxyItem.from_dict(raw) for raw in data]
            if items:
                await asyncio.to_thread(dao.upsert_many, items)
                # 迁移成功后改名备份（保留，万一回滚）
                bak = path.with_suffix(".json.bak")
                path.rename(bak)
                logger.info(
                    f"旧 JSON 已迁移到 SQLite: {len(items)} 条 -> {bak}"
                )
            return True
        except Exception as e:
            logger.warning(f"JSON 迁移失败: {e}")
            return False

    async def flush(self) -> None:
        """优雅停机：全量刷盘 + WAL checkpoint"""
        await self.save()
        # checkpoint 把 WAL 数据合并回主文件，避免启动时还要 replay
        await asyncio.to_thread(db.checkpoint)

    async def clear(self) -> None:
        """清空（仅测试 / 管理接口用）"""
        async with self._lock:
            self._pool.clear()
        # 同时清 DB
        await asyncio.to_thread(dao.delete_all)
        logger.warning("代理池已清空")


# ============================================================
# 全局单例（导出的就是这个 pool）
# ============================================================
# 其他模块直接 `from pool import pool` 就能拿到这个唯一实例
pool = ProxyPool()
