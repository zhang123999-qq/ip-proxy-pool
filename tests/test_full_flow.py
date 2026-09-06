"""
端到端测试

模拟完整流程：
1. 启动空池
2. 注入一批假代理
3. 模拟验证器反馈（一半成功一半失败）
4. 检查分数变化
5. 验证持久化 + 重启恢复
6. 验证 API 完整调用

运行：python tests/test_full_flow.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pool import pool
from models import ProxyItem
from fastapi.testclient import TestClient

from api import app


async def setup_data():
    """准备一批测试代理"""
    await pool.clear()  # 清内存 + DB
    test_ips = [
        ("10.0.0.1", 8080, 12),  # 高分
        ("10.0.0.2", 8081, 8),   # 中分
        ("10.0.0.3", 8082, 5),   # 低分
        ("10.0.0.4", 8083, 3),   # 极低
    ]
    for ip, port, score in test_ips:
        await pool.add(ProxyItem(ip=ip, port=port, score=score))


async def scenario_full_lifecycle():
    """场景：完整生命周期"""
    await setup_data()
    assert pool.size() == 4
    print(f"  初始: {pool.size()} 条")

    # 模拟一系列成功 / 失败
    # 10.0.0.1 (12分) 全部成功 → 涨到 20
    for _ in range(10):
        await pool.update_score("10.0.0.1", 8080, True)
    item = pool.get("10.0.0.1", 8080)
    assert item.score == 20, f"高分封顶失败: {item.score}"
    print(f"  10.0.0.1 10 次成功 → {item.score} 分（封顶）")

    # 10.0.0.4 (3分) 全部失败 → 删
    for i in range(3):
        ok, score, msg = await pool.update_score("10.0.0.4", 8083, False)
        print(f"  10.0.0.4 第{i+1}次失败: {msg}")
    assert not pool.contains("10.0.0.4", 8083), "应该被删除"
    print(f"  10.0.0.4 被删除，剩 {pool.size()} 条")

    # 持久化 + 重启
    n = await pool.save()
    assert n == 3
    pool._pool.clear()
    assert pool.size() == 0
    await pool.load()
    assert pool.size() == 3
    assert pool.get("10.0.0.1", 8080).score == 20
    print(f"  重启后恢复: {pool.size()} 条，10.0.0.1={pool.get('10.0.0.1', 8080).score} 分")


async def scenario_weighted_random():
    """场景：加权随机"""
    await setup_data()
    # 统计 1000 次抽样
    counter = {"10.0.0.1": 0, "10.0.0.2": 0, "10.0.0.3": 0, "10.0.0.4": 0}
    for _ in range(1000):
        item = pool.random_one()
        if item:
            counter[item.ip] += 1
    # 高分应该出现更多
    assert counter["10.0.0.1"] > counter["10.0.0.4"], \
        f"加权失效: {counter}"
    print(f"  1000 次抽样: {counter}")


def scenario_api_flow():
    """场景：API 完整调用"""
    with TestClient(app) as c:
        # 健康
        r = c.get("/health")
        assert r.status_code == 200

        # 随机
        r = c.get("/proxy/random")
        assert r.status_code == 200
        d = r.json()
        print(f"  /proxy/random → {d['msg']}")

        # 反馈成功
        target = d["data"]
        r = c.post(
            "/proxy/feedback",
            json={"ip": target["ip"], "port": target["port"], "success": True},
        )
        assert r.status_code == 200
        print(f"  /proxy/feedback success → {r.json()['msg']}")

        # 反馈非法 IP
        r = c.post(
            "/proxy/feedback",
            json={"ip": "not-an-ip", "port": 80, "success": True},
        )
        assert r.status_code == 422
        print(f"  /proxy/feedback 非法 IP → 422 (Pydantic 拒绝)")

        # stats
        r = c.get("/proxy/stats")
        d = r.json()["data"]
        assert d["size"] >= 1
        print(f"  /proxy/stats → size={d['size']} avg={d['avg_score']}")

        # top
        r = c.get("/proxy/top?n=3")
        d = r.json()["data"]
        assert "items" in d
        print(f"  /proxy/top?n=3 → {len(d['items'])} 条")

        # 列表
        r = c.get("/proxy/all?page=1&page_size=10")
        d = r.json()["data"]
        assert d["total"] >= 1
        print(f"  /proxy/all → total={d['total']}")

        # 删除
        r = c.post("/proxy/remove", json={"ip": "10.0.0.99", "port": 9999})
        assert r.status_code == 404
        print(f"  /proxy/remove 不存在 → 404")


async def scenario_protocol_filter():
    """场景：协议过滤"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, protocol="http", score=10))
    await pool.add(ProxyItem(ip="2.2.2.2", port=443, protocol="https", score=10))
    # 只拿 http
    item = pool.random_one(protocol="http")
    assert item.ip == "1.1.1.1", f"过滤失效: {item.ip}"
    print(f"  协议过滤 http → {item.ip}")

    # 只拿 https
    item = pool.random_one(protocol="https")
    assert item.ip == "2.2.2.2", f"过滤失效: {item.ip}"
    print(f"  协议过滤 https → {item.ip}")


async def scenario_min_score_filter():
    """场景：最低分门槛"""
    pool._pool.clear()
    await pool.add(ProxyItem(ip="1.1.1.1", port=80, score=1))
    await pool.add(ProxyItem(ip="2.2.2.2", port=80, score=10))

    # min_score=5 应只拿到 2.2.2.2
    for _ in range(50):
        item = pool.random_one(min_score=5)
        assert item is not None
        assert item.ip == "2.2.2.2"
    print(f"  min_score=5 过滤：连续 50 次都拿到 2.2.2.2")


async def scenario_batch():
    """场景：批量获取"""
    pool._pool.clear()
    for i in range(10):
        await pool.add(ProxyItem(ip=f"10.0.0.{i+1}", port=8080, score=10 + i))

    items = pool.random_n(n=5, min_score=10)
    assert len(items) == 5
    # 不重复
    assert len({it.ip for it in items}) == 5
    print(f"  random_n(5) → {[it.ip for it in items]}")


if __name__ == "__main__":
    print("\n[1] 完整生命周期（打分 + 持久化 + 重启）")
    asyncio.run(scenario_full_lifecycle())

    print("\n[2] 加权随机分布")
    asyncio.run(scenario_weighted_random())

    print("\n[3] API 完整调用")
    scenario_api_flow()

    print("\n[4] 协议过滤")
    asyncio.run(scenario_protocol_filter())

    print("\n[5] 最低分门槛")
    asyncio.run(scenario_min_score_filter())

    print("\n[6] 批量获取")
    asyncio.run(scenario_batch())

    print("\n=== 端到端测试全部通过 ===")
