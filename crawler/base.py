"""
代理爬虫基础抽象类（反爬增强版）
==============================

本模块重点：
1. 完整的浏览器请求头（UA / Referer / Accept / Accept-Language）
2. 全局限速令牌桶（每秒最多 N 个请求，HTTP 共享）
3. 单源随机延时（CRAWL_MIN_DELAY ~ CRAWL_MAX_DELAY）
4. 单源失败计数 + 指数退避熔断
5. 自动重试（最多 3 次，退避时间指数上升）

子类只需实现 fetch()，即可获得上述全部反爬能力。

继承示例：
    class MyCrawler(BaseCrawler):
        name = "my-source"
        async def fetch(self) -> List[ProxyItem]:
            html = await self._get_html("https://example.com")
            return [self.make_item("1.2.3.4", 8080)]
"""
import asyncio
import random
import re
import time
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

import httpx
import chardet
from bs4 import BeautifulSoup


# ============================================================
# 编码探测（解决 httpx.Response 没有 apparent_encoding 问题）
# ============================================================
def _detect_charset(raw: bytes) -> str:
    """
    用 chardet 探测响应原始字节编码（httpx 0.27+ 取消了 apparent_encoding）。

    :param raw: 响应原始字节
    :return: 探测到的编码（兜底 utf-8）
    """
    if not raw:
        return "utf-8"
    try:
        detected = chardet.detect(raw)
        enc = (detected or {}).get("encoding") or "utf-8"
        # chardet 在非 ASCII 极短内容上可能返回 None
        if not enc:
            return "utf-8"
        return enc
    except Exception:
        return "utf-8"

# 下方 import 故意放在 _detect_charset 定义之后（让工具函数靠近文件顶部方便查阅）
from config import (  # noqa: E402
    CRAWL_TIMEOUT,         # 单次请求超时
    USER_AGENTS,           # UA 池
    REFERERS,              # Referer 池
    ACCEPT_LANGUAGES,      # Accept-Language 池
    CRAWL_FAKE_BROWSER,    # 是否加更真实头
    CRAWL_MIN_DELAY,       # 随机延时下限
    CRAWL_MAX_DELAY,       # 随机延时上限
    CRAWL_RATE_PER_SEC,    # 全局限速（每秒 N 个请求）
    CRAWL_MAX_FAIL,        # 单源熔断的失败阈值
    CRAWL_BACKOFF_BASE,    # 退避基准秒数
)
from models import ProxyItem, ProxyProtocol  # noqa: E402
from utils import logger  # noqa: E402


# ============================================================
# 全局限速器（令牌桶）：所有爬虫共享一个，防止突发请求触发风控
# ============================================================
class RateLimiter:
    """
    令牌桶限流器（单例约束：单进程单事件循环使用）

    工作原理（直觉）：
    ┌────────────────────────────────────┐
    │           令牌桶（容量 5）           │
    │                                    │
    │   每秒自动补充 2 个令牌              │
    │   acquire() 拿走一个，没有就 sleep 等 │
    └────────────────────────────────────┘

    多个爬虫并发 acquire 时，串行化在锁里，保证「一致性」。

    注意：是 class 级别状态（_tokens / _last_refill），
         只适用于「单进程 + 单 asyncio 事件循环」。
         如果多进程跑（比如 gunicorn），每个进程要独立的 RateLimiter。
    """

    # 协程间互斥（避免 race 修改 _tokens / _last_refill）
    _lock = asyncio.Lock()
    # 当前桶里的令牌数（最多 _capacity）
    _tokens: float = 0.0
    # 上次补充令牌的时间戳（time.time() 秒数）
    _last_refill: float = 0.0
    # 每秒补充速率（个/秒）
    _rate: float = CRAWL_RATE_PER_SEC
    # 桶容量（最多攒 5 个 —— 应对突发）
    _capacity: float = 5.0

    @classmethod
    async def acquire(cls) -> None:
        """
        获取一个令牌。没有就等到有为止。
        协程安全（asyncio.Lock 序列化）。
        """
        async with cls._lock:
            now = time.time()
            if cls._last_refill == 0.0:
                # 第一次调用，初始化时间戳
                cls._last_refill = now
            # 计算补充：距上次过去了多久 × 速率 = 新增令牌数
            elapsed = now - cls._last_refill
            cls._tokens = min(
                cls._capacity,
                cls._tokens + elapsed * cls._rate,
            )
            cls._last_refill = now
            if cls._tokens < 1:
                # 令牌不够 → 计算需要等多久才能攒到 1 个
                wait = (1 - cls._tokens) / cls._rate
                cls._tokens = 0  # 在让出锁之前归零（避免其他协程看到「半满」）
            else:
                # 够 → 直接拿走一个
                wait = 0
                cls._tokens -= 1
        # 出锁后 sleep（持锁时 sleep 会卡死其他 acquire）
        if wait > 0:
            await asyncio.sleep(wait)


