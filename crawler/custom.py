"""
自定义代理源工厂
=================

用户通过 /admin/sources 动态添加的源 → 在这里被翻译成可执行的爬虫实例。

支持 3 种 type：
- text  纯文本，每行一条 `ip:port`（适用 GitHub raw .txt、纯文本列表）
- table HTML 表格（适用大部分免费代理网站）
- api   通用文本/JSON API（适用 proxyscrape、proxy-list.download 这类）

每个源都是 BaseCrawler 的子类——所以自动继承：
- 限速（令牌桶）
- 单源随机延时
- 真实请求头（UA / Referer / Accept-Language）
- 失败熔断 + 指数退避
- 自动重试 3 次
"""
from typing import List

from .base import BaseCrawler
from models import ProxyItem
from storage.dao import CustomSource
from utils import logger


# ============================================================
# type=text：纯文本格式
# ============================================================
class CustomTextCrawler(BaseCrawler):
    """
    纯文本源，每行一条 ip:port。

    CustomSource 字段：
        name        唯一名
        url         文本 URL
        proto       默认协议（http/https）
        config      {
            "headers": {...},       # 可选，自定义请求头
            "encoding": "utf-8",    # 可选，文本编码
        }
    """
    name: str = ""

    def __init__(self, source: CustomSource):
        super().__init__()
        self.name = source.name
        self.proto = source.proto
        self.url = source.url
        self.cfg = source.config or {}

    async def fetch(self) -> List[ProxyItem]:
        # 自定义 headers（可选）
        extra = {}
        if self.cfg.get("headers"):
            extra["headers"] = self.cfg["headers"]

        text = await self._get_html(self.url, **extra)
        items: List[ProxyItem] = []
        if text:
            for ip, port in self.extract_ip_port(text):
                items.append(self.make_item(ip, port, self.proto))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# type=table：HTML 表格
# ============================================================
class CustomTableCrawler(BaseCrawler):
    """
    表格源，自动解析 <table><tbody><tr><td>。

    CustomSource.config 支持：
        ip_col      int  IP 列下标（默认 0）
        port_col    int  端口列下标（默认 1）
        proto_col   int  协议列下标（默认 -1，用 source.proto）
        pagination  dict {param: "page", start: 1, end: 3}
                         指定则按 {url}?{param}=N 翻页
        headers     dict 自定义请求头
    """
    name: str = ""

    def __init__(self, source: CustomSource):
        super().__init__()
        self.name = source.name
        self.url = source.url
        self.proto = source.proto
        self.cfg = source.config or {}

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        ip_col = int(self.cfg.get("ip_col", 0))
        port_col = int(self.cfg.get("port_col", 1))
        proto_col = int(self.cfg.get("proto_col", -1))
        extra = {}
        if self.cfg.get("headers"):
            extra["headers"] = self.cfg["headers"]

        pagination = self.cfg.get("pagination")
        if pagination and isinstance(pagination, dict):
            # 翻页模式：?page=N
            param = pagination.get("param", "page")
            start = int(pagination.get("start", 1))
            end = int(pagination.get("end", 3))
            sep = "&" if "?" in self.url else "?"
            for p in range(start, end + 1):
                url = f"{self.url}{sep}{param}={p}"
                html = await self._get_html(url, **extra)
                if not html:
                    continue
                items.extend(
                    self.parse_table_or_text(
                        html, proto=self.proto,
                        ip_col=ip_col, port_col=port_col, proto_col=proto_col,
                    )
                )
        else:
            html = await self._get_html(self.url, **extra)
            if html:
                items = self.parse_table_or_text(
                    html, proto=self.proto,
                    ip_col=ip_col, port_col=port_col, proto_col=proto_col,
                )
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# type=api：通用文本/JSON API
# ============================================================
class CustomApiCrawler(BaseCrawler):
    """
    通用 API 源，比如 ProxyScrape / OpenProxyList。

    CustomSource.config 支持：
        sub_urls     list[{"url": "...", "proto": "http"}]
                     指定多个 URL，proto 各自不同
                     不指定就用 source.url + source.proto
        json         bool  是否 JSON 响应（默认 false，按文本解析）
        headers      dict  自定义请求头
    """
    name: str = ""

    def __init__(self, source: CustomSource):
        super().__init__()
        self.name = source.name
        self.proto = source.proto
        self.url = source.url
        self.cfg = source.config or {}

    async def fetch(self) -> List[ProxyItem]:
        items: List[ProxyItem] = []
        extra = {}
        if self.cfg.get("headers"):
            extra["headers"] = self.cfg["headers"]
        is_json = bool(self.cfg.get("json", False))

        sub_urls = self.cfg.get("sub_urls")
        if isinstance(sub_urls, list) and sub_urls:
            targets = [(it.get("proto", self.proto), it["url"])
                       for it in sub_urls if it.get("url")]
        else:
            targets = [(self.proto, self.url)]

        for proto, url in targets:
            if is_json:
                data = await self._get_json(url, **extra)
                if not data:
                    continue
                # JSON 格式容错：list[dict] 或 dict 套 list
                try:
                    arr = data if isinstance(data, list) else \
                        data.get("data") or data.get("proxies") or []
                    if isinstance(arr, dict):
                        arr = [arr]
                    for row in arr:
                        ip = row.get("ip") or row.get("IP") or row.get("address")
                        port = row.get("port") or row.get("Port") or row.get("p")
                        if ip and str(port).isdigit():
                            items.append(self.make_item(ip, port, proto))
                except Exception as e:
                    logger.warning(f"[{self.name}] JSON 解析失败: {e}")
            else:
                text = await self._get_html(url, **extra)
                if not text:
                    continue
                for ip, port in self.extract_ip_port(text):
                    items.append(self.make_item(ip, port, proto))
        logger.info(f"[{self.name}] 采集到 {len(items)} 条")
        return items


# ============================================================
# 工厂
# ============================================================
def build_crawler_from_source(source: CustomSource) -> BaseCrawler:
    """
    把 CustomSource 转成 BaseCrawler 实例

    :raises ValueError: type 不支持
    """
    t = source.type.lower().strip()
    if t == "text":
        return CustomTextCrawler(source)
    if t == "table":
        return CustomTableCrawler(source)
    if t == "api":
        return CustomApiCrawler(source)
    raise ValueError(f"不支持的源类型: {source.type}")


def build_all_custom_crawlers() -> List[BaseCrawler]:
    """
    从 DB 加载所有 enabled 的自定义源 → 转成爬虫列表

    类型校验失败 / 加载失败的源会被跳过（在日志记录原因）。
    通常被 manager 的初始化或热重载调用。
    """
    from storage.dao import custom_source_dao

    out: List[BaseCrawler] = []
    for src in custom_source_dao.list_all(enabled_only=True):
        try:
            out.append(build_crawler_from_source(src))
        except Exception as e:
            logger.warning(
                f"跳过自定义源 [{src.name}] ({src.type}): {e}"
            )
    return out
