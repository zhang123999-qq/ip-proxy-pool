"""
API 集成测试（不依赖真实外网）

运行：python tests/test_api.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from api import app
from pool import pool


# 全局单例 TestClient（避免 lifespan 反复启停）
_client = None


def get_client() -> TestClient:
    global _client
    if _client is None:
        _client = TestClient(app)
    return _client


def setup_module(_):
    pool._pool.clear()


def teardown_module(_):
    pool._pool.clear()


def test_health():
    r = get_client().get("/health")
    assert r.status_code == 200
    assert r.json()["code"] == 0
    print("✓ test_health")


def test_count_empty():
    r = get_client().get("/proxy/count")
    assert r.json()["data"]["size"] == 0
    print("✓ test_count_empty")


def test_random_empty():
    r = get_client().get("/proxy/random")
    assert r.json()["code"] == 1
    assert r.json()["data"] is None
    print("✓ test_random_empty")


def test_feedback_404():
    r = get_client().post(
        "/proxy/feedback",
        json={"ip": "1.2.3.4", "port": 8080, "success": True},
    )
    assert r.status_code == 404
    print("✓ test_feedback_404")


def test_feedback_invalid_ip():
    r = get_client().post(
        "/proxy/feedback",
        json={"ip": "not-ip", "port": 8080, "success": True},
    )
    assert r.status_code == 422  # pydantic 校验
    print("✓ test_feedback_invalid_ip")


def test_remove_404():
    r = get_client().post("/proxy/remove", json={"ip": "1.2.3.4", "port": 8080})
    assert r.status_code == 404
    print("✓ test_remove_404")


def test_stats():
    r = get_client().get("/proxy/stats")
    d = r.json()["data"]
    assert "size" in d
    assert "avg_score" in d
    print("✓ test_stats")


def test_top_n():
    r = get_client().get("/proxy/top?n=5")
    assert r.status_code == 200
    print("✓ test_top_n")


def test_batch():
    r = get_client().post(
        "/proxy/batch", json={"n": 3, "min_score": 0}
    )
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 0  # 池空
    print("✓ test_batch")


# ============================================================
# 回归测试（2026-09-06 P0+P1 API 格式优化）
# 防 P0：ProxyItemResponse 丢字段 / HTTPException 双轨制
# 防 P1：/top 缺 count / /usage 裸数组 / remove+clear data=None
# ============================================================
import pytest
from models import ProxyItem


async def _add_one(ip="10.0.0.1", port=8080, score=12):
    """往池里加一个代理（异步 setup helper）"""
    await pool.add(ProxyItem(
        ip=ip, port=port, score=score,
        source="test-source",
    ))


def test_proxy_item_response_includes_all_11_fields():
    """P0 回归：ProxyItemResponse 必须保留 11 字段（含 source / last_used_for_crawl）"""
    item = ProxyItem(
        ip="8.8.8.8", port=53, score=15,
        source="google-dns",
        last_used_for_crawl=1234567890.5,
    )
    from api.schemas import ProxyItemResponse
    resp = ProxyItemResponse(**item.to_dict())
    d = resp.model_dump()
    assert "source" in d, "source 字段丢失"
    assert "last_used_for_crawl" in d, "last_used_for_crawl 字段丢失"
    assert d["source"] == "google-dns"
    assert d["last_used_for_crawl"] == 1234567890.5
    assert len(d) == 11, f"应返回 11 字段，实际 {len(d)}"
    print("✓ test_proxy_item_response_includes_all_11_fields (P0 丢字段修复)")


def test_top_includes_count():
    """P1 回归：/proxy/top 必须返回 {count, items[]}（与 /all /batch 一致）"""
    pool._pool.clear()
    r = get_client().get("/proxy/top?n=5")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "count" in data, "/proxy/top 必须包含 count 字段"
    assert "items" in data
    assert isinstance(data["count"], int)
    print("✓ test_top_includes_count (P1 形状统一)")


def test_usage_returns_object_not_array():
    """P1 回归：/proxy/usage 必须返回 {count, items[]} 而不是裸数组"""
    r = get_client().get("/proxy/usage")
    assert r.status_code == 200
    data = r.json()["data"]
    # 必须是 dict（带 count/items），不能是 list
    assert isinstance(data, dict), f"/proxy/usage 应返回 dict，实际 {type(data).__name__}"
    assert "count" in data
    assert "items" in data
    assert isinstance(data["items"], list)
    print("✓ test_usage_returns_object_not_array (P1 形状统一)")


def test_feedback_404_wrapped_to_business_format():
    """P0 回归：HTTPException 必须被包成 {code: 1, msg, data: null}，HTTP 404 保留"""
    pool._pool.clear()
    r = get_client().post(
        "/proxy/feedback",
        json={"ip": "9.9.9.9", "port": 9999, "success": True},
    )
    assert r.status_code == 404, "HTTP 状态码保留 404"
    body = r.json()
    assert body["code"] == 1, f"业务 code 应为 1，实际 {body['code']}"
    assert "msg" in body
    assert body["data"] is None
    assert "9.9.9.9" not in body["msg"], "msg 不应泄漏具体 IP（脱敏）"
    print("✓ test_feedback_404_wrapped_to_business_format (P0 双轨制修复)")


def test_remove_404_wrapped_to_business_format():
    """P0 回归：/proxy/remove 404 也走业务包格式"""
    r = get_client().post(
        "/proxy/remove", json={"ip": "9.9.9.9", "port": 9999}
    )
    assert r.status_code == 404
    body = r.json()
    assert body["code"] == 1
    assert body["data"] is None
    print("✓ test_remove_404_wrapped_to_business_format (P0 双轨制修复)")


def test_validation_error_wrapped_to_business_format():
    """P0 回归：Pydantic 422 校验错误也走业务包格式"""
    r = get_client().post(
        "/proxy/feedback",
        json={"ip": "not-ip", "port": 8080, "success": True},
    )
    assert r.status_code == 422, "HTTP 状态码保留 422"
    body = r.json()
    assert body["code"] == 1, "422 也走业务 code=1"
    assert "参数校验失败" in body["msg"], f"msg 应说明参数校验失败，实际 {body['msg']}"
    assert body["data"] is None
    print("✓ test_validation_error_wrapped_to_business_format (P0 双轨制修复)")


def test_remove_success_returns_data_not_null():
    """P1 回归：/proxy/remove 成功时 data 不为 null，包含被删代理信息"""
    import asyncio
    asyncio.run(_add_one(ip="1.2.3.4", port=8080))

    r = get_client().post(
        "/proxy/remove", json={"ip": "1.2.3.4", "port": 8080}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0
    assert isinstance(body["data"], dict), "data 应是 dict，不是 null"
    assert body["data"]["ip"] == "1.2.3.4"
    assert body["data"]["port"] == 8080
    pool._pool.clear()
    print("✓ test_remove_success_returns_data_not_null (P1 null 修复)")


def test_proxy_random_serializes_all_fields():
    """P0 回归：实际走 /proxy/random 时，11 字段必须全部返回（端到端验证）"""
    import asyncio
    asyncio.run(_add_one(
        ip="5.6.7.8", port=3128, score=18,
    ))
    # 给代理设置 source（_add_one 默认 source="test-source"）
    r = get_client().get("/proxy/random")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data is not None
    assert "source" in data, "/proxy/random 响应缺 source 字段（仍被 Pydantic 丢）"
    assert "last_used_for_crawl" in data
    assert data["ip"] == "5.6.7.8"
    pool._pool.clear()
    print("✓ test_proxy_random_serializes_all_fields (端到端 P0 验证)")


if __name__ == "__main__":
    setup_module(None)
    try:
        test_health()
        test_count_empty()
        test_random_empty()
        test_feedback_404()
        test_feedback_invalid_ip()
        test_remove_404()
        test_stats()
        test_top_n()
        test_batch()
        # 2026-09-06 新增
        test_proxy_item_response_includes_all_11_fields()
        test_top_includes_count()
        test_usage_returns_object_not_array()
        test_feedback_404_wrapped_to_business_format()
        test_remove_404_wrapped_to_business_format()
        test_validation_error_wrapped_to_business_format()
        test_remove_success_returns_data_not_null()
        test_proxy_random_serializes_all_fields()
        print("\n=== API 测试全部通过（含 8 个新增回归） ===")
    finally:
        teardown_module(None)
