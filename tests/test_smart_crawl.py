"""
智能爬源（代理养代理）单元测试

覆盖：
1. 模式决策（auto / 显式）
2. 软扣分（不触发自动删除）
3. 爬源代理冷却（防重复用）
4. 降级链（连续失败 → SKIP）
5. 直连 vs 代理爬源

运行：python tests/test_smart_crawl.py
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crawler.manager import CrawlerManager, CrawlMode
from pool import pool
from models import ProxyItem


# ============================================================
# 模式决策
# ============================================================
async def test_decide_mode_direct_when_empty():
    """池空 → DIRECT"""
    pool._pool.clear()
    m = CrawlerManager([])
    mode = m.decide_mode("auto")
    assert mode == CrawlMode.DIRECT
    print("✓ test_decide_mode_direct_when_empty")


async def test_decide_mode_explicit():
    """显式模式直接返回"""
    pool._pool.clear()
    m = CrawlerManager([])
    assert m.decide_mode("direct") == CrawlMode.DIRECT
    assert m.decide_mode("proxy") == CrawlMode.PROXY
    assert m.decide_mode("hybrid") == CrawlMode.HYBRID
    assert m.decide_mode("skip") == CrawlMode.SKIP
    print("✓ test_decide_mode_explicit")


async def test_decide_mode_skip_after_full_fail():
    """连续 N 次全失败 → SKIP"""
    pool._pool.clear()
    for i in range(10):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=8080, score=15
        ))
    m = CrawlerManager([])
    m._consecutive_full_fails = 5
    mode = m.decide_mode("auto")
    assert mode == CrawlMode.SKIP
    print("✓ test_decide_mode_skip_after_full_fail")


async def test_decide_mode_proxy_when_few():
    """池有少量高分代理 → PROXY"""
    pool._pool.clear()
    for i in range(3):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=8080, score=15
        ))
    m = CrawlerManager([])
    mode = m.decide_mode("auto")
    assert mode == CrawlMode.PROXY
    print("✓ test_decide_mode_proxy_when_few")


async def test_decide_mode_hybrid_when_many():
    """池有 ≥ 阈值 → HYBRID"""
    pool._pool.clear()
    for i in range(8):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=8080, score=15
        ))
    m = CrawlerManager([])
    mode = m.decide_mode("auto")
    assert mode == CrawlMode.HYBRID
    print("✓ test_decide_mode_hybrid_when_many")


# ============================================================
# 软扣分
# ============================================================
async def test_soft_score_no_delete():
    """软扣分：失败 10 次也不会自动删除"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, score=10))
    for _ in range(10):
        await pool.update_score_soft(
            "1.1.1.1", 80, False, reason="test"
        )
    item = pool.get("1.1.1.1", 80)
    assert item is not None, "软扣分不应删除代理"
    assert item.score == 0
    print("✓ test_soft_score_no_delete (10 次软扣后仍在池)")


async def test_hard_score_still_deletes():
    """硬扣分仍然自动删除"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, score=10))
    for _ in range(4):
        await pool.update_score("1.1.1.1", 80, False)
    assert pool.get("1.1.1.1", 80) is None
    print("✓ test_hard_score_still_deletes")


# ============================================================
# 爬源代理冷却
# ============================================================
async def test_crawl_candidates_cooldown():
    """冷却期内不返回同一代理"""
    pool._pool.clear()
    for i in range(3):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=80, score=15
        ))
    cand1 = pool.get_crawl_candidates(limit=10, cooldown_sec=300)
    assert len(cand1) == 3
    for c in cand1:
        pool.mark_used_for_crawl(c.ip, c.port)
    cand2 = pool.get_crawl_candidates(limit=10, cooldown_sec=300)
    assert len(cand2) == 0
    print("✓ test_crawl_candidates_cooldown")


async def test_crawl_candidates_min_score():
    """低分代理不作为爬源候选"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, score=5))
    await pool.add(ProxyItem(ip="2.2.2.2", port=80, score=15))
    cand = pool.get_crawl_candidates(
        limit=10, min_score=10, cooldown_sec=0
    )
    assert len(cand) == 1
    assert cand[0].ip == "2.2.2.2"
    print("✓ test_crawl_candidates_min_score")


