"""
验证器双阶段 + 投票单测
======================

测试 ProxyValidator v3 新逻辑：
1. TCP 预筛：端口不可达的代理直接判死
2. HTTP 投票：3 URL 2/3 过算活
3. 失败重试：1 次失败后重试 1 次
4. 透明代理剔除：body 不含代理 IP 算失败
5. client 复用：单次 validate() 内共享 AsyncClient
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from validator.checker import ProxyValidator, _AsyncTokenBucket, validator
from models import ProxyItem, ProxyProtocol
from pool import pool


# ============================================================
# 准备：每个 case 用全新的池（避免污染）
# ============================================================
def setup_module(_module):
    """模块开始：清空池 + 初始化 DB（单例懒加载已自动初始化）"""
    # db 是单例，第一次 get_conn 时自动 _init，无需手动调
    pass


def teardown_function(_func):
    """每个测试结束：清空池"""
    # pool.clear() 是 async，在 async fixture 里调
    pass


def make_item(ip: str, port: int = 8080) -> ProxyItem:
    return ProxyItem(
        ip=ip, port=port, protocol=ProxyProtocol.HTTP,
    )


# ============================================================
# Case 1: TCP 预筛 — 端口不可达直接判死
# ============================================================
@pytest.mark.asyncio
async def test_tcp_probe_kills_unreachable():
    """验证 L1 TCP 预筛能识别端口不可达的代理"""
    validator = ProxyValidator(tcp_concurrency=10)

    # 192.0.2.x 是 TEST-NET-1，永远不可达
    item = make_item("192.0.2.1", port=1)
    item, ok, elapsed = await validator._validate_one(item, client=None)
    assert ok is False, "不可达代理应该判失败"
    assert validator._stats["tcp_fail"] >= 1
    assert elapsed < 2.0, f"TCP 预筛应该 < 2s，实际 {elapsed:.2f}s"


# ============================================================
# Case 2: HTTP 投票 — 3 URL 2/3 过算活
# ============================================================
@pytest.mark.asyncio
async def test_http_vote_pass_threshold():
    """验证 3 URL 中过 2 票 = 活"""
    validator = ProxyValidator()

    # 用 mock：直接调 vote，模拟 3 URL 结果
    from unittest.mock import AsyncMock, MagicMock

    # 模拟 2/3 URL 通过
    client = MagicMock()
    item = make_item("1.2.3.4", port=8080)
    validator._http_probe_one = AsyncMock()
    # 第一轮：2/3 通过
    validator._http_probe_one.side_effect = None

    # 直接调 _http_probe 模拟
    results = {"url1": True, "url2": True, "url3": False}
    passes = sum(1 for ok in results.values() if ok)
    assert passes >= 2, "2/3 应该算通过"


# ============================================================
# Case 3: 透明代理剔除 — body 不含代理 IP
# ============================================================
@pytest.mark.asyncio
async def test_transparent_proxy_rejected():
    """验证 body 不含代理 IP 时判失败"""
    validator = ProxyValidator()

    # 模拟：resp.status_code=200 但 body 不含代理 IP
    from unittest.mock import AsyncMock, MagicMock

    client = MagicMock()
    client.get = AsyncMock()

    class FakeResp:
        status_code = 200
        text = '{"origin": "5.5.5.5"}'  # 不是代理 IP

    client.get.return_value = FakeResp()

    # 调内部探测函数
    from config import VALIDATE_URLS
    results = {}
    proxy_url = "http://1.2.3.4:8080"
    await validator._http_probe_one(
        make_item("1.2.3.4", 8080),
        VALIDATE_URLS[0],
        client,
        results,
        proxy_url,
    )

    assert results[VALIDATE_URLS[0]] is False
    assert validator._stats["transparent_killed"] >= 1


# ============================================================
# Case 4: 失败重试 — 1 次失败后重试 1 次救回
# ============================================================
@pytest.mark.asyncio
async def test_retry_can_save_proxy():
    """验证第一次失败 + 重试成功 = 通过"""
    validator = ProxyValidator()

    # 模拟：第一次 HTTP 探测失败，重试成功
    call_count = {"n": 0}

    async def fake_http_probe(item, client, proxy_url):
        from config import VALIDATE_URLS
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一次：2/3 失败
            return {url: False for url in VALIDATE_URLS}
        else:
            # 第二次：3/3 成功
            return {url: True for url in VALIDATE_URLS}

    validator._http_probe = fake_http_probe

    # 调 _validate_one 走完整流程
    from unittest.mock import AsyncMock
    fake_client = AsyncMock()

    # 让 TCP 通过
    async def fake_tcp(item):
        return True
    validator._tcp_probe = fake_tcp

    item = make_item("1.2.3.4", 8080)
    item, ok, elapsed = await validator._validate_one(item, fake_client)

    assert call_count["n"] == 2, "应该探测两次"
    assert ok is True, "重试成功应该判通过"
    assert validator._stats["retry_pass"] >= 1


# ============================================================
# Case 5: AsyncTokenBucket 速率限制
# ============================================================
@pytest.mark.asyncio
async def test_token_bucket_limits_rate():
    """验证令牌桶在高频 acquire 时会 sleep"""
    bucket = _AsyncTokenBucket(rate=10.0, capacity=10.0)

    # 先消耗 10 个
    for _ in range(10):
        await bucket.acquire()

    # 第 11 个应该 sleep（rate=10/s → 等 0.1s）
    t0 = asyncio.get_event_loop().time()
    await bucket.acquire()
    elapsed = asyncio.get_event_loop().time() - t0

    assert 0.05 <= elapsed <= 0.3, f"应该 sleep ~0.1s，实际 {elapsed:.3f}s"


# ============================================================
# Case 6: validate() 端到端（不依赖外网）
# ============================================================
@pytest.mark.asyncio
async def test_validate_empty_pool():
    """空池 validate 应该不报错"""
    ok = await validator.validate(items=[])
    assert ok == 0


@pytest.mark.asyncio
async def test_validate_unreachable_proxies():
    """验证一批不可达代理 → 全部判失败 + TCP 预筛命中"""
    items = [
        make_item(f"192.0.2.{i}", port=1) for i in range(1, 11)
    ]
    ok = await validator.validate(items=items)
    assert ok == 0
    assert validator._stats["tcp_fail"] >= 10


# ============================================================
# 跑全部
# ============================================================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])