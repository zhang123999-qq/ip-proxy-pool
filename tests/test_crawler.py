"""
爬虫反爬模块单元测试

通过 mock 网络请求，验证：
1. 限速令牌桶正常工作
2. 浏览器头随机化
3. 重试 + 退避逻辑
4. 失败熔断
5. 来源解析正确

运行：python tests/test_crawler.py
"""
import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from crawler.base import BaseCrawler, RateLimiter
from crawler import BUILTIN_CRAWLERS
from models import ProxyItem


def reset_rate_limiter():
    """重置全局限速器（每个测试前调用）"""
    RateLimiter._tokens = 0.0
    RateLimiter._last_refill = 0.0


# ============================================================
# 工具方法
# ============================================================
def test_extract_ip_port():
    """测试从任意文本中提取 IP:PORT"""
    text = """
    server list:
    1.2.3.4:8080
    1.2.3.5 3128
    1.2.3.6,8888
    999.999.999.999:1234  # 非法 IP 应被过滤
    """
    pairs = BaseCrawler.extract_ip_port(text)
    assert len(pairs) == 3, f"期望 3 条，实际 {len(pairs)}"
    assert ("1.2.3.4", 8080) in pairs
    assert ("1.2.3.5", 3128) in pairs
    assert ("1.2.3.6", 8888) in pairs
    print("✓ test_extract_ip_port")


def test_parse_table_proto_no_pollution():
    """回归测试：parse_table_or_text 不会让协议变量跨行污染

    场景：第 1 行协议列是 https，第 2 行是 socks4（不在白名单），
          第 3 行是 http。Bug 出现时第 2 行会被错标为 https。
    """
    html = """
    <table>
      <tbody>
        <tr><td>1.1.1.1</td><td>80</td><td>elapsed</td><td>https</td></tr>
        <tr><td>2.2.2.2</td><td>8080</td><td>elapsed</td><td>socks4</td></tr>
        <tr><td>3.3.3.3</td><td>8888</td><td>elapsed</td><td>http</td></tr>
      </tbody>
    </table>
    """
    items = BaseCrawler.parse_table_or_text(
        html, proto="http", ip_col=0, port_col=1, proto_col=3,
    )
    proto_by_ip = {it.ip: it.protocol.value for it in items}
    assert proto_by_ip["1.1.1.1"] == "https", proto_by_ip
    # socks4 不在白名单 → 回退到入参 proto="http"，而**不是**上一行的 https
    assert proto_by_ip["2.2.2.2"] == "http", proto_by_ip
    assert proto_by_ip["3.3.3.3"] == "http", proto_by_ip
    print("✓ test_parse_table_proto_no_pollution")


def test_make_item():
    """测试 ProxyItem 构造"""
    item = BaseCrawler.make_item("1.2.3.4", 8080, "https")
    assert item.ip == "1.2.3.4"
    assert item.port == 8080
    assert item.protocol.value == "https"
    assert item.score == 10
    print("✓ test_make_item")


def test_random_headers():
    """测试请求头随机化"""
    # 用一个具体爬虫子类来测（BaseCrawler 是抽象类）
    from crawler.sources import IP66FreeCrawler
    headers_list = [IP66FreeCrawler()._random_headers() for _ in range(20)]
    # UA 应该有不同
    uas = {h["User-Agent"] for h in headers_list}
    assert len(uas) > 1, f"UA 没随机化: {uas}"
    # 必须有 User-Agent
    for h in headers_list:
        assert "User-Agent" in h
        assert "Accept" in h
    print("✓ test_random_headers")


# ============================================================
# 限速器
# ============================================================
async def test_rate_limiter():
    """测试令牌桶限流（1 秒内最多 2 个）"""
    reset_rate_limiter()
    # 临时调低速率便于测试
    RateLimiter._rate = 2.0
    RateLimiter._capacity = 2.0

    start = time.time()
    # 拿 5 个令牌
    for _ in range(5):
        await RateLimiter.acquire()
    elapsed = time.time() - start
    # 前 2 个瞬时，后 3 个需要 ~1.5 秒
    assert elapsed >= 1.0, f"限速失效：5 个请求只花 {elapsed:.2f}s"
    print(f"✓ test_rate_limiter (5 请求 {elapsed:.2f}s)")


