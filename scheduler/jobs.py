"""
定时调度器模块
==============

用 APScheduler 跑 3 个周期任务：
1. 代理采集：每 10 分钟跑一次（启动时立刻跑）
2. 代理验证：每 5 分钟跑一次（启动 30 秒后跑）
3. 持久化：  每 1 小时跑一次

为什么需要调度器？
- 采集：免费代理源会持续放出新代理
- 验证：免费代理会过期（小时级）
- 持久化：防止进程崩溃丢失数据
"""
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import (
    JOB_CRAWL_INTERVAL_MIN,
    JOB_VALIDATE_INTERVAL_MIN,
    JOB_PERSIST_INTERVAL_HOUR,
)
from crawler import get_default_manager
from pool import pool
from validator import validator
from utils import logger


class SchedulerService:
    """调度服务（单例）"""

    def __init__(self):
        # AsyncIOScheduler 适配 asyncio
        self.scheduler = AsyncIOScheduler()
        # 拿默认的爬虫管理器（7 个内置源）
        self.manager = get_default_manager()
        # 幂等保护：避免重复 start/shutdown
        self._started = False

    def start(self) -> None:
        """
        注册并启动所有任务（可重复调用，只生效一次）
        """
        if self._started:
            logger.debug("调度器已启动，跳过")
            return

        # ----- 任务 1：代理采集 -----
        self.scheduler.add_job(
            self._job_crawl,
            trigger=IntervalTrigger(minutes=JOB_CRAWL_INTERVAL_MIN),
            id="job_crawl",
            name="代理采集",
            next_run_time=datetime.now(),  # 启动后立即跑一次
            max_instances=1,  # 同一任务最多 1 个实例在跑
            coalesce=True,    # 多次错过的任务合并成 1 次
        )

        # ----- 任务 2：代理验证 -----
        self.scheduler.add_job(
            self._job_validate,
            trigger=IntervalTrigger(minutes=JOB_VALIDATE_INTERVAL_MIN),
            id="job_validate",
            name="代理验证",
            next_run_time=datetime.now() + timedelta(seconds=30),
            max_instances=1,
            coalesce=True,
        )

        # ----- 任务 3：持久化 -----
        self.scheduler.add_job(
            self._job_persist,
            trigger=IntervalTrigger(hours=JOB_PERSIST_INTERVAL_HOUR),
            id="job_persist",
            name="持久化",
            max_instances=1,
            coalesce=True,
        )

        try:
            self.scheduler.start()
            self._started = True
            logger.info("调度器已启动")
        except Exception as e:
            logger.error(f"调度器启动失败: {e}")

    # ----- 任务实现 -----
    async def _job_crawl(self) -> None:
        """
        采集任务（智能版）
        - 自动决策 DIRECT / PROXY / HYBRID
        - 池里代理可作为爬源通道
        """
        try:
            from config import CRAWL_MODE
            result = await self.manager.run_all(mode=CRAWL_MODE)
            added = 0
            for it in result.items:
                if await pool.add(it):
                    added += 1
            logger.info(
                f"采集任务完成: mode={result.mode} "
                f"拉取 {len(result.items)}, 新增 {added}, "
                f"池总量 {pool.size()}"
            )
        except Exception as e:
            logger.error(f"爬虫任务异常: {type(e).__name__}: {e}")

    async def _job_validate(self) -> None:
        """验证任务：验证池中所有代理，更新分数"""
        try:
            await validator.validate()
        except Exception as e:
            logger.error(f"验证任务异常: {type(e).__name__}: {e}")

    async def _job_persist(self) -> None:
        """定时兜底：刷盘到 SQLite（add/update_score 已实时同步，定时是冗余保护）"""
        try:
            await pool.save()
        except Exception as e:
            logger.error(f"持久化任务异常: {type(e).__name__}: {e}")

    def shutdown(self) -> None:
        """关闭调度器（幂等）"""
        if not self._started:
            return
        try:
            self.scheduler.shutdown(wait=False)
            self._started = False
            logger.info("调度器已关闭")
        except Exception as e:
            logger.error(f"调度器关闭异常: {e}")


# 全局单例
scheduler_service = SchedulerService()