# ============================================================
# 爬虫抽象基类
# ============================================================
class BaseCrawler(ABC):
    """
    代理源爬虫基类

    使用方法（子类示例）：
        class MyCrawler(BaseCrawler):
            name = "my-source"
            async def fetch(self) -> List[ProxyItem]:
                html = await self._get_html("https://example.com")
                return [self.make_item("1.2.3.4", 8080)]
    """

    # 子类必须指定名字（用于日志 / 统计 / DB source 字段）
    name: str = "base"

    def __init__(self, timeout: int = CRAWL_TIMEOUT):
        self.timeout = timeout
        # 失败计数（连续失败达到 CRAWL_MAX_FAIL 触发熔断）
        self._fail_count = 0
        # 下次允许重试的时间戳（熔断期内 acquire 不发请求）
        self._cooldown_until = 0.0

    # ---------------- 反爬相关（不直接调用，全由 _request 内部用）----------------
    def _random_headers(self) -> dict:
        """
        生成一组随机浏览器头
        - UA 随机（USER_AGENTS 池里抽）
        - Accept / Accept-Encoding / Upgrade-Insecure-Requests 等固定真实浏览器的头
        - 可选 Accept-Language / Referer（CRAWL_FAKE_BROWSER=True 时随机加）
        """
        ua = random.choice(list(USER_AGENTS))
        headers = {
            "User-Agent": ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
        }
        if CRAWL_FAKE_BROWSER:
            # 加上这些头更像真实浏览器（防 bypass）
            headers["Accept-Language"] = random.choice(
                list(ACCEPT_LANGUAGES)
            )
            headers["Referer"] = random.choice(list(REFERERS))
            # 移动端 UA 时加上移动平台标记（Sec-Fetch-*）
            if "Mobile" in ua or "iPhone" in ua:
                headers["Sec-Fetch-Dest"] = "document"
                headers["Sec-Fetch-Mode"] = "navigate"
        return headers

    async def _cooldown_sleep(self) -> None:
        """单源随机延时（每次请求之间）

        范围 [CRAWL_MIN_DELAY, CRAWL_MAX_DELAY] 秒
        随机化让爬虫更难被识别为「恒定频率请求」
        """
        delay = random.uniform(CRAWL_MIN_DELAY, CRAWL_MAX_DELAY)
        await asyncio.sleep(delay)

    def _is_cooled_down(self) -> bool:
        """是否已过熔断期（True = 可以请求；False = 还在 cooldown）"""
        return time.time() >= self._cooldown_until

    def _mark_success(self) -> None:
        """请求成功，重置失败计数"""
        self._fail_count = 0

    def _mark_failure(self) -> None:
        """请求失败，递增失败计数；必要时触发熔断

        熔断策略：连续失败 CRAWL_MAX_FAIL 次后，进入冷却期
        冷却时长 = CRAWL_BACKOFF_BASE × 2^(超过阈值的次数)
        例如：第 3 次失败 → 5×1=5s；第 4 次失败 → 5×2=10s；第 5 次 → 5×4=20s
        这样反复抓失败能自动延长冷却，避免被目标站 ban IP
        """
        self._fail_count += 1
        if self._fail_count >= CRAWL_MAX_FAIL:
            backoff = CRAWL_BACKOFF_BASE * (
                2 ** (self._fail_count - CRAWL_MAX_FAIL)
            )
            self._cooldown_until = time.time() + backoff
            logger.warning(
                f"[{self.name}] 连续 {self._fail_count} 次失败，"
                f"熔断 {backoff:.0f}s"
            )

    # ---------------- HTTP 通用方法（自带反爬 + 重试） ----------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        **kwargs,
    ) -> Optional[httpx.Response]:
        """
        带反爬 + 重试的 HTTP 请求

        调用流程：
        ┌─ 熔断检查（cooldown 中直接返 None）
        ├─ 拿令牌（限速）
        ├─ 随机延时
        └─ for attempt 1..max_retries:
              构造随机头
              发请求
              ├─ 200 → mark_success → 返回
              ├─ 403/429/503（被风控）→ 指数退避后重试
              ├─ 超时/连不上 → mark_failure 一次（多次重试仍是同一次失败计数）
              └─ 其他错误 → mark_failure

        注意：mark_failure 只算「完全耗尽重试」才算一次；中途 retry 不算
        """
        # 1. 熔断中 → 直接放弃（不消耗令牌）
        if not self._is_cooled_down():
            wait = self._cooldown_until - time.time()
            logger.info(
                f"[{self.name}] 熔断中，跳过（剩余 {wait:.0f}s）"
            )
            return None

        # 2. 全局限速（拿令牌）
        await RateLimiter.acquire()
        # 3. 单源随机延时
        await self._cooldown_sleep()

        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                headers = self._random_headers()
                # 合并调用方自定义 headers（可覆盖默认值）
                if "headers" in kwargs:
                    headers.update(kwargs.pop("headers"))
                # httpx.AsyncClient 是异步 HTTP 客户端，每次新建（避免连接池污染）
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    headers=headers,
                    follow_redirects=True,
                    http2=False,  # 大部分免费源不支持 h2，避免协议差异
                ) as client:
                    resp = await client.request(method, url, **kwargs)
                    if resp.status_code in (403, 429, 503):
                        # 这些状态码表示「被风控了」
                        logger.warning(
                            f"[{self.name}] HTTP {resp.status_code} (被风控)"
                        )
                        last_err = httpx.HTTPStatusError(
                            "blocked",
                            request=resp.request,
                            response=resp,
                        )
                        # 指数退避后重试
                        await asyncio.sleep(
                            CRAWL_BACKOFF_BASE * attempt
                        )
                        continue
                    # 其他 4xx/5xx 抛异常 → 走到 except
                    resp.raise_for_status()
                    self._mark_success()
                    return resp
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.NetworkError,
            ) as e:
                last_err = e
                logger.debug(
                    f"[{self.name}] 第 {attempt} 次网络异常: "
                    f"{type(e).__name__}"
                )
            except Exception as e:
                last_err = e
                logger.debug(
                    f"[{self.name}] 第 {attempt} 次异常: "
                    f"{type(e).__name__}: {e}"
                )
            # 失败退避（最后一次失败不 sleep，让失败更快暴露）
            if attempt < max_retries:
                await asyncio.sleep(CRAWL_BACKOFF_BASE * attempt)
        # 全部重试失败 → mark_failure 触发熔断
        self._mark_failure()
        logger.warning(
            f"[{self.name}] 重试 {max_retries} 次仍失败: "
            f"{type(last_err).__name__ if last_err else '?'}"
        )
        return None

    async def _get_html(self, url: str, **kwargs) -> Optional[str]:
        """GET 并返回 HTML 文本（包装 _request，更易用）

        自动处理编码：很多中文站没有正确声明 charset，
        用 chardet 直接探测响应原始字节编码（替代不存在的
        resp.apparent_encoding，httpx 0.27+ 取消该属性）。
        """
        resp = await self._request("GET", url, **kwargs)
        if resp is None:
            return None
        # 显式指定编码，避免中文站乱码
        if resp.encoding is None or resp.encoding == "ISO-8859-1":
            raw = await resp.aread()
            resp.encoding = _detect_charset(raw)
        return resp.text

    async def _get_json(self, url: str, **kwargs) -> Optional[dict]:
        """GET 并返回 JSON（包装 _request，更易用）"""
        resp = await self._request("GET", url, **kwargs)
        if resp is None:
            return None
        try:
            return resp.json()
        except Exception as e:
            logger.warning(f"[{self.name}] 解析 JSON 失败: {e}")
            return None

    # ---------------- 数据构造（静态方法）----------------
    @staticmethod
    def make_item(ip: str, port: int, protocol: str = "http") -> ProxyItem:
        """
        把字符串 IP/端口转成 ProxyItem

        自动校验：
        - protocol 非法 → 兜底为 HTTP
        - 如果调用者传 str(port) 也能转 int

        用 staticmethod 是因为这个方法不需要 self 状态
        """
        try:
            proto = ProxyProtocol(protocol.lower().strip())
        except ValueError:
            proto = ProxyProtocol.HTTP
        return ProxyItem(
            ip=ip.strip(),
            port=int(port),
            protocol=proto,
        )

    @staticmethod
    def extract_ip_port(text: str) -> List[Tuple[str, int]]:
        """
        从任意文本中提取 (ip, port) 对
        支持以下格式：
          - 1.2.3.4:8080        （最常见）
          - 1.2.3.4 8080         （空格分隔）
          - 1.2.3.4,8080         （逗号分隔）
          - 1.2.3.4\\t8080       （tab 分隔）

        配套正则：
          (\\d{1,3}(?:\\.\\d{1,3}){3})  匹配 IPv4
          [\\s:,\\t]+                    1+ 个分隔符
          (\\d{2,5})                     2-5 位端口
        """
        pattern = re.compile(
            r"(\d{1,3}(?:\.\d{1,3}){3})[\s:,\t]+(\d{2,5})"
        )
        out = []
        for m in pattern.finditer(text or ""):
            ip, port = m.group(1), int(m.group(2))
            if not ProxyItem.is_valid_ip(ip):
                # 排除看起来像 IPv4 但实际非法（比如 999.999.999.999）
                continue
            if not (0 < port < 65536):
                continue
            out.append((ip, port))
        return out

    @staticmethod
    def parse_table_or_text(
        html: str,
        proto: str = "http",
        ip_col: int = 0,
        port_col: int = 1,
        proto_col: int = -1,
    ) -> List["ProxyItem"]:
        """
        通用解析器：先尝试表格，失败则正则提取文本

        适用大部分免费代理网站（HTML 表格 或 纯文本）。

        :param html: 待解析 HTML / 文本
        :param proto: 默认协议（表格无协议列时用）
        :param ip_col / port_col: 表格中 IP/端口的列下标
        :param proto_col: 协议列下标，-1 表示用 proto 默认
        """
        items: List["ProxyItem"] = []
        try:
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("table tbody tr")
            if rows:
                # ----- 表格模式 -----
                # 每行的协议默认值都用入参 proto，不要被上一行污染
                for tr in rows:
                    # 行内局部变量：避免循环外 proto 被改影响后续行
                    row_proto = proto
                    tds = tr.find_all("td")
                    # 列数不够说明这行是非数据行（表头/空白），跳过
                    if len(tds) <= max(ip_col, port_col, proto_col):
                        continue
                    ip = tds[ip_col].get_text(strip=True)
                    port = tds[port_col].get_text(strip=True)
                    # 协议列存在且值是 http/https/both → 用本格；
                    # 否则保持本行默认 row_proto（不变循环外 proto）
                    if proto_col >= 0 and len(tds) > proto_col:
                        cell_proto = tds[proto_col].get_text(
                            strip=True
                        ).lower()
                        if cell_proto in ("http", "https", "both"):
                            row_proto = cell_proto
                    if ip and port.isdigit():
                        items.append(
                            BaseCrawler.make_item(ip, port, row_proto)
                        )
            else:
                # ----- 文本 fallback（站点改版后没表格）-----
                for ip, port in BaseCrawler.extract_ip_port(html):
                    items.append(
                        BaseCrawler.make_item(ip, port, proto)
                    )
        except Exception:
            # BeautifulSoup 偶尔会抛（比如极端畸形 HTML），静默吞
            pass
        return items

    # ---------------- 抽象方法（子类必须实现） ----------------
    @abstractmethod
    async def fetch(self) -> List[ProxyItem]:
        """
        子类实现：从数据源抓取代理，返回 ProxyItem 列表

        子类建议只做「请求 + 解析」两步：
        - 请求统一用 self._get_html(url) —— 自动限速 / 反爬 / 重试 / 熔断
        - 解析统一用 self.parse_table_or_text(html) 或 self.extract_ip_port(text)

        异常处理：
        - 抛异常 → 内部 manager 会兜住（计入 failed_sources）
        - 返回 [] → 正常空结果（不计入失败）
        """
        raise NotImplementedError
