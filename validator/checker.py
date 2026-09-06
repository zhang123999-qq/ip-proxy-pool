"""
代理验证器模块
==============

工作流程：
1. 从代理池取一批代理
2. 用每个代理去访问 VALIDATE_URL（比如 httpbin.org/ip）
3. 访问成功 → 打分 +1
4. 访问失败 → 打分 -3
5. 分数 ≤ 0 的会被自动从池中删除

为什么要并发？
- 100 个代理串行验证 → 几十分钟
- 100 个代理并发 50 → 几十秒

技术细节：
- asyncio.Semaphore 控制并发数
- httpx 异步 HTTP 客户端
- 超时控制防止被某个慢代理卡住
"""
import asyncio
import random
import time
from typing import List, Optional, Tuple

import httpx

from config import (
    VALIDATE_TIMEOUT,
    VALIDATE_CONCURRENCY,
    VALIDATE_URL,
    USER_AGENTS,
)
from models import ProxyItem
from pool import pool
from utils import logger


# ============================================================
# 验证器
# ============================================================
class ProxyValidator:
    """
    代理验证器

    使用：
        validator = ProxyValidator()  # 或直接用全局 validator
        await validator.validate()    # 验证池中所有代理
    """

    def __init__(self, concurrency: int = VALIDATE_CONCURRENCY):
        # 信号量：最多同时跑 N 个验证
        # 这就是「并发数」的实现原理
        self._sem = asyncio.Semaphore(concurrency)
        # 预构造 UA 列表，方便 random.choice
        self._uas: List[str] = list(USER_AGENTS)

    async def _check_one(
        self, item: ProxyItem
    ) -> Tuple[ProxyItem, bool, float]:
        """
        验证单个代理

        :return: (代理项, 是否可用, 耗时秒)
        """
        # 构造代理 URL：http://1.2.3.4:8080
        proxy_url = f"{item.protocol.value}://{item.ip}:{item.port}"
        # 每次随机一个 UA
        headers = {"User-Agent": random.choice(self._uas)}
        start = time.time()
        success = False
        try:
            # httpx 异步客户端，proxy 参数走代理
            async with httpx.AsyncClient(
                proxy=proxy_url,
                timeout=VALIDATE_TIMEOUT,
                headers=headers,
                follow_redirects=True,
            ) as client:
                resp = await client.get(VALIDATE_URL)
                # 200 算成功
                if resp.status_code == 200:
                    success = True
        except (httpx.TimeoutException, httpx.ProxyError, httpx.ConnectError) as e:
            # 常见错误：超时、代理拒绝、连不上
            logger.debug(f"验证失败 {item.key}: {type(e).__name__}: {e}")
            success = False
        except ValueError as e:
            # 已知兼容问题：asyncio.shield 内部参数错误（anyio 旧版）
            # 视为超时失败，不当作致命错误
            if "shield" in str(e) or "exceptions" in str(e):
                logger.debug(f"验证失败(已知兼容) {item.key}: {e}")
                success = False
            else:
                logger.warning(f"验证异常 {item.key}: {type(e).__name__}: {e}")
                success = False
        except Exception as e:
            # 其他未知错误
            logger.warning(f"验证异常 {item.key}: {type(e).__name__}: {e}")
            success = False

        elapsed = round(time.time() - start, 3)
        return item, success, elapsed

    async def validate(
        self, items: Optional[List[ProxyItem]] = None
    ) -> int:
        """
        批量验证代理

        :param items: 要验证的代理列表；None 表示验证池中所有
        :return: 验证成功的数量
        """
        # 没传就用池里全部
        if items is None:
            items = pool.all()

        if not items:
            logger.info("代理池为空，跳过验证")
            return 0

        # _worker 是包装了一层信号量的协程
        async def _worker(item: ProxyItem):
            async with self._sem:  # 拿信号量（满了就等）
                return await self._check_one(item)

        # 为每个代理创建一个任务
        tasks = [_worker(it) for it in items]
        # asyncio.gather 等所有任务完成
        # return_exceptions=True 让单个失败不影响其他
        results = await asyncio.gather(*tasks, return_exceptions=True)

        ok_count = 0
        for res in results:
            # 异常结果（_check_one 里 raise 出来的）
            if isinstance(res, Exception):
                logger.error(f"验证任务异常: {res}")
                continue
            # 正常结果：(item, success, elapsed)
            item, success, elapsed = res
            # 更新打分（+1 / -3 / 自动删除）
            await pool.update_score(item.ip, item.port, success)
            if success:
                ok_count += 1
        logger.info(
            f"本轮验证完成: 总 {len(results)}, 可用 {ok_count}, "
            f"池剩余 {pool.size()}"
        )
        return ok_count


# ============================================================
# 全局验证器实例（直接用这个就行）
# ============================================================
validator = ProxyValidator()
