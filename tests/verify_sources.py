"""
代理源验证脚本
===============

对候选源做实际 HTTP 请求，确认能拉到 ip:port 格式的数据。

输出：哪些源「可用」、采到多少条、采样 3 条展示。

运行：
    python tests/verify_sources.py
"""
import asyncio
import re
import sys
import time
from pathlib import Path

# 让脚本能找到项目根的包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

# IPv4 正则（与 BaseCrawler.extract_ip_port 同款）
IP_PORT = re.compile(
    r"(\d{1,3}(?:\.\d{1,3}){3})[\s:,\t]+(\d{2,5})"
)


# ============================================================
# 候选源：name / url / proto / type
# ============================================================
# type=text   URL 返回纯文本，每行 ip:port
# type=api    JSON / 文本 API
CANDIDATES = [
    # ===== GitHub raw: 已有 =====
    ("gh-speedx-http",    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "http",  "text"),
    ("gh-speedx-https",   "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/https.txt", "https", "text"),
    ("gh-shifty-http",    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt", "http",  "text"),
    ("gh-shifty-https",   "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt", "https", "text"),
    ("gh-monosans-http",  "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt", "http",  "text"),
    ("gh-roosterkid",     "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "https", "text"),
    ("gh-proxy4parsing",  "https://raw.githubusercontent.com/proxy4parsing/proxy-list/main/http.txt", "http", "text"),

    # ===== GitHub raw: 新候选 =====
    ("gh-kangproxy-http",  "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/http.txt",  "http",  "text"),
    ("gh-kangproxy-https", "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/https.txt", "https", "text"),
    ("gh-kangproxy-socks4","https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/socks4.txt", "socks4", "text"),
    ("gh-kangproxy-socks5","https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/socks5.txt", "socks5", "text"),
    ("gh-proxmint-http",   "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/http.txt",   "http",  "text"),
    ("gh-proxmint-https",  "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/https.txt",  "https", "text"),
    ("gh-proxmint-socks4", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks4.txt", "socks4", "text"),
    ("gh-proxmint-socks5", "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/socks5.txt", "socks5", "text"),
    ("gh-proxmint-all",    "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/all.txt",    "http",  "text"),
    ("gh-thordata-all",    "https://raw.githubusercontent.com/Thordata/awesome-free-proxy-list/main/proxies/all.txt", "http", "text"),
    ("gh-vakhov-http",     "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",        "http",  "text"),
    ("gh-zloi-http",       "https://raw.githubusercontent.com/zloi-user/hideip.me/main/http.txt",              "http",  "text"),
    ("gh-zloi-https",      "https://raw.githubusercontent.com/zloi-user/hideip.me/main/https.txt",             "https", "text"),
    ("gh-sunny9577",       "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt", "http", "text"),
    ("gh-hookzof-socks5",  "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",          "socks5", "text"),
    # geos
    ("gh-proxmint-us",     "https://raw.githubusercontent.com/proxmint/free-proxy-list/main/proxies/countries/US/proxies.txt", "http", "text"),

    # ===== API 文本 / JSON（无代理裸跑） =====
    ("api-proxyscrape-v2",  "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all",  "http",  "api"),
    ("api-proxyscrape-v2s", "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=https&timeout=5000&country=all", "https", "api"),
    ("api-proxyscrape-v4",  "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&proxy_format=ipport&format=text", "http", "api"),
    ("api-openproxylist",   "https://api.openproxylist.xyz/http.txt", "http", "api"),
    ("api-databay",         "https://databay.com/free-proxy-list.txt", "http", "api"),
    ("api-proxmint",        "https://proxmint.com/api/free-proxies?protocol=http&format=txt", "http", "api"),
    # JSON 类（要按 JSON 解）
    ("api-proxyscrape-json","https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies&protocol=http&proxy_format=protocolipport&format=json", "http", "api-json"),

    # ===== HTML 表格 / 列表页（已有部分）=====
    ("html-geonode",       "https://proxylist.geonode.com/api/proxy-list?limit=200&page=1&sort_by=lastChecked&sort_type=desc&protocols=http", "http", "html-geo"),
]


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _count_ip_port(text: str) -> int:
    """粗略统计有效 ip:port 条数"""
    return len(IP_PORT.findall(text or ""))


async def _check_one(client: httpx.AsyncClient, name: str, url: str,
                     proto: str, src_type: str) -> dict:
    """对一个源做实测：HTTP 请求 + 校验"""
    start = time.time()
    try:
        resp = await client.get(url, headers=HEADERS, timeout=15.0)
        if resp.status_code >= 400:
            return {
                "name": name, "url": url, "proto": proto,
                "ok": False, "n": 0, "ms": int((time.time()-start)*1000),
                "err": f"HTTP {resp.status_code}",
                "sample": "",
            }

        if "json" in src_type or "html-geo" in src_type:
            body = resp.text  # JSON 文本格式也能正则
        else:
            body = resp.text

        n = _count_ip_port(body)
        elapsed = int((time.time() - start) * 1000)
        samples = IP_PORT.findall(body)[:3]
        sample_str = " ".join(f"{ip}:{p}" for ip, p in samples)

        return {
            "name": name, "url": url, "proto": proto,
            "ok": n > 0, "n": n, "ms": elapsed,
            "err": "" if n > 0 else "0 个 ip:port（格式不匹配）",
            "sample": sample_str,
        }
    except Exception as e:
        return {
            "name": name, "url": url, "proto": proto,
            "ok": False, "n": 0, "ms": int((time.time()-start)*1000),
            "err": f"{type(e).__name__}",
            "sample": "",
        }


async def main():
    print("="*70)
    print(f"  验证 {len(CANDIDATES)} 个候选代理源（逐个 HTTP 请求）")
    print("="*70)

    async with httpx.AsyncClient(follow_redirects=True, http2=False) as c:
        tasks = [_check_one(c, *cand) for cand in CANDIDATES]
        results = await asyncio.gather(*tasks)

    # 按可用性排序
    results.sort(key=lambda r: (not r["ok"], -r["n"]))

    ok_count = sum(1 for r in results if r["ok"])
    print(f"\n✅ 可用: {ok_count} / {len(results)}\n")

    print(f"{'状态':<5}{'源名':<26}{'条数':>8}{'耗时':>8}  {'示例':<32}{'备注'}")
    print("-" * 110)
    for r in results:
        status = "✅" if r["ok"] else "❌"
        note = r["err"] if r["err"] else r["url"]
        sample = (r["sample"][:30] + "…") if len(r["sample"]) > 32 else r["sample"]
        print(
            f"{status:<5}{r['name']:<26}{r['n']:>8}{r['ms']:>7}ms  "
            f"{sample:<32}{note[:50]}"
        )

    # 输出可用清单（写入文件，方便直接复制粘贴到 custom_sources）
    out = Path(__file__).resolve().parent.parent / "data" / "verified_sources.txt"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        f.write("# Verified proxy sources\n")
        f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# {ok_count} / {len(results)} alive\n\n")
        for r in results:
            if r["ok"]:
                f.write(f"{r['name']}\t{r['url']}\t{r['proto']}\n")
    print(f"\n可用源已写入 {out}")


if __name__ == "__main__":
    asyncio.run(main())
