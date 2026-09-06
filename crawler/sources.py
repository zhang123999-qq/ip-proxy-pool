"""
内置免费代理源（反爬适配版）
==========================

每个源都做了：
- 真实请求头（UA / Referer / Accept-Language）
- 多页翻页（如果源支持）
- 限速 + 延时（由 BaseCrawler 统一处理）
- 解析失败不抛异常，warn 后返回空列表
- 表格 + 文本双解析（站点改版自动 fallback）

注意：免费代理源经常改版，单一源不可用是常态。
任何源解析失败不会影响其他源（manager 兜底）。

要新增源？继承 BaseCrawler 实现 fetch()，加到 BUILTIN_CRAWLERS 即可。
"""
from typing import List

from .base import BaseCrawler
from models import ProxyItem
from utils import logger


# ============================================================
# 快代理：HTML 表格（HTTP 代理）
# ============================================================
class KuaiDaiLiFreeCrawler(BaseCrawler):
    """快代理免费 HTTP 代理列表"""
    name = "kuaidaili-free"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        # 翻 3 页（页码从 1 开始）
        for page in range(1, 4):
            url = f"https://www.kuaidaili.com/free/inha/{page}/"
            html = await self._get_html(url)
            if not html:
                continue
            # 第 3 列是协议类型
            page_items = self.parse_table_or_text(
                html, proto="http", ip_col=0, port_col=1, proto_col=3,
            )
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 快代理：国内高匿（备用源）
# ============================================================
class KuaiDaiLiInhaCrawler(BaseCrawler):
    """快代理国内高匿代理"""
    name = "kuaidaili-inha"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for page in range(1, 3):
            url = f"https://www.kuaidaili.com/inha/{page}/"
            html = await self._get_html(url)
            if not html:
                continue
            page_items = self.parse_table_or_text(
                html, proto="http", ip_col=0, port_col=1, proto_col=3,
            )
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 66ip.cn：兼容新旧两种页面结构
# ============================================================
class IP66FreeCrawler(BaseCrawler):
    """66ip 公开代理页"""
    name = "66ip-free"

    async def fetch(self) -> List[ProxyItem]:
        url = "http://www.66ip.cn/"
        html = await self._get_html(url)
        items: List[ProxyItem] = []
        if html:
            items = self.parse_table_or_text(html, proto="http")
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 89ip.cn（支持分页 + 表格解析）
# ============================================================
class IP89FreeCrawler(BaseCrawler):
    """89ip 公开代理页"""
    name = "89ip-free"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for page in range(1, 3):
            url = f"https://www.89ip.cn/index_{page}.html" if page > 1 \
                else "https://www.89ip.cn/"
            html = await self._get_html(url)
            if not html:
                continue
            page_items = self.parse_table_or_text(html, proto="http")
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# iphai.cn（表格 + 多页）
# ============================================================
class IPHaiFreeCrawler(BaseCrawler):
    """iphai 公开代理页"""
    name = "iphai-free"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for page in range(1, 3):
            url = f"http://www.iphai.cn/?page={page}"
            html = await self._get_html(url)
            if not html:
                continue
            page_items = self.parse_table_or_text(html, proto="http")
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# proxydaily.top
# ============================================================
class ProxyDailyCrawler(BaseCrawler):
    """proxydaily 公开代理页"""
    name = "proxydaily-http"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://www.proxydaily.top/free-http-proxy-list"
        html = await self._get_html(url)
        items: List[ProxyItem] = []
        if html:
            items = self.parse_table_or_text(html, proto="http")
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# qiyunip.com
# ============================================================
class QiYunIpFreeCrawler(BaseCrawler):
    """qiyunip 公开代理页"""
    name = "qiyunip-free"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://www.qiyunip.com/freeProxy.html"
        html = await self._get_html(url)
        items: List[ProxyItem] = []
        if html:
            items = self.parse_table_or_text(html, proto="http")
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 站大爷（HTML 表格）
# ============================================================
class ZhandayeCrawler(BaseCrawler):
    """站大爷免费代理（每页 ~20 个，HTML 表格）"""
    name = "zhandaye-free"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for page in range(1, 3):
            url = f"http://www.zdaye.com/free/{page}/"
            html = await self._get_html(url)
            if not html:
                continue
            page_items = self.parse_table_or_text(
                html, proto="http", ip_col=0, port_col=1, proto_col=2,
            )
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 快代理 HTTPS（专攻 https 协议）
# ============================================================
class KuaiDaiLiHttpsCrawler(BaseCrawler):
    """快代理免费 HTTPS 代理"""
    name = "kuaidaili-https"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for page in range(1, 3):
            url = f"https://www.kuaidaili.com/free/intr/{page}/"
            html = await self._get_html(url)
            if not html:
                continue
            page_items = self.parse_table_or_text(
                html, proto="https", ip_col=0, port_col=1, proto_col=3,
            )
            items.extend(page_items)
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 小幻 HTTP 代理（每日更新）
# ============================================================
class XiaohuanProxyCrawler(BaseCrawler):
    """小幻 HTTP 代理（表格 + 每日更新，~30 条/页）"""
    name = "xiaohuan-http"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://ip.ihuan.me/"
        html = await self._get_html(url)
        items: List[ProxyItem] = []
        if html:
            items = self.parse_table_or_text(html, proto="http")
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# ProxyScrape API（JSON/文本接口，海外站，量大）
# ============================================================
class ProxyScrapeApiCrawler(BaseCrawler):
    """ProxyScrape 公开 API（~1000 条/次）"""
    name = "proxyscrape-api"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        # 同时拿 http + https
        for proto in ("http", "https"):
            url = (
                f"https://api.proxyscrape.com/v2/?request=displayproxies"
                f"&protocol={proto}&timeout=5000&country=all"
            )
            text = await self._get_html(url)
            if not text:
                continue
            # 文本格式：每行一个 ip:port
            for line in text.splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                try:
                    ip, port = line.split(":", 1)
                    ip = ip.strip()
                    port = port.strip()
                    if ip and port.isdigit():
                        items.append(self.make_item(ip, port, proto))
                except Exception:
                    continue
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# OpenProxyList API（每日刷新）
# ============================================================
class OpenProxyListCrawler(BaseCrawler):
    """OpenProxyList（纯文本格式，~500 条）"""
    name = "openproxylist-http"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://api.openproxylist.xyz/http.txt"
        text = await self._get_html(url)
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "http"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# Proxy-List 实时 API
# ============================================================
class ProxyListDownloadCrawler(BaseCrawler):
    """Proxy-List.download（JSON / 文本两种，~100 条/次）"""
    name = "proxylist-download-http"

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for proto in ("http", "https"):
            url = f"https://www.proxy-list.download/api/v1/get?type={proto}"
            text = await self._get_html(url)
            if not text:
                continue
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, proto))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# FreeProxyList（HTML 表格，海外 IP 多）
# ============================================================
class FreeProxyListCrawler(BaseCrawler):
    """FreeProxyList（HTML 表格，每页 ~30 条，含 HTTPS 支持）"""
    name = "free-proxy-list-net"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://free-proxy-list.net/"
        html = await self._get_html(url)
        items: List[ProxyItem] = []
        if not html:
            logger.info(f"[{self.name}] 采集到 0 条")
            return items
        # 第 7 列是 https 支持（yes/no），特殊处理
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for tr in soup.select("table tbody tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                ip = tds[0].get_text(strip=True)
                port = tds[1].get_text(strip=True)
                https = tds[6].get_text(strip=True).lower() == "yes"
                proto = "https" if https else "http"
                if ip and port.isdigit():
                    items.append(self.make_item(ip, port, proto))
        except Exception as e:
            logger.warning(f"[{self.name}] 解析失败: {e}")
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# GitHub 仓库代理源（量大、稳定、每天自动更新）
# ============================================================
class GitHubProxyListCrawler(BaseCrawler):
    """
    GitHub 通用代理列表（多种仓库，文本格式）
    通过 raw.githubusercontent.com 直读

    内置 7 个高产 GitHub raw 仓库（每个都是每天自动更新）
    单次跑下来通常能采到 3w+ 行，去重后 1w+
    """
    name: str = ""  # 子类必须设置

    # GitHub raw 代理仓库列表（每个都很大）
    # 验证时间 2026-09-06；单次采到条数标注在后面
    GITHUB_URLS = [
        # name, url, protocol
        ("http", "https://raw.githubusercontent.com/proxy4parsing/proxy-list/main/http.txt"),  # ~19k
        ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),    # ~3k
        ("http", "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt"),  # ~2k
        ("http", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/all.txt"),    # ~1.4k
        ("https","https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt"),         # ~1k
        ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),# ~400
        ("http", "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt"),  # ~500
        ("https","https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt"),# ~45
        # 小批量源（互为备份）
        ("http", "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt"),     # ~40
        ("https","https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt"),    # ~13
        # 注：socks4/socks5 源暂时移除
        # - httpx 0.27 不支持 socks 协议代理（httpx-socks 需要额外的三方库）
        # - 采集后会被 ProxyProtocol 兜底成 http，落池后被 L1 TCP 验证剔除，浪费资源
        # - 若未来要支持，可改用 httpx-socks 库并在 validator 里加 socks 协议分支
    ]

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for proto, url in self.GITHUB_URLS:
            text = await self._get_html(url)
            if not text:
                continue
            n_before = len(items)
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, proto))
            logger.debug(
                f"[{self.name}] {url.split('/')[-2]} +{len(items)-n_before}"
            )
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class SpeedXProxyListCrawler(GitHubProxyListCrawler):
    """GitHub 多仓库聚合（11 个 raw 源，单次 3w+ 条）"""
    name = "github-proxylist-http"


