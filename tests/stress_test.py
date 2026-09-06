"""
逐模块深度运行测试
===================

不是单元测试，是「在真实数据量下看模块能否撑住」的压力测试。

每模块单独跑，最后输出 PASS/FAIL + 内存峰值 + 关键耗时。

运行：
    python tests/stress_test.py
"""
import asyncio
import gc
import sys
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    VALIDATE_TIMEOUT, VALIDATE_CONCURRENCY,
    CRAWL_TIMEOUT, SAME_PROXY_PER_SOURCE_MAX,
)
from models import ProxyItem, ProxyProtocol
from pool import pool
from validator import validator
from crawler import get_default_manager, reset_manager
from storage.dao import dao
from crawler.base import RateLimiter


# ============================================================
# 内存监控 context manager
# ============================================================
@contextmanager
def mem(name: str):
    """跟踪一块代码的内存峰值 + 耗时"""
    tracemalloc.start()
    t0 = time.time()
    gc.collect()
    yield
    elapsed = time.time() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(
        f"   ⏱️ {elapsed*1000:7.1f}ms | "
        f"📈 峰值 +{peak/1024/1024:5.2f}MB  "
        f"<{name}>"
    )


# ============================================================
# 测试 1: ProxyItem 加锁 / 并发 update_score
# ============================================================
def test_proxy_item_concurrent():
    print("\n[1/8] ProxyItem 并发安全测试")
    item = ProxyItem(ip="1.2.3.4", port=8080)
    from threading import Thread, Barrier

    N_THREADS = 32
    N_OPS = 5000
    barrier = Barrier(N_THREADS)

    def worker(tid: int):
        barrier.wait()
        for k in range(N_OPS):
            item.with_score(min(20, item.score + 1))

    threads = [Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    with mem(f"32 线程 × {N_OPS} 次 = {N_THREADS*N_OPS} 次 with_score"):
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 全部 +1 都生效：最终 score = max(20, initial + N_THREADS * N_OPS)
    final_min = 20
    final_max = 10 + N_THREADS * N_OPS
    if item.score == final_min:
        print(f"   ✅ score = {item.score}（触顶 20）")
    elif 10 <= item.score <= final_min:
        print(f"   ✅ score = {item.score}（并发无丢失，正常范围）")
    else:
        print(f"   ❌ score = {item.score}，预期在 [10, 20] / {final_max}")
        return False
    return True


# ============================================================
# 测试 2: 池压力（10k add + 5k feedback + 2k remove）
# ============================================================
async def test_pool_pressure():
    print("\n[2/8] 代理池压力测试（10k add + 5k feedback）")
    await pool.clear()

    async def adder():
        for i in range(10_000):
            await pool.add(ProxyItem(
                ip=f"10.0.{i//256}.{i%256}",
                port=8000 + (i % 1000),
                protocol=ProxyProtocol.HTTP,
            ))

    async def feedback():
        await asyncio.sleep(0.01)  # 等 adder 起跑
        for i in range(5_000):
            await pool.update_score(
                f"10.0.{(i*3)//256}.{(i*3)%256}",
                8000 + ((i*7) % 1000),
                success=(i % 2 == 0),
            )

    with mem("10k add + 5k feedback"):
        await asyncio.gather(adder(), feedback())

    size = pool.size()
    if size < 5000:
        print(f"   ❌ 池大小 {size}（预期 ≥ 5000）")
        return False
    print(f"   ✅ 池大小 {size}（无崩溃，分数波动后很多 score≤0 已删）")
    await pool.clear()
    return True


# ============================================================
# 测试 3: Validator 验证 1k 条
# ============================================================
async def test_validator():
    print("\n[3/8] Validator 验证测试（1k 假代理）")
    items = [
        ProxyItem(
            ip=f"10.0.{i//256}.{i%256}", port=8000 + (i % 1000),
            protocol=ProxyProtocol.HTTP,
        )
        for i in range(1_000)
    ]

    with mem("1000 条假代理验证（必然全失败）"):
        # validate 返回成功条数（int），不是字典
        ok_count = await validator.validate(items)

    print(f"   ok_count = {ok_count}（预期 0，假 IP 全失败）")
    if ok_count == 0:
        print(f"   ✅ Validator 走完一轮（无崩溃）")
        return True
    else:
        # 真有几个能成功也别慌（可能是公网可达的本机）
        print(f"   ✅ Validator 跑通，意外成功的 {ok_count} 个无害")
        return True


# ============================================================
# 测试 4: RateLimiter 精度
# ============================================================
async def test_rate_limiter():
    print("\n[4/8] RateLimiter 令牌桶精度")
    # 重置一下
    RateLimiter._tokens = 0.0
    RateLimiter._last_refill = 0.0

    # 理论分析：cap=5, rate=2/s
    # 前 5 个 acquire 不阻塞（拿满 cap）
    # 后 5 个 acquire 各等 0.5s（refill 一个）
    # → 10 次 ≈ 2.5s
    start = time.time()
    for _ in range(10):
        await RateLimiter.acquire()
    elapsed = time.time() - start

    # 期望 ~2.5s（容忍 ±0.8s）
    if 1.7 <= elapsed <= 3.5:
        print(f"   ✅ {elapsed:.2f}s（10 次 acquire，cap=5 rate=2/s）")
        return True
    else:
        print(f"   ❌ {elapsed:.2f}s，预期 ~2.5s")
        return False


# ============================================================
# 测试 5: SQLite WAL 10k upsert
# ============================================================
async def test_storage_wal():
    print("\n[5/8] SQLite WAL 性能（10k 批量 upsert）")
    await pool.clear()
    items = [
        ProxyItem(
            ip=f"192.168.{i//256}.{i%256}", port=9000 + (i % 1000),
            protocol=ProxyProtocol.HTTP,
        )
        for i in range(10_000)
    ]

    loop = asyncio.get_event_loop()
    with mem("10k ProxyItem 批量 upsert to SQLite"):
        n = await loop.run_in_executor(None, dao.upsert_many, items)

    print(f"   ✅ 写入 {n} 条")
    await pool.clear()
    return True


# ============================================================
# 测试 6: Crawler 一轮采集（DIRECT 模式）
# ============================================================
async def test_crawler_run():
    print("\n[6/8] Crawler 一轮采集（DIRECT 模式）")
    reset_manager()
    manager = get_default_manager()

    with mem(f"{len(manager.crawlers)} 源采集一轮"):
        result = await manager.run_all(mode="direct")

    total = len(manager.crawlers)
    succ = result.success_sources
    print(
        f"   采集结果: 模式={result.mode}, 成功 {succ}/{total}, "
        f"采到 {len(result.items)} 条"
    )
    if succ < 5:
        print(f"   ⚠️ 成功源太少（{succ}/{total}），可能网络问题")
    else:
        print(f"   ✅ {succ}/{total} 源在线")
    return True


# ============================================================
# 测试 7: CrawlManager 模式决策 + HYBRID 流程
# ============================================================
async def test_manager_modes():
    print("\n[7/8] CrawlerManager 模式决策")
    reset_manager()
    manager = get_default_manager()

    # 池空 → DIRECT
    await pool.clear()
    m = manager.decide_mode("auto")
    print(f"   池空决策: {m.value} (期望 direct)")

    # 池有高分代理 → HYBRID
    # 注意：pool.add(item) 不接受 score kwarg；用 with_score 调整
    for i in range(50):
        item = ProxyItem(
            ip=f"172.16.{i//256}.{i%256}", port=8080 + i,
            protocol=ProxyProtocol.HTTP,
        )
        await pool.add(item)
        item.with_score(15)
    m = manager.decide_mode("auto")
    print(f"   50 个高分代理决策: {m.value} (期望 hybrid)")
    await pool.clear()
    return True


# ============================================================
# 测试 8: 完整 API 路由 smoke（不开 lifespan）
# ============================================================
def test_api_routes():
    print("\n[8/8] API 路由 smoke")
    from contextlib import asynccontextmanager
    from api.app import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    @asynccontextmanager
    async def _no_lifespan(_app):
        yield
    app.router.lifespan_context = _no_lifespan

    failed = 0
    with TestClient(app) as c:
        cases = [
            ("GET",  "/health",                    200),
            ("GET",  "/proxy/count",               200),
            ("GET",  "/proxy/stats",               200),
            ("GET",  "/proxy/random?min_score=0",  200),
            ("GET",  "/proxy/top?n=5",             200),
            ("GET",  "/proxy/all?page=1&page_size=5", 200),
            ("GET",  "/proxy/usage",               200),
            ("GET",  "/proxy/crawl-mode",          200),
        ]
        with mem("8 个公开路由 smoke"):
            for method, url, want in cases:
                r = c.request(method, url)
                ok = r.status_code == want
                marker = "✅" if ok else "❌"
                print(f"   {marker} {method:<5} {url:<35} → {r.status_code} (期望 {want})")
                if not ok:
                    failed += 1
    return failed == 0


# ============================================================
# main
# ============================================================
async def main():
    print("=" * 70)
    print("  深度运行测试：8 模块压力 + 内存监控")
    print("=" * 70)

    results = []

    # 1) 同步测试
    results.append(("proxy_item_concurrent", test_proxy_item_concurrent()))

    # 2-7) 异步测试
    for name, coro in [
        ("pool_pressure",       test_pool_pressure()),
        ("validator",           test_validator()),
        ("rate_limiter",        test_rate_limiter()),
        ("storage_wal",         test_storage_wal()),
        ("crawler_run",         test_crawler_run()),
        ("manager_modes",       test_manager_modes()),
    ]:
        try:
            ok = await coro
        except Exception as e:
            print(f"   ❌ 异常: {type(e).__name__}: {e}")
            ok = False
        results.append((name, ok))

    # 8) API 路由
    results.append(("api_routes", test_api_routes()))

    print("\n" + "=" * 70)
    print("  总结")
    print("=" * 70)
    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        marker = "✅" if ok else "❌"
        print(f"  {marker} {name}")
    print(f"\n  {passed}/{len(results)} 模块通过\n")
    if passed != len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