# ============================================================
# 重试 + 退避
# ============================================================
class _MockCrawler(BaseCrawler):
    name = "mock"

    def __init__(self, fail_times: int = 0):
        super().__init__()
        self.fail_times = fail_times
        self.call_count = 0

    async def fetch(self):
        # 直接调 _request 走反爬流程
        result = await self._request("GET", "https://example.com")
        return result


async def test_retry_then_success():
    """测试重试：前 N 次失败，第 N+1 次成功"""
    reset_rate_limiter()
    responses = [
        MagicMock(status_code=503),  # 第 1 次：被风控
        MagicMock(status_code=429),  # 第 2 次：被限流
        MagicMock(status_code=200, text="ok"),  # 第 3 次：成功
    ]
    call_count = [0]

    def make_client(*args, **kwargs):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        async def request(method, url, **kw):
            r = responses[call_count[0]]
            call_count[0] += 1
            return r
        client.request = request
        return client

    with patch("crawler.base.httpx.AsyncClient", side_effect=make_client):
        c = _MockCrawler()
        # 把超时调小一点，测试更快
        c.timeout = 2
        # 把退避调成 0
        with patch("crawler.base.CRAWL_BACKOFF_BASE", 0):
            with patch("crawler.base.CRAWL_MIN_DELAY", 0):
                with patch("crawler.base.CRAWL_MAX_DELAY", 0):
                    resp = await c._request("GET", "https://example.com")
        assert resp is not None
        assert call_count[0] == 3, f"应重试 3 次，实际 {call_count[0]}"
    print("✓ test_retry_then_success")


async def test_retry_exhaust():
    """测试重试用尽后返回 None"""
    reset_rate_limiter()
    c = _MockCrawler()
    c.timeout = 2
    with patch("crawler.base.httpx.AsyncClient") as M:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        async def request(*a, **kw):
            r = MagicMock(status_code=503)
            return r

        client.request = request
        M.return_value = client
        with patch("crawler.base.CRAWL_BACKOFF_BASE", 0):
            with patch("crawler.base.CRAWL_MIN_DELAY", 0):
                with patch("crawler.base.CRAWL_MAX_DELAY", 0):
                    resp = await c._request("GET", "https://example.com")
        assert resp is None
        # 单次 _request 内部重试 3 次都失败 → mark_failure 调 1 次
        assert c._fail_count == 1
    print("✓ test_retry_exhaust")


async def test_circuit_breaker():
    """测试连续失败触发熔断（连续 3 次 _request 失败）"""
    reset_rate_limiter()
    c = _MockCrawler()
    c.timeout = 2
    with patch("crawler.base.httpx.AsyncClient") as M:
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)

        async def request(*a, **kw):
            r = MagicMock(status_code=503)
            return r

        client.request = request
        M.return_value = client
        with patch("crawler.base.CRAWL_BACKOFF_BASE", 0):
            with patch("crawler.base.CRAWL_MIN_DELAY", 0):
                with patch("crawler.base.CRAWL_MAX_DELAY", 0):
                    # 连续失败 3 次
                    for _ in range(3):
                        await c._request("GET", "https://example.com")
        assert c._fail_count == 3
        # 触发熔断（cooldown_until 被设置）
        assert c._cooldown_until > 0
        # 熔断期内再调用 → 直接 None
        resp = await c._request("GET", "https://example.com")
        assert resp is None
    print("✓ test_circuit_breaker")