# ============================================================
# 🆕 新一代公开 API 源（2026-09 验证可用）
# ============================================================
class DatabayApiCrawler(BaseCrawler):
    """Databay 公开 TXT 接口（~6300 条/次，混合协议）"""
    name = "databay-api"

    URLS = [
        ("http", "https://databay.com/free-proxy-list.txt"),
    ]

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for proto, url in self.URLS:
            text = await self._get_html(url)
            if not text:
                continue
            # Databay 含 # 注释行（更新频率/版权），需要过滤
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for ip, port in self.extract_ip_port(line):
                    items.append(self.make_item(ip, port, proto))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class ProxyScrapeV4Crawler(BaseCrawler):
    """ProxyScrape v4 API（json-compatible，~650 条 http + 50 https）"""
    name = "proxyscrape-v4"

    URLS = [
        ("http",  "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&proxy_format=ipport&format=text"),
        ("https", "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=https&proxy_format=ipport&format=text"),
    ]

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        for proto, url in self.URLS:
            text = await self._get_html(url)
            if not text:
                continue
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, proto))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class ProxyScrapeV2HttpsCrawler(BaseCrawler):
    """ProxyScrape v2 HTTPS 专版（~840 条/次）"""
    name = "proxyscrape-v2-https"

    async def fetch(self) -> List[ProxyItem]:
        text = await self._get_html(
            "https://api.proxyscrape.com/v2/?request=displayproxies"
            "&protocol=https&timeout=5000&country=all"
        )
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "https"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class ProxMintApiCrawler(BaseCrawler):
    """ProxMint 公开 TXT 接口（~50 条/次，30 分钟更新）"""
    name = "proxmint-api"

    async def fetch(self) -> List[ProxyItem]:
        text = await self._get_html(
            "https://proxmint.com/api/free-proxies?protocol=http&format=txt"
        )
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "http"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 🆕 扩展公开 API 源（2026-09-06 验证可用）
# ============================================================
class ProxyScrapeV2AllCrawler(BaseCrawler):
    """ProxyScrape v2 all（混合协议，每行 ip:port 无协议标记 → 默认 both 兼容 http/https）"""
    name = "proxyscrape-v2-all"

    async def fetch(self) -> List[ProxyItem]:
        url = (
            "https://api.proxyscrape.com/v2/?request=displayproxies"
            "&protocol=all&timeout=5000&country=all"
        )
        text = await self._get_html(url)
        items: List[ProxyItem] = []
        if text:
            # protocol=all 返回混合协议行，默认 both 让 HTTP/HTTPS 验证都能走
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "both"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class ProxyScrapeV1Crawler(BaseCrawler):
    """ProxyScrape v1（兼容旧版，~50ms 响应，部分有用）"""
    name = "proxyscrape-v1"

    async def fetch(self) -> List[ProxyItem]:
        url = (
            "https://api.proxyscrape.com/?request=displayproxies"
            "&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all"
        )
        text = await self._get_html(url)
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "http"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


