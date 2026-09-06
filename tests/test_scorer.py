"""
代理池打分引擎单元测试

运行：python -m pytest tests/ -v
或：  python tests/test_scorer.py
"""
import asyncio
import sys
from pathlib import Path

# 把项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pool import pool
from models import ProxyItem


def reset_pool():
    """重置单例状态（每个测试前调用）"""
    pool._pool.clear()
    from storage import dao
    dao.delete_all()


def test_add_and_dedup():
    reset_pool()
    p = ProxyItem(ip="1.2.3.4", port=8080)
    assert asyncio.run(pool.add(p)) is True
    assert asyncio.run(pool.add(p)) is False
    assert pool.size() == 1
    print("✓ add_and_dedup")


def test_score_up_cap():
    reset_pool()
    p = ProxyItem(ip="1.2.3.4", port=8080)
    asyncio.run(pool.add(p))
    for _ in range(50):
        asyncio.run(pool.update_score("1.2.3.4", 8080, True))
    assert pool.get("1.2.3.4", 8080).score == 20  # 上限
    print("✓ score_up_cap")


def test_score_down_remove():
    reset_pool()
    p = ProxyItem(ip="1.2.3.4", port=8080)
    asyncio.run(pool.add(p))
    # 初始 10，每次 -3
    expected = [7, 4, 1]  # 10-3, 10-6, 10-9
    for i in range(3):
        ok, score, _ = asyncio.run(pool.update_score("1.2.3.4", 8080, False))
        assert ok is True
        assert score == expected[i], f"第{i+1}次：期望 {expected[i]} 实得 {score}"
    # 第 4 次失败：1 - 3 = -2 → max(0, -2)=0 → 删除
    ok, score, msg = asyncio.run(pool.update_score("1.2.3.4", 8080, False))
    assert ok is True
    assert score == 0
    assert "已删除" in msg
    assert pool.size() == 0
    print("✓ score_down_remove")


def test_nonexistent_feedback():
    reset_pool()
    ok, _, msg = asyncio.run(pool.update_score("9.9.9.9", 1234, True))
    assert ok is False
    assert "不存在" in msg
    print("✓ nonexistent_feedback")


def test_weighted_random():
    reset_pool()
    # 入 100 个分数 1 和 100 个分数 20
    for i in range(100):
        asyncio.run(pool.add(ProxyItem(ip="10.0.0.1", port=1000 + i, score=1)))
    for i in range(100):
        asyncio.run(pool.add(ProxyItem(ip="10.0.0.2", port=2000 + i, score=20)))
    # 抽 1000 次，统计分数 20 的占比应该明显高于 50%
    high = sum(
        1 for _ in range(1000)
        if pool.random_one().ip == "10.0.0.2"
    )
    ratio = high / 1000
    assert ratio > 0.6, f"加权失效：高分占比仅 {ratio:.2%}"
    print(f"✓ weighted_random (高分占比 {ratio:.2%})")


def test_min_score_filter():
    reset_pool()
    asyncio.run(pool.add(ProxyItem(ip="1.1.1.1", port=1, score=1)))
    asyncio.run(pool.add(ProxyItem(ip="2.2.2.2", port=2, score=5)))
    # min_score=3 应该拿不到 1.1.1.1
    for _ in range(50):
        item = pool.random_one(min_score=3)
        assert item is not None and item.ip == "2.2.2.2"
    print("✓ min_score_filter")


def test_invalid_ip_rejected():
    reset_pool()
    p = ProxyItem(ip="not.an.ip", port=8080)
    assert asyncio.run(pool.add(p)) is False
    assert pool.size() == 0
    print("✓ invalid_ip_rejected")


def test_persistence_roundtrip():
    """持久化往返测试（SQLite）"""
    reset_pool()
    asyncio.run(pool.add(ProxyItem(ip="8.8.8.8", port=53)))
    asyncio.run(pool.add(ProxyItem(ip="1.1.1.1", port=53)))

    # save 全量刷盘到 SQLite
    asyncio.run(pool.save())
    assert pool.size() == 2

    # 清空内存，load 从 SQLite 恢复
    pool._pool.clear()
    asyncio.run(pool.load())
    assert pool.size() == 2
    assert pool.get("8.8.8.8", 53) is not None
    assert pool.get("1.1.1.1", 53) is not None
    print("✓ persistence_roundtrip")


if __name__ == "__main__":
    test_add_and_dedup()
    test_score_up_cap()
    test_score_down_remove()
    test_nonexistent_feedback()
    test_weighted_random()
    test_min_score_filter()
    test_invalid_ip_rejected()
    test_persistence_roundtrip()
    print("\n=== 全部通过 ===")
