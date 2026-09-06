"""
爬虫模块入口
============

对外暴露：
- BaseCrawler:    爬虫基类（要写新源就继承它）
- CrawlerManager: 多个爬虫的统一调度（智能版，支持代理爬源）
- CrawlMode:      模式枚举
- CrawlResult:    单次爬取结果
- BUILTIN_CRAWLERS: 内置的免费源
- get_default_manager: 获取默认管理器（合并内置+自定义源）
- build_crawler_from_source: 单条 CustomSource → BaseCrawler
- build_all_custom_crawlers: 全部已启用自定义源 → 爬虫列表
"""
from .base import BaseCrawler, RateLimiter
from .manager import (
    CrawlerManager,
    CrawlMode,
    CrawlResult,
    get_default_manager,
    reset_manager,
)
from .sources import BUILTIN_CRAWLERS
from .custom import (
    build_crawler_from_source,
    build_all_custom_crawlers,
)


__all__ = [
    "BaseCrawler",
    "RateLimiter",
    "CrawlerManager",
    "CrawlMode",
    "CrawlResult",
    "BUILTIN_CRAWLERS",
    "get_default_manager",
    "reset_manager",
    "build_crawler_from_source",
    "build_all_custom_crawlers",
]