class MuRongPIGHttpCrawler(BaseCrawler):
    """MuRongPIG http（GitHub 仓库，量极大，靠打分系统筛）"""
    name = "murongpig-http"

    async def fetch(self) -> List[ProxyItem]:
        url = "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt"
        text = await self._get_html(url)
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, "http"))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 内置爬虫注册表（共 22 个源：10 表格 + 8 API + 1 HTML 海外 + 1 聚合 + 2 备用 + 1 mixed）
# ============================================================
# 加新源：在末尾追加即可
BUILTIN_CRAWLERS: List[BaseCrawler] = [
    # 老源（10 个：HTML 表格 + 文本 API）
    KuaiDaiLiFreeCrawler(),
    KuaiDaiLiInhaCrawler(),
    IP66FreeCrawler(),
    IP89FreeCrawler(),
    IPHaiFreeCrawler(),
    ProxyDailyCrawler(),
    QiYunIpFreeCrawler(),
    KuaiDaiLiHttpsCrawler(),     # 快代理 HTTPS
    ZhandayeCrawler(),            # 站大爷
    XiaohuanProxyCrawler(),       # 小幻
    # 海外/API 源（高量）
    ProxyScrapeApiCrawler(),      # ProxyScrape v2 HTTP
    OpenProxyListCrawler(),       # OpenProxyList
    ProxyListDownloadCrawler(),   # Proxy-List.download
    FreeProxyListCrawler(),       # FreeProxyList
    # 🆕 新一代公开 API 源（2026-09 验证）
    DatabayApiCrawler(),          # Databay 公开 TXT（~6k/次）🔥
    ProxyScrapeV4Crawler(),       # ProxyScrape v4（http+https）
    ProxyScrapeV2HttpsCrawler(),  # ProxyScrape v2 HTTPS（~840/次）
    ProxMintApiCrawler(),         # ProxMint 备用
    # 🆕 2026-09-06 追加
    ProxyScrapeV2AllCrawler(),    # ProxyScrape v2 all（混合协议）
    ProxyScrapeV1Crawler(),       # ProxyScrape v1 兼容版
    MuRongPIGHttpCrawler(),       # MuRongPIG GitHub 仓库（量极大）
    # GitHub 聚合源（11 仓库聚合，单次 3w+）
    SpeedXProxyListCrawler(),     # GitHub 多仓库聚合
]
