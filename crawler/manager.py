"""
爬虫管理器模块（智能版）
=======================

核心创新：「代理养代理」机制
- 池里有代理 → 优先用代理去爬代理源（突破风控）
- 池空 / 代理耗尽 → 降级回直连
- 全部失败 → SKIP 模式（避免死循环）

降级链：
    HYBRID → DIRECT → SKIP

API：
    manager = get_default_manager()
    result = await manager.run_all()        # 自动决策
    result = await manager.run_all(mode="direct")  # 强制模式
"""
import asyncio
from enum import Enum
from typing import List, Optional

import httpx

from config import CRAWL_TIMEOUT, SAME_PROXY_PER_SOURCE_MAX
from .base import BaseCrawler, RateLimiter, _detect_charset
from models import ProxyItem
from pool import pool
from utils import logger


# ============================================================
# 模式枚举
# ============================================================
class CrawlMode(str, Enum):
    """爬源模式"""
    DIRECT = "direct"     # 直连
    PROXY = "proxy"       # 全部用代理
    HYBRID = "hybrid"     # 混合
    SKIP = "skip"         # 跳过本轮


# ============================================================
# 爬源结果
# ============================================================
class CrawlResult:
    """一次爬取的结果汇总"""
    def __init__(self):
        self.mode: str = ""                    # 本轮使用的模式
        self.total_sources: int = 0            # 源总数
        self.success_sources: int = 0          # 成功源数
        self.failed_sources: int = 0           # 失败源数
        self.items: List[ProxyItem] = []       # 采到的代理
        self.proxy_used: List[str] = []        # 用了哪些代理
        self.errors: List[str] = []            # 错误信息

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "total_sources": self.total_sources,
            "success_sources": self.success_sources,
            "failed_sources": self.failed_sources,
            "items_count": len(self.items),
            "proxy_used": self.proxy_used,
            "errors": self.errors,
        }