# ============================================================
# 来源解析（mock 整个 _get_html）
# ============================================================
async def test_kuaidaili_parsing():
    """测试快代理解析（mock 1 页）"""
    fake_html = """
    <html><body>
    <table>
      <tbody>
        <tr><td>1.2.3.4</td><td>8080</td><td>HTTP</td><td>http</td><td>高匿</td><td>...</td><td>...</td></tr>
        <tr><td>5.6.7.8</td><td>3128</td><td>HTTPS</td><td>https</td><td>普匿</td><td>...</td><td>...</td></tr>
      </tbody>
    </table>
    </body></html>
    """
    from crawler.sources import KuaiDaiLiFreeCrawler
    c = KuaiDaiLiFreeCrawler()
    # mock 掉父类 _get_html 和延时
    c._get_html = AsyncMock(return_value=fake_html)
    c._cooldown_sleep = AsyncMock()
    # mock 掉 RateLimiter，避免测试卡住
    with patch("crawler.base.RateLimiter.acquire", new=AsyncMock()):
        items = await c.fetch()
    # 3 页 × 2 条 = 6 条（去重是 manager 的事）
    assert len(items) == 6
    ips = {it.ip for it in items}
    assert "1.2.3.4" in ips
    assert "5.6.7.8" in ips
    print(f"✓ test_kuaidaili_parsing ({len(items)} 条，3 页)")


async def test_66ip_parsing():
    """测试 66ip 文本解析"""
    fake_text = "1.2.3.4:8080 5.6.7.8:3128 9.10.11.12:8888"
    from crawler.sources import IP66FreeCrawler
    c = IP66FreeCrawler()
    c._get_html = AsyncMock(return_value=fake_text)
    c._cooldown_sleep = AsyncMock()
    with patch("crawler.base.RateLimiter.acquire", new=AsyncMock()):
        items = await c.fetch()
    assert len(items) == 3
    ips = {it.ip for it in items}
    assert "1.2.3.4" in ips
    assert "5.6.7.8" in ips
    print("✓ test_66ip_parsing")


async def test_manager_dedup():
    """测试多源去重"""
    c1 = MagicMock()
    c1.name = "c1"
    c1.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])
    c2 = MagicMock()
    c2.name = "c2"
    c2.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])
    from crawler.manager import CrawlerManager
    m = CrawlerManager([c1, c2])
    result = await m.run_all(mode="direct")
    # 智能 manager 现在返回 CrawlResult
    items = result.items
    assert len(items) == 1
    print("✓ test_manager_dedup")


async def test_manager_isolation():
    """测试单源失败不影响其他源"""
    c1 = MagicMock()
    c1.name = "ok"
    c1.fetch = AsyncMock(return_value=[ProxyItem(ip="1.1.1.1", port=80)])

    c2 = MagicMock()
    c2.name = "broken"
    c2.fetch = AsyncMock(side_effect=Exception("boom"))

    c3 = MagicMock()
    c3.name = "also-ok"
    c3.fetch = AsyncMock(return_value=[ProxyItem(ip="2.2.2.2", port=80)])

    from crawler.manager import CrawlerManager
    m = CrawlerManager([c1, c2, c3])
    result = await m.run_all(mode="direct")
    items = result.items
    assert len(items) == 2
    ips = {it.ip for it in items}
    assert ips == {"1.1.1.1", "2.2.2.2"}
    # 失败计数 1
    assert result.failed_sources == 1
    print("✓ test_manager_isolation")


# ============================================================
# 入口
# ============================================================
if __name__ == "__main__":
    test_extract_ip_port()
    test_parse_table_proto_no_pollution()
    test_make_item()
    test_random_headers()

    asyncio.run(test_rate_limiter())
    asyncio.run(test_retry_then_success())
    asyncio.run(test_retry_exhaust())
    asyncio.run(test_circuit_breaker())
    asyncio.run(test_kuaidaili_parsing())
    asyncio.run(test_66ip_parsing())
    asyncio.run(test_manager_dedup())
    asyncio.run(test_manager_isolation())

    # 打印内置源数量
    print(f"\n内置代理源数量: {len(BUILTIN_CRAWLERS)}")
    for cr in BUILTIN_CRAWLERS:
        print(f"  - {cr.name}")

    print("\n=== 爬虫测试全部通过 ===")