async def test_crawl_candidates_cooldown_expired():
    """冷却过期后可以再次使用"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, score=15))
    pool.mark_used_for_crawl("1.1.1.1", 80)
    assert len(pool.get_crawl_candidates(cooldown_sec=300)) == 0
    cand = pool.get_crawl_candidates(cooldown_sec=0)
    assert len(cand) == 1
    print("✓ test_crawl_candidates_cooldown_expired")


# ============================================================
# 端到端：智能爬源
# ============================================================
async def test_run_all_direct_mode():
    """直连模式：池空也跑得动"""
    pool._pool.clear()
    c1 = MagicMock()
    c1.name = "src1"
    c1.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])
    c2 = MagicMock()
    c2.name = "src2"
    c2.fetch = AsyncMock(return_value=[ProxyItem(ip="2.2.2.2", port=80)])
    m = CrawlerManager([c1, c2])
    result = await m.run_all(mode="direct")
    assert result.mode == "direct"
    assert result.failed_sources == 0
    assert len(result.items) == 2
    for it in result.items:
        assert it.source in ("src1", "src2")
    print("✓ test_run_all_direct_mode")


async def test_run_all_proxy_mode_uses_pool():
    """代理模式：用池里的代理去爬"""
    pool._pool.clear()
    for i in range(2):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=8080, score=15,
        ))
    c1 = MagicMock()
    c1.name = "src1"
    c1.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])
    m = CrawlerManager([c1])
    result = await m.run_all(mode="proxy")
    assert result.mode == "proxy"
    assert len(result.proxy_used) >= 1
    usage = pool.get_usage()
    assert len(usage) >= 1
    print(f"✓ test_run_all_proxy_mode_uses_pool (用了 {len(result.proxy_used)} 个代理)")


async def test_run_all_skip_mode():
    """SKIP 模式直接返回"""
    pool._pool.clear()
    c1 = MagicMock()
    c1.name = "src1"
    c1.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])
    m = CrawlerManager([c1])
    result = await m.run_all(mode="skip")
    assert result.mode == "skip"
    assert len(result.items) == 0
    c1.fetch.assert_not_called()
    print("✓ test_run_all_skip_mode")


async def test_run_all_proxy_fail_soft_score():
    """代理爬源失败 → 软扣分（不删除）"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="10.0.0.1", port=8080, score=15))
    await pool.update_score_soft("10.0.0.1", 8080, False, reason="crawl")
    item = pool.get("10.0.0.1", 8080)
    assert item is not None
    assert item.score == 14
    print("✓ test_run_all_proxy_fail_soft_score")


async def test_proxy_assignment_limit():
    """单源代理数限制"""
    pool._pool.clear()
    for i in range(10):
        await pool.add(ProxyItem(
            ip=f"10.0.0.{i+1}", port=8080, score=15,
        ))
    m = CrawlerManager([])
    cs = []
    for i in range(3):
        c = MagicMock()
        c.name = f"src{i}"
        cs.append(c)
    m.crawlers = cs
    candidates = pool.get_crawl_candidates(limit=100)
    assignment = m._assign_proxies(candidates, "proxy")
    for name, ps in assignment.items():
        assert len(ps) <= 2, f"{name} 拿了 {len(ps)} 个代理"
    print("✓ test_proxy_assignment_limit (单源 ≤ 2 代理)")


# ============================================================
# 入口
# ============================================================
async def main():
    await test_decide_mode_direct_when_empty()
    await test_decide_mode_explicit()
    await test_decide_mode_skip_after_full_fail()
    await test_decide_mode_proxy_when_few()
    await test_decide_mode_hybrid_when_many()

    await test_soft_score_no_delete()
    await test_hard_score_still_deletes()
    await test_crawl_candidates_cooldown()
    await test_crawl_candidates_min_score()
    await test_crawl_candidates_cooldown_expired()

    await test_run_all_direct_mode()
    await test_run_all_proxy_mode_uses_pool()
    await test_run_all_skip_mode()
    await test_run_all_proxy_fail_soft_score()
    await test_proxy_assignment_limit()

    print("\n=== 智能爬源测试 14/14 通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