# ============================================================
# 爬虫管理器
# ============================================================
class CrawlerManager:
    """
    智能爬虫管理器

    模式决策逻辑（auto）：
    - 池 ≥ PROXY_MIN_COUNT_FOR_CRAWL → HYBRID
    - 池有 1 ~ 阈值-1 个高分代理 → PROXY
    - 池空 → DIRECT
    - 连续 N 次全失败 → SKIP
    """

    def __init__(self, crawlers: List[BaseCrawler]):
        self.crawlers = crawlers
        self.last_result: Optional[CrawlResult] = None
        self._consecutive_full_fails = 0

    def decide_mode(self, mode_hint: str = "auto") -> CrawlMode:
        """
        决策模式

        :param mode_hint: direct / proxy / hybrid / auto / skip
        """
        from config import (
            PROXY_MIN_COUNT_FOR_CRAWL,
            CRAWL_TOTAL_FAIL_THRESHOLD,
        )

        if mode_hint == "skip":
            return CrawlMode.SKIP
        if mode_hint == "direct":
            return CrawlMode.DIRECT
        if mode_hint == "proxy":
            return CrawlMode.PROXY
        if mode_hint == "hybrid":
            return CrawlMode.HYBRID

        # auto 决策
        if self._consecutive_full_fails >= CRAWL_TOTAL_FAIL_THRESHOLD:
            logger.warning(
                f"连续 {self._consecutive_full_fails} 次全失败，进入 SKIP 模式"
            )
            return CrawlMode.SKIP

        candidates = pool.get_crawl_candidates(limit=100)
        if not candidates:
            logger.info("池中无可用爬源代理 → DIRECT 模式")
            return CrawlMode.DIRECT
        if len(candidates) >= PROXY_MIN_COUNT_FOR_CRAWL:
            logger.info(
                f"池有 {len(candidates)} 个高分代理 → HYBRID 模式"
            )
            return CrawlMode.HYBRID
        logger.info(
            f"池有 {len(candidates)} 个高分代理 → PROXY 模式"
        )
        return CrawlMode.PROXY

    async def run_all(self, mode: str = "auto") -> CrawlResult:
        """
        执行一次采集

        :param mode: direct / proxy / hybrid / auto / skip
        """
        result = CrawlResult()
        result.mode = self.decide_mode(mode).value
        result.total_sources = len(self.crawlers)
        self.last_result = result

        if result.mode == CrawlMode.SKIP.value:
            logger.info("本轮 SKIP，等待下一轮")
            return result

        # 准备代理 + 每轮独立的去重集（避免跨调用累积污染）
        candidates = pool.get_crawl_candidates(limit=100)
        proxy_assignments = self._assign_proxies(candidates, result.mode)
        # 局部去重集：本 run_all 内多源去重（不是协程共享，竞态安全）
        seen_keys: set = set()

        # 准备任务
        tasks = []
        for cr in self.crawlers:
            proxies = proxy_assignments.get(cr.name, [])
            logger.info(
                f"启动爬虫: {cr.name} "
                f"(mode={result.mode}, proxies={len(proxies)})"
            )
            tasks.append(
                self._safe_run_with_mode(
                    cr, proxies, result.mode, result, seen_keys,
                )
            )

        # 并发跑
        await asyncio.gather(*tasks, return_exceptions=True)

        # 统计结果
        result.success_sources = result.total_sources - result.failed_sources
        # 只在「至少有源 + 全部失败」时才累加连续失败计数
        # 否则空 crawlers 列表（total=0）会被误判为「全失败」
        if result.total_sources > 0 and \
                result.failed_sources == result.total_sources:
            self._consecutive_full_fails += 1
        else:
            self._consecutive_full_fails = 0

        logger.info(
            f"采集汇总: mode={result.mode} "
            f"成功 {result.success_sources}/{result.total_sources}, "
            f"采到 {len(result.items)} 条"
        )
        return result

    def _assign_proxies(
        self,
        candidates: List[ProxyItem],
        mode: str,
    ) -> dict:
        """
        给每个爬虫分配代理

        策略：轮换分配
        - HYBRID 模式下：前 N 个爬虫用代理，其余直连
        - PROXY 模式下：所有爬虫都用代理
        - DIRECT 模式下：不分配代理
        """
        assignment: dict = {}
        if mode == CrawlMode.DIRECT.value or not candidates:
            for cr in self.crawlers:
                assignment[cr.name] = []
            return assignment

        # 轮换分配
        idx = 0
        n_crawlers = len(self.crawlers)
        for i, cr in enumerate(self.crawlers):
            # HYBRID：前半爬虫走代理，后半走直连
            if mode == CrawlMode.HYBRID.value and i >= n_crawlers // 2:
                assignment[cr.name] = []
                continue
            n = min(SAME_PROXY_PER_SOURCE_MAX, len(candidates))
            assignment[cr.name] = [
                candidates[(idx + j) % len(candidates)]
                for j in range(n)
            ]
            idx += n
        return assignment

    # ---------------- 热重载 ----------------
    def reload_crawlers(
        self, crawlers: Optional[List[BaseCrawler]] = None
    ) -> int:
        """
        重新装载爬虫列表（API 增删源后调用）

        用 copy-then-swap 避免被正在进行的 run_all() 撞到
        （asyncio.gather 还在迭代 self.crawlers 时不要改）

        :param crawlers: 新列表；None = 自动合并内置+DB 里的自定义源
        :return: 当前爬虫数
        """
        if crawlers is None:
            from .sources import BUILTIN_CRAWLERS
            from .custom import build_all_custom_crawlers
            crawlers = list(BUILTIN_CRAWLERS) + build_all_custom_crawlers()
        # copy-then-swap 让 scheduler 正在跑的 run_all 不受影响
        self.crawlers = list(crawlers)
        # 重置熔断统计（避免历史数据污染新源）
        self._consecutive_full_fails = 0
        return len(self.crawlers)

    async def _safe_run_with_mode(
        self,
        crawler: BaseCrawler,
        proxies: List[ProxyItem],
        mode: str,
        result: CrawlResult,
        seen_keys: set,
    ) -> List[ProxyItem]:
        """
        用指定模式跑单个爬虫
        - 直连：直接调 fetch
        - 代理：每个代理跑一次

        seen_keys 由 run_all 传入（轮内共享），多源去重
        """
        if not proxies:
            # 直连模式
            try:
                items = await crawler.fetch()
                for it in items:
                    it.source = crawler.name
                    if it.key not in seen_keys:
                        seen_keys.add(it.key)
                        result.items.append(it)
                return list(result.items)
            except Exception as e:
                logger.error(
                    f"爬虫 {crawler.name} 直连异常: "
                    f"{type(e).__name__}: {e}"
                )
                result.failed_sources += 1
                result.errors.append(f"{crawler.name}: {e}")
                return []

        # 代理模式：用每个代理去爬一次
        all_items: List[ProxyItem] = []
        for proxy in proxies:
            try:
                items = await self._fetch_via_proxy(crawler, proxy)
                for it in items:
                    it.source = crawler.name
                    if it.key not in seen_keys:
                        seen_keys.add(it.key)
                        all_items.append(it)
                await pool.update_score_soft(
                    proxy.ip, proxy.port, True,
                    reason=f"crawl-{crawler.name}",
                )
                pool.mark_used_for_crawl(proxy.ip, proxy.port)
                result.proxy_used.append(proxy.key)
            except Exception as e:
                logger.warning(
                    f"代理 {proxy.key} 爬 {crawler.name} 失败: "
                    f"{type(e).__name__}"
                )
                await pool.update_score_soft(
                    proxy.ip, proxy.port, False,
                    reason=f"crawl-{crawler.name}",
                )
        if all_items:
            result.items.extend(all_items)
        else:
            result.failed_sources += 1
        return all_items

    async def _fetch_via_proxy(
        self,
        crawler: BaseCrawler,
        proxy: ProxyItem,
    ) -> List[ProxyItem]:
        """
        用指定代理去爬
        临时给爬虫注入走代理的 httpx 客户端
        """
        original_get_html = crawler._get_html
        original_get_json = crawler._get_json

        proxy_url = f"{proxy.protocol.value}://{proxy.ip}:{proxy.port}"

        async def _proxied_get_html(url: str, **kwargs):
            await RateLimiter.acquire()
            await crawler._cooldown_sleep()
            try:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=CRAWL_TIMEOUT,
                    headers=crawler._random_headers(),
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url, **kwargs)
                    if resp.status_code in (403, 429, 503):
                        raise httpx.HTTPStatusError(
                            "blocked",
                            request=resp.request,
                            response=resp,
                        )
                    resp.raise_for_status()
                    if resp.encoding is None or resp.encoding == "ISO-8859-1":
                        raw = await resp.aread()
                        resp.encoding = _detect_charset(raw)
                    return resp.text
            except Exception as e:
                logger.debug(
                    f"代理 {proxy.key} 爬 {url} 失败: "
                    f"{type(e).__name__}: {e}"
                )
                return None

        async def _proxied_get_json(url: str, **kwargs):
            resp_text = await _proxied_get_html(url, **kwargs)
            if resp_text is None:
                return None
            import json
            try:
                return json.loads(resp_text)
            except Exception as e:
                logger.warning(
                    f"代理 {proxy.key} JSON 解析失败: {e}"
                )
                return None

        crawler._get_html = _proxied_get_html
        crawler._get_json = _proxied_get_json
        try:
            items = await crawler.fetch()
            return items
        finally:
            crawler._get_html = original_get_html
            crawler._get_json = original_get_json


# 全局单例
_manager_instance: Optional[CrawlerManager] = None


def get_default_manager() -> CrawlerManager:
    """
    获取默认爬虫管理器（内置源 + DB 中的自定义源）

    首次调用时合并，懒加载。后续 API 增删源通过 manager.reload_crawlers() 触发。
    """
    global _manager_instance
    if _manager_instance is None:
        from .sources import BUILTIN_CRAWLERS
        from .custom import build_all_custom_crawlers
        all_crawlers = list(BUILTIN_CRAWLERS) + build_all_custom_crawlers()
        _manager_instance = CrawlerManager(all_crawlers)
    return _manager_instance


def reset_manager() -> None:
    """
    重置单例（测试用）

    让下次 get_default_manager() 重新合并内置+自定义源。
    """
    global _manager_instance
    _manager_instance = None
