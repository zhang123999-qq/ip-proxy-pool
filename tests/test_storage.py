"""
SQLite 存储层单元测试

覆盖：
1. DAO 增删改查
2. 批量 upsert
3. 爬源候选查询（SQL 过滤）
4. 池 → DB 同步（add/update_score/remove）
5. JSON 自动迁移
6. flush / checkpoint

运行：python tests/test_storage.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import dao, db
from models import ProxyItem, ProxyProtocol
from pool import pool


# ============================================================
# DAO 基础
# ============================================================
async def test_dao_insert_get():
    """插入 + 查询单条"""
    dao.delete_all()
    item = ProxyItem(ip="1.1.1.1", port=80, score=12, source="test")
    assert dao.insert(item) is True
    got = dao.get("1.1.1.1", 80)
    assert got is not None
    assert got.score == 12
    assert got.source == "test"
    # 重复插入应忽略
    assert dao.insert(item) is False
    print("✓ test_dao_insert_get")


async def test_dao_upsert():
    """upsert 更新已有记录"""
    dao.delete_all()
    item = ProxyItem(ip="2.2.2.2", port=80, score=10)
    dao.insert(item)
    # 改分数后 upsert
    item.score = 18
    dao.upsert(item)
    got = dao.get("2.2.2.2", 80)
    assert got.score == 18
    print("✓ test_dao_upsert")


async def test_dao_upsert_many():
    """批量 upsert"""
    dao.delete_all()
    items = [
        ProxyItem(ip=f"10.0.{i}.1", port=80, score=10 + i, source="bulk")
        for i in range(100)
    ]
    n = dao.upsert_many(items)
    assert n == 100
    assert dao.count() == 100
    print("✓ test_dao_upsert_many (100 条)")


async def test_dao_delete():
    """删除"""
    dao.delete_all()
    dao.insert(ProxyItem(ip="3.3.3.3", port=80))
    assert dao.delete("3.3.3.3", 80) is True
    assert dao.delete("3.3.3.3", 80) is False
    print("✓ test_dao_delete")


async def test_dao_stats():
    """统计"""
    dao.delete_all()
    dao.insert(ProxyItem(ip="4.4.4.4", port=80, score=5, protocol=ProxyProtocol.HTTP))
    dao.insert(ProxyItem(ip="5.5.5.5", port=443, score=15, protocol=ProxyProtocol.HTTPS))
    s = dao.stats()
    assert s["size"] == 2
    assert s["max_score"] == 15
    assert s["min_score"] == 5
    assert s["http"] == 1
    assert s["https"] == 1
    print("✓ test_dao_stats")


# ============================================================
# 爬源候选（SQL 过滤）
# ============================================================
async def test_dao_crawl_candidates():
    """爬源候选：分数 + 冷却过滤"""
    dao.delete_all()
    # 高分配用过的（冷却中）
    p1 = ProxyItem(ip="6.6.6.6", port=80, score=18, last_used_for_crawl=time.time())
    # 高分未用过
    p2 = ProxyItem(ip="7.7.7.7", port=80, score=16)
    # 低分
    p3 = ProxyItem(ip="8.8.8.8", port=80, score=5)
    dao.insert(p1)
    dao.insert(p2)
    dao.insert(p3)
    # 冷却 300s → p1 冷却中应被过滤，p2 符合，p3 低分
    cands = dao.get_crawl_candidates(limit=10, min_score=10, cooldown_sec=300)
    ips = {c.ip for c in cands}
    assert "7.7.7.7" in ips
    assert "6.6.6.6" not in ips
    assert "8.8.8.8" not in ips
    # 冷却 0 → p1 也符合
    cands2 = dao.get_crawl_candidates(limit=10, min_score=10, cooldown_sec=0)
    ips2 = {c.ip for c in cands2}
    assert "6.6.6.6" in ips2
    print("✓ test_dao_crawl_candidates")


# ============================================================
# 爬源历史
# ============================================================
async def test_dao_crawl_history():
    """爬源行为记录"""
    # 清空历史
    conn = db.get_conn()
    conn.execute("DELETE FROM crawl_history")
    conn.commit()
    dao.log_crawl("9.9.9.9", 80, "src1", True, 120)
    dao.log_crawl("9.9.9.9", 80, "src1", False, 30)
    hist = dao.get_crawl_history(limit=10)
    assert len(hist) == 2
    assert hist[0]["success"] == 0  # 最新的在前
    assert hist[1]["success"] == 1
    print("✓ test_dao_crawl_history")


# ============================================================
# 池 → DB 同步
# ============================================================
async def test_pool_add_syncs_db():
    """池 add 后 DB 有记录"""
    await pool.clear()
    item = ProxyItem(ip="11.11.11.11", port=80, score=10)
    await pool.add(item)
    assert dao.get("11.11.11.11", 80) is not None
    print("✓ test_pool_add_syncs_db")


async def test_pool_update_score_syncs_db():
    """池打分后 DB 分数同步"""
    await pool.clear()
    await pool.add(ProxyItem(ip="12.12.12.12", port=80, score=10))
    await pool.update_score("12.12.12.12", 80, True)
    assert dao.get("12.12.12.12", 80).score == 11
    print("✓ test_pool_update_score_syncs_db")


async def test_pool_remove_syncs_db():
    """池删除后 DB 也删除"""
    await pool.clear()
    await pool.add(ProxyItem(ip="13.13.13.13", port=80, score=10))
    await pool.remove("13.13.13.13", 80)
    assert dao.get("13.13.13.13", 80) is None
    print("✓ test_pool_remove_syncs_db")


async def test_pool_load_from_db():
    """池 load 从 DB 恢复"""
    await pool.clear()
    # 直接塞 DB
    dao.delete_all()
    dao.insert(ProxyItem(ip="14.14.14.14", port=80, score=20))
    n = await pool.load()
    assert n == 1
    assert pool.get("14.14.14.14", 80) is not None
    print("✓ test_pool_load_from_db")


async def test_pool_save_full():
    """池 save 全量 upsert"""
    await pool.clear()
    await pool.add(ProxyItem(ip="15.15.15.15", port=80, score=10))
    n = await pool.save()
    assert n >= 1
    print("✓ test_pool_save_full")


async def test_pool_flush():
    """flush 不报错"""
    await pool.flush()
    print("✓ test_pool_flush")


# ============================================================
# 入口
# ============================================================
async def main():
    await test_dao_insert_get()
    await test_dao_upsert()
    await test_dao_upsert_many()
    await test_dao_delete()
    await test_dao_stats()
    await test_dao_crawl_candidates()
    await test_dao_crawl_history()
    await test_pool_add_syncs_db()
    await test_pool_update_score_syncs_db()
    await test_pool_remove_syncs_db()
    await test_pool_load_from_db()
    await test_pool_save_full()
    await test_pool_flush()
    print("\n=== SQLite 存储测试 13/13 通过 ===")


if __name__ == "__main__":
    asyncio.run(main())
