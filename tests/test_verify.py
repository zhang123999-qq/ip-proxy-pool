"""
测试 verify 模式（先验证再返回）
================================

覆盖场景：
1. 默认模式（verify=false）：不验证，0ms 返回
2. verify=true：先 TCP 预筛，找可用代理
3. verify=true 但池子无可用代理：返回空
4. batch 模式 + verify=true
5. min_score 过滤
6. verify_timeout 超时
"""
import asyncio
import pytest
from httpx import AsyncClient, ASGITransport

from api import app
from models import ProxyItem, ProxyProtocol
from pool import pool


# ============================================================
# 测试辅助：清空池子
# ============================================================
@pytest.fixture(autouse=True)
def clear_pool():
    """每个测试前清空池子（避免互相干扰）"""
    pool._pool.clear()
    yield
    pool._pool.clear()


# ============================================================
# 测试 1：默认模式（verify=false）不验证
# ============================================================
@pytest.mark.asyncio
async def test_random_default_no_verify():
    """默认模式：直接返回池中的代理（0ms）"""
    pool._pool["1.2.3.4:8080"] = ProxyItem(
        ip="1.2.3.4", port=8080, protocol=ProxyProtocol.HTTP, score=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/proxy/random")
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 0
        assert data["msg"] == "ok"
        assert data["data"]["ip"] == "1.2.3.4"


# ============================================================
# 测试 2：verify=true 模式，找不到可用代理时返回空
# ============================================================
@pytest.mark.asyncio
async def test_random_with_verify_no_usable():
    """verify=true：池中代理都连不通 → 返回 code=1"""
    # 加一个连不通的代理（127.0.0.1:1 几乎肯定连不通）
    pool._pool["127.0.0.1:1"] = ProxyItem(
        ip="127.0.0.1", port=1, protocol=ProxyProtocol.HTTP, score=10
    )
    pool._pool["127.0.0.1:2"] = ProxyItem(
        ip="127.0.0.1", port=2, protocol=ProxyProtocol.HTTP, score=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(
            "/proxy/random?verify=true&verify_timeout=0.3"
        )
        assert r.status_code == 200
        data = r.json()
        # 验证 3 次都失败 → code=1
        assert data["code"] == 1
        assert data["data"] is None


# ============================================================
# 测试 3：verify=true 找到可用代理（127.0.0.1 自连接）
# ============================================================
@pytest.mark.asyncio
async def test_random_with_verify_success():
    """verify=true：找可用代理 → 返回带 (verified) 标记"""
    # 找一个能连通的代理——用本机 nginx 端口（如果跑着的话）
    # 实际测试用 1.1.1.1 是不行的，因为我们不是用代理去测，是测代理本身
    # 所以这里用一个"必然失败"的 IP，但验证 _random_one_with_verify 函数的逻辑
    pool._pool["1.1.1.1:80"] = ProxyItem(
        ip="1.1.1.1", port=80, protocol=ProxyProtocol.HTTP, score=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get(
            "/proxy/random?verify=true&verify_timeout=2.0"
        )
        assert r.status_code == 200
        data = r.json()
        # 1.1.1.1:80 TCP 实际能连通 → 应该返回成功
        if data["code"] == 0:
            assert "(verified)" in data["msg"]
            assert data["data"]["ip"] == "1.1.1.1"
        else:
            # 网络限制时也可能失败，跳过
            pytest.skip("网络环境无法连接 1.1.1.1:80")


# ============================================================
# 测试 4：min_score 过滤
# ============================================================
@pytest.mark.asyncio
async def test_random_min_score_filter():
    """min_score=15 只返高分别的"""
    pool._pool["1.2.3.4:8080"] = ProxyItem(
        ip="1.2.3.4", port=8080, score=5
    )
    pool._pool["1.2.3.5:8081"] = ProxyItem(
        ip="1.2.3.5", port=8081, score=18
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/proxy/random?min_score=15")
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 0
        # 必须返回分数 >= 15 的
        assert data["data"]["score"] >= 15
        assert data["data"]["ip"] == "1.2.3.5"


# ============================================================
# 测试 5：protocol 过滤
# ============================================================
@pytest.mark.asyncio
async def test_random_protocol_filter():
    """protocol=https 只返 https 的"""
    pool._pool["1.2.3.4:8080"] = ProxyItem(
        ip="1.2.3.4", port=8080, protocol=ProxyProtocol.HTTP, score=10
    )
    pool._pool["1.2.3.5:443"] = ProxyItem(
        ip="1.2.3.5", port=443, protocol=ProxyProtocol.HTTPS, score=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/proxy/random?protocol=https")
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 0
        assert data["data"]["protocol"] == "https"
        assert data["data"]["ip"] == "1.2.3.5"


# ============================================================
# 测试 6：batch 默认模式
# ============================================================
@pytest.mark.asyncio
async def test_batch_default():
    """batch 默认模式"""
    for i in range(5):
        pool._pool[f"1.2.3.{i}:8080"] = ProxyItem(
            ip=f"1.2.3.{i}", port=8080, score=10
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/proxy/batch",
            json={"n": 3, "protocol": "http", "min_score": 1},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["code"] == 0
        assert data["data"]["count"] == 3
        assert len(data["data"]["items"]) == 3


# ============================================================
# 测试 7：batch verify 模式（池子无可用代理）
# ============================================================
@pytest.mark.asyncio
async def test_batch_with_verify_no_usable():
    """batch verify=true：池子无可用 → count=0"""
    pool._pool["127.0.0.1:1"] = ProxyItem(
        ip="127.0.0.1", port=1, score=10
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post(
            "/proxy/batch",
            json={
                "n": 3,
                "verify": True,
                "verify_timeout": 0.3,
            },
        )
        assert r.status_code == 200
        data = r.json()
        # 都连不通 → count=0
        assert data["data"]["count"] == 0
        assert data["msg"] == "ok (verified)"


# ============================================================
# 测试 8：验证不影响打分（verify 失败不扣分）
# ============================================================
@pytest.mark.asyncio
async def test_verify_does_not_affect_score():
    """verify 模式失败不能扣分（探测性验证）"""
    proxy = ProxyItem(
        ip="127.0.0.1", port=1, protocol=ProxyProtocol.HTTP, score=15
    )
    pool._pool["127.0.0.1:1"] = proxy

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 调 verify 模式，3 次都失败
        r = await ac.get(
            "/proxy/random?verify=true&verify_timeout=0.3"
        )
        assert r.json()["code"] == 1

    # 分数没变（应该是 15，不是 12）
    assert proxy.score == 15
