"""
FastAPI 路由模块
================

本文件定义了所有的 HTTP 接口。
启动后访问 http://127.0.0.1:8000/docs 可以看到自动生成的 Swagger 文档。

接口列表：
- GET  /health               健康检查
- GET  /proxy/count          数量
- GET  /proxy/stats          统计
- GET  /proxy/random         随机获取一个（加权）
- GET  /proxy/all            列表（分页）
- GET  /proxy/top            分数最高 N 个
- POST /proxy/batch          批量获取
- POST /proxy/feedback       反馈使用结果（影响分数）
- POST /proxy/remove         主动删除
- POST /proxy/refresh        手动触发采集+验证
- GET  /proxy/crawl-mode     当前爬源模式 + 决策依据
- GET  /proxy/usage          代理爬源使用情况
- GET  /proxy/last-crawl     上次爬取结果详情
- POST /admin/save           [需 ADMIN_TOKEN] 手动刷盘
- POST /admin/clear          [需 ADMIN_TOKEN] 清空代理池
- POST /admin/sources        [需 ADMIN_TOKEN] 新增自定义代理源
- GET  /admin/sources        [需 ADMIN_TOKEN] 查看自定义源
- DELETE /admin/sources/{name}  [需 ADMIN_TOKEN] 删除源
- POST /admin/sources/{name}/toggle [需 ADMIN_TOKEN] 启停源

阅读建议：
- 先看 import 部分，了解依赖
- 再按从上到下顺序看每个路由
- 重点看 query/path/body 参数的写法
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query

# 从其他模块导入
# pool：代理池单例
# validator：验证器
# get_default_manager：默认爬虫管理器
# schemas：请求/响应的数据模型
from pool import pool
from validator import validator
from crawler import get_default_manager
from config import ADMIN_TOKEN
import asyncio
from .schemas import (
    FeedbackRequest,
    RemoveRequest,
    BatchGetRequest,
    ProxyItemResponse,
    CommonResponse,
    SourceCreateRequest,
    SourceToggleRequest,
)
from storage.dao import CustomSource, custom_source_dao
from crawler import build_crawler_from_source
from utils import logger

# APIRouter 是 FastAPI 的「子路由」概念
# 所有用 @router.xxx 装饰的函数都会被注册到主 app
router = APIRouter()


# ============================================================
# 管理接口鉴权依赖
# ============================================================
async def require_admin_token(
    authorization: Optional[str] = Header(None),
) -> None:
    """
    管理接口的鉴权依赖：
    - ADMIN_TOKEN 为空 → 关闭鉴权（开发模式）
    - 否则要求 Header `Authorization: Bearer <token>`
    """
    if not ADMIN_TOKEN:
        # 未配置 token，鉴权关闭
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="缺少 Authorization: Bearer <token>",
        )
    token = authorization[len("Bearer "):].strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="token 无效")


# ============================================================
# 随机获取
# ============================================================
@router.get(
    "/proxy/random",
    response_model=CommonResponse,
    summary="随机获取一个代理（按分数加权）",
)
async def random_one(
    protocol: Optional[str] = Query(
        None,
        description="协议过滤: http / https / both"
    ),
    min_score: int = Query(
        1, ge=0, le=20,
        description="最低分数门槛（避免拿到刚入池的代理）"
    ),
    verify: bool = Query(
        False,
        description="是否先 TCP 预筛再返回（保证可用，1~2s 延迟）"
    ),
    verify_timeout: float = Query(
        1.5, ge=0.1, le=5.0,
        description="verify 模式的单次验证超时（秒）"
    ),
) -> CommonResponse:
    """
    随机获取一个代理

    模式：
    - 默认（verify=false）：加权随机，~0ms，可能拿到刚入库的
    - 严格（verify=true）：先 TCP 预筛再返回，~1~2s 保证可用
    - 高分（min_score=15）：只取历史上多次通过的高分代理，~0ms

    示例：
        GET /proxy/random
        GET /proxy/random?protocol=https
        GET /proxy/random?min_score=10
        GET /proxy/random?verify=true
        GET /proxy/random?verify=true&verify_timeout=2.0
    """
    if verify:
        # 严格模式：先 TCP 预筛再返回（最多试 3 次）
        item = await _random_one_with_verify(
            protocol=protocol,
            min_score=min_score,
            timeout=verify_timeout,
        )
    else:
        # 快模式：直接加权随机
        item = pool.random_one(protocol=protocol, min_score=min_score)

    if item is None:
        return CommonResponse(
            code=1, msg="代理池为空或无符合条件代理", data=None
        )
    return CommonResponse(
        code=0,
        msg="ok" if not verify else "ok (verified)",
        data=ProxyItemResponse(**item.to_dict()).model_dump(),
    )


async def _random_one_with_verify(
    protocol: Optional[str],
    min_score: int,
    timeout: float,
    max_tries: int = 3,
):
    """
    严格模式：先验证再返回

    流程：
    1. 拿一个代理（加权随机）
    2. 走 L1 TCP 快速预筛
    3. 通过就返回，没过就拿下一个
    4. 最多试 max_tries 次

    关键：不调用 update_score（不扣分）
    这是"探测性"验证，不影响打分系统
    """
    for attempt in range(max_tries):
        item = pool.random_one(protocol=protocol, min_score=min_score)
        if item is None:
            return None
        try:
            # TCP 探测（不扣分，只看是否真的连得通）
            ok = await asyncio.wait_for(
                validator._tcp_probe(item),
                timeout=timeout,
            )
            if ok:
                logger.debug(
                    f"verify 模式：{item.key} 通过 L1 TCP 预筛"
                )
                return item
            logger.debug(
                f"verify 模式：{item.key} L1 TCP 失败 "
                f"(try {attempt + 1}/{max_tries})"
            )
        except asyncio.TimeoutError:
            logger.debug(
                f"verify 模式：{item.key} 验证超时 "
                f"(try {attempt + 1}/{max_tries})"
            )
            continue
        except Exception as e:
            logger.debug(
                f"verify 模式：{item.key} 异常: {e}"
            )
            continue
    return None


@router.post(
    "/proxy/batch",
    response_model=CommonResponse,
    summary="批量获取多个不重复代理",
)
async def batch_get(req: BatchGetRequest) -> CommonResponse:
    """
    批量获取 N 个不重复的代理

    请求体：
        {"n": 5, "protocol": "http", "min_score": 1, "verify": false}
        {"n": 3, "verify": true, "verify_timeout": 1.5}  ← 严格模式
    """
    if req.verify:
        items = await _batch_get_with_verify(
            n=req.n,
            protocol=req.protocol,
            min_score=req.min_score,
            timeout=req.verify_timeout,
        )
    else:
        items = pool.random_n(
            n=req.n, protocol=req.protocol, min_score=req.min_score
        )
    return CommonResponse(
        code=0,
        msg="ok" if not req.verify else "ok (verified)",
        data={
            "count": len(items),
            "items": [
                ProxyItemResponse(**it.to_dict()).model_dump()
                for it in items
            ],
        },
    )


async def _batch_get_with_verify(
    n: int,
    protocol: Optional[str],
    min_score: int,
    timeout: float,
) -> list:
    """
    严格模式批量获取

    最多试 3n+5 次（避免死循环），找到 n 个为止
    """
    result: list = []
    tried_keys: set = set()
    max_total_tries = n * 3 + 5
    for _ in range(max_total_tries):
        if len(result) >= n:
            break
        item = pool.random_one(protocol=protocol, min_score=min_score)
        if item is None or item.key in tried_keys:
            continue
        tried_keys.add(item.key)
        try:
            ok = await asyncio.wait_for(
                validator._tcp_probe(item),
                timeout=timeout,
            )
            if ok:
                result.append(item)
        except (asyncio.TimeoutError, Exception):
            continue
    return result


# ============================================================
# 列表
# ============================================================
@router.get(
    "/proxy/all",
    response_model=CommonResponse,
    summary="分页获取所有代理",
)
async def all_proxies(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    protocol: Optional[str] = Query(None, description="协议过滤"),
) -> CommonResponse:
    """
    分页获取所有代理（按分数倒序）
    """
    items = [
        it for it in pool.all()
        if protocol is None or it.protocol.value == protocol
    ]
    items.sort(key=lambda x: x.score, reverse=True)
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]
    return CommonResponse(
        code=0,
        msg="ok",
        data={
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [
                ProxyItemResponse(**it.to_dict()).model_dump()
                for it in page_items
            ],
        },
    )


@router.get(
    "/proxy/top",
    response_model=CommonResponse,
    summary="获取分数最高的 N 个代理",
)
async def top(n: int = Query(10, ge=1, le=100)) -> CommonResponse:
    """获取分数最高的 N 个代理（监控/展示用）"""
    items = pool.top_n(n)
    return CommonResponse(
        code=0,
        msg="ok",
        data={
            "count": len(items),
            "items": [
                ProxyItemResponse(**it.to_dict()).model_dump()
                for it in items
            ],
        },
    )


# ============================================================
# 健康检查 / 统计
# ============================================================
@router.get("/health", response_model=CommonResponse, summary="健康检查")
async def health() -> CommonResponse:
    """返回服务是否可用 + 当前代理数"""
    return CommonResponse(
        code=0, msg="ok", data={"size": pool.size()}
    )


@router.get(
    "/proxy/stats", response_model=CommonResponse, summary="代理池统计"
)
async def stats() -> CommonResponse:
    """
    池统计信息
    - size: 数量
    - avg_score / max_score / min_score
    - http / https / both 协议分布
    """
    return CommonResponse(code=0, msg="ok", data=pool.stats())


@router.get(
    "/proxy/count", response_model=CommonResponse, summary="当前代理数量"
)
async def count() -> CommonResponse:
    return CommonResponse(code=0, msg="ok", data={"size": pool.size()})


# ============================================================
# 反馈 / 删除
# ============================================================
@router.post(
    "/proxy/feedback",
    response_model=CommonResponse,
    summary="反馈代理使用结果，更新分数",
)
async def feedback(req: FeedbackRequest) -> CommonResponse:
    """
    业务使用代理后调用这个接口反馈结果
    - success=true  → 该代理 +1 分
    - success=false → 该代理 -3 分（<=0 自动删除）

    请求示例：
        POST /proxy/feedback
        {"ip": "1.2.3.4", "port": 8080, "success": true}
    """
    if not pool.contains(req.ip, req.port):
        raise HTTPException(status_code=404, detail="代理不在池中")
    ok, new_score, msg = await pool.update_score(
        req.ip, req.port, req.success
    )
    logger.info(f"feedback: {msg}")
    return CommonResponse(
        code=0 if ok else 1,
        msg=msg,
        data={
            "ip": req.ip,
            "port": req.port,
            "new_score": new_score,
            "success": req.success,
        },
    )


@router.post(
    "/proxy/remove",
    response_model=CommonResponse,
    summary="主动从池中删除代理",
)
async def remove(req: RemoveRequest) -> CommonResponse:
    """主动剔除某些已知不可用的代理"""
    ok = await pool.remove(req.ip, req.port)
    if not ok:
        raise HTTPException(status_code=404, detail="代理不在池中")
    return CommonResponse(
        code=0,
        msg=f"已删除 {req.ip}:{req.port}",
        data={"ip": req.ip, "port": req.port},
    )


# ============================================================
# 维护接口
# ============================================================
@router.post(
    "/proxy/refresh",
    response_model=CommonResponse,
    summary="手动触发一次采集 + 验证",
)
async def refresh(mode: str = "auto") -> CommonResponse:
    """
    手动触发一次完整的采集 + 验证流程
    mode 可选：auto / direct / proxy / hybrid / skip
    """
    manager = get_default_manager()
    try:
        # 1. 采集
        result = await manager.run_all(mode=mode)
        added = 0
        for it in result.items:
            if await pool.add(it):
                added += 1
        # 2. 验证新入池的
        if added > 0:
            new_items = [
                it for it in result.items if pool.contains(it.ip, it.port)
            ]
            await validator.validate(new_items)
        return CommonResponse(
            code=0, msg="ok",
            data={
                "mode": result.mode,
                "fetched": len(result.items),
                "added": added,
                "size": pool.size(),
                "proxy_used": result.proxy_used,
            },
        )
    except Exception as e:
        logger.exception(f"refresh 异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 智能爬源相关查询接口
# ============================================================
@router.get(
    "/proxy/crawl-mode",
    response_model=CommonResponse,
    summary="当前爬源模式 + 决策依据",
)
async def crawl_mode() -> CommonResponse:
    """
    返回当前爬源模式信息：
    - 模式决策（如果现在跑会用哪个模式）
    - 池中可作爬源通道的代理数
    - 上次爬取结果
    - 配置阈值
    """
    from config import (
        PROXY_MIN_SCORE_FOR_CRAWL,
        PROXY_MIN_COUNT_FOR_CRAWL,
        PROXY_USE_COOLDOWN_SEC,
        CRAWL_MODE,
    )
    manager = get_default_manager()
    decided = manager.decide_mode("auto")
    candidates = pool.get_crawl_candidates(limit=100)
    return CommonResponse(
        code=0, msg="ok",
        data={
            "configured_mode": CRAWL_MODE,
            "decided_mode": decided.value,
            "crawl_candidates": len(candidates),
            "thresholds": {
                "min_score": PROXY_MIN_SCORE_FOR_CRAWL,
                "min_count": PROXY_MIN_COUNT_FOR_CRAWL,
                "cooldown_sec": PROXY_USE_COOLDOWN_SEC,
            },
            "last_result": (
                manager.last_result.to_dict()
                if manager.last_result else None
            ),
        },
    )


@router.get(
    "/proxy/usage",
    response_model=CommonResponse,
    summary="代理爬源使用情况（冷却追踪）",
)
async def usage() -> CommonResponse:
    """
    返回所有曾经用于爬源的代理 + 冷却剩余时间

    data 形状统一为 {count, items[]}，与 /proxy/top / /proxy/all 保持一致
    """
    items = pool.get_usage()
    return CommonResponse(
        code=0,
        msg="ok",
        data={
            "count": len(items),
            "items": items,
        },
    )


@router.get(
    "/proxy/last-crawl",
    response_model=CommonResponse,
    summary="上次爬取结果详情",
)
async def last_crawl() -> CommonResponse:
    """返回最近一次爬取的完整结果"""
    manager = get_default_manager()
    if manager.last_result is None:
        return CommonResponse(code=1, msg="暂无爬取记录", data=None)
    return CommonResponse(
        code=0, msg="ok", data=manager.last_result.to_dict()
    )


@router.post(
    "/admin/save",
    response_model=CommonResponse,
    summary="手动触发持久化（SQLite）",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def save_now() -> CommonResponse:
    """手动把当前池刷盘到 SQLite（需要 ADMIN_TOKEN）"""
    n = await pool.save()
    return CommonResponse(code=0, msg="ok", data={"saved": n})


@router.post(
    "/admin/clear",
    response_model=CommonResponse,
    summary="清空代理池（危险，仅调试用）",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def clear() -> CommonResponse:
    """清空代理池（仅调试用，需要 ADMIN_TOKEN）"""
    await pool.clear()
    return CommonResponse(
        code=0,
        msg="cleared",
        data={"cleared": True},
    )


# ============================================================
# 自定义代理源管理（动态添加代理平台入口）
# ============================================================
@router.post(
    "/admin/sources",
    response_model=CommonResponse,
    summary="新增自定义代理源（无需改代码）",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def create_source(req: SourceCreateRequest) -> CommonResponse:
    """
    新增一个代理源，零代码即时生效。
    服务重启后依旧保留。

    需要 `ADMIN_TOKEN`。
    """
    try:
        src = custom_source_dao.create(
            CustomSource(
                name=req.name,
                type_=req.type,
                url=req.url,
                proto=req.proto,
                config=req.config or {},
                enabled=req.enabled,
            )
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 预校验：能不能转成爬虫（提前暴露错误）
    try:
        crawler = build_crawler_from_source(src)
    except Exception as e:
        # 失败回滚
        custom_source_dao.delete(src.name)
        raise HTTPException(status_code=400, detail=f"源配置不合法: {e}")

    # 热更新 manager
    manager = get_default_manager()
    n = manager.reload_crawlers()

    return CommonResponse(
        code=0, msg=f"已添加源 [{req.name}]",
        data={
            **src.to_dict(),
            "preview_name": crawler.name,
            "active_sources_total": n,
        },
    )


@router.get(
    "/admin/sources",
    response_model=CommonResponse,
    summary="查看所有自定义代理源",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def list_sources() -> CommonResponse:
    """
    列出 DB 中全部自定义源（含 disabled）。
    """
    items = custom_source_dao.list_all(enabled_only=False)
    return CommonResponse(
        code=0, msg="ok",
        data={
            "count": len(items),
            "items": [it.to_dict() for it in items],
        },
    )


@router.delete(
    "/admin/sources/{name}",
    response_model=CommonResponse,
    summary="删除自定义代理源（按 name）",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def delete_source(name: str) -> CommonResponse:
    """按 name 删除一个自定义源（内置源不可删）"""
    ok = custom_source_dao.delete(name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"源 [{name}] 不存在")
    # 热更新 manager
    manager = get_default_manager()
    n = manager.reload_crawlers()
    return CommonResponse(
        code=0, msg=f"已删除源 [{name}]",
        data={"active_sources_total": n},
    )


@router.post(
    "/admin/sources/{name}/toggle",
    response_model=CommonResponse,
    summary="启停自定义代理源",
    include_in_schema=False,
    dependencies=[Depends(require_admin_token)],
)
async def toggle_source(name: str, req: SourceToggleRequest) -> CommonResponse:
    """启停某个源（不会真删，可再开）"""
    ok = custom_source_dao.set_enabled(name, req.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail=f"源 [{name}] 不存在")
    manager = get_default_manager()
    n = manager.reload_crawlers()
    return CommonResponse(
        code=0, msg="ok",
        data={"name": name, "enabled": req.enabled, "active_sources_total": n},
    )
