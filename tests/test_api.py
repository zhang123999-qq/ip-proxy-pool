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
    """P0 回归：ProxyItemResponse 必须保留全部字段

    单独构造 ProxyItemResponse 时只填基础 11 字段（ISO 字段保持 None），
    由 item_to_response_dict() 在路由层补全 ISO。
    """
    item = ProxyItem(
        ip="8.8.8.8", port=53, score=15,
        source="google-dns",
        last_used_for_crawl=1234567890.5,
    )
    from api.schemas import ProxyItemResponse, item_to_response_dict
    resp = ProxyItemResponse(**item.to_dict())
    d = resp.model_dump()
    # 基础 11 字段必须全部保留
    assert "source" in d, "source 字段丢失"
    assert "last_used_for_crawl" in d, "last_used_for_crawl 字段丢失"
    assert d["source"] == "google-dns"
    assert d["last_used_for_crawl"] == 1234567890.5

    # item_to_response_dict 必须自动填 ISO 字段
    d2 = item_to_response_dict(item)
    assert d2["last_check_iso"] is None  # ts=0 时 None
    assert d2["created_at_iso"] is None
    print("✓ test_proxy_item_response_includes_all_11_fields (P0 丢字段 + ISO helper)")


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
    assert "validation failed" in body["msg"], f"msg 应英文说明校验失败，实际 {body['msg']}"
    assert body["data"] is None
    print("✓ test_validation_error_wrapped_to_business_format (P0 双轨制 + 英文 msg)")


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


def test_iso_timestamps_present_and_format():
    """P2 回归：/proxy/random 响应必须包含 last_check_iso + created_at_iso（ISO8601 格式）"""
    import asyncio
    import re
    item = ProxyItem(
        ip="7.7.7.7", port=8080, score=10,
        last_check=1700000000.0,    # 2023-11-14T22:13:20Z
        created_at=1600000000.0,    # 2020-09-13T12:26:40Z
    )
    asyncio.run(pool.add(item))

    r = get_client().get("/proxy/random")
    data = r.json()["data"]
    assert data is not None
    # ISO8601 格式（含 T 和毫秒 +Z）：2023-11-14T22:13:20.000+00:00
    iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+00:00$")
    assert iso_re.match(data["last_check_iso"]), \
        f"last_check_iso 格式错: {data['last_check_iso']}"
    assert iso_re.match(data["created_at_iso"]), \
        f"created_at_iso 格式错: {data['created_at_iso']}"
    pool._pool.clear()
    print("✓ test_iso_timestamps_present_and_format (P2 ISO8601)")


def test_iso_timestamps_null_for_zero():
    """P2 边界：ts=0 时 ISO 字段必须是 None（避免 1970-01-01 噪声）"""
    item = ProxyItem(
        ip="7.7.7.8", port=8081, score=10,
        last_check=0.0,   # 默认值，未验证过
        created_at=0.0,
    )
    from api.schemas import item_to_response_dict
    d = item_to_response_dict(item)
    assert d["last_check_iso"] is None
    assert d["created_at_iso"] is None
    print("✓ test_iso_timestamps_null_for_zero (P2 边界)")


def test_batch_partial_ok_code_when_verify_short():
    """P2 回归：/proxy/batch verify 模式只找到 < n 时 code=2（部分成功）"""
    import asyncio
    pool._pool.clear()
    # 池里只有 1 个代理，请求 3 个 verify
    asyncio.run(_add_one(ip="1.1.1.1", port=80, score=20))

    r = get_client().post(
        "/proxy/batch",
        json={"n": 3, "verify": True, "verify_timeout": 1.0},
    )
    body = r.json()
    assert body["code"] == 2, f"部分成功应 code=2，实际 {body['code']}"
    assert "partial" in body["msg"], f"msg 应提示 partial，实际 {body['msg']}"
    assert body["data"]["requested"] == 3
    assert body["data"]["count"] == 1
    assert len(body["data"]["items"]) == 1
    pool._pool.clear()
    print("✓ test_batch_partial_ok_code_when_verify_short (P2 code=2)")


def test_batch_full_ok_code_when_verify_satisfied():
    """P2 边界：/proxy/batch verify 模式刚好满足时 code=0（不是 2）"""
    import asyncio
    pool._pool.clear()
    for i in range(3):
        asyncio.run(_add_one(ip=f"1.1.1.{i+1}", port=80, score=20))

    r = get_client().post(
        "/proxy/batch",
        json={"n": 3, "verify": True, "verify_timeout": 1.0},
    )
    body = r.json()
    assert body["code"] == 0, f"完全满足应 code=0，实际 {body['code']}"
    assert body["data"]["count"] == 3
    pool._pool.clear()
    print("✓ test_batch_full_ok_code_when_verify_satisfied (P2 边界)")


def test_msg_is_english_no_chinese():
    """P2 回归：所有 msg / detail 必须是英文（不再有中英混杂）"""
    import re
    cn_pattern = re.compile(r"[\u4e00-\u9fff]+")  # 中文字符
    # 查 5 个代表性接口
    samples = [
        ("GET", "/health"),
        ("GET", "/proxy/count"),
        ("GET", "/proxy/random"),
        ("GET", "/proxy/stats"),
        ("POST", "/proxy/feedback"),  # 期望 422
    ]
    for method, path in samples:
        if method == "GET":
            r = get_client().get(path)
        else:
            r = get_client().post(path, json={"ip": "1.2.3.4", "port": 80, "success": True})
        body = r.json()
        assert not cn_pattern.search(body["msg"]), \
            f"{method} {path} msg 含中文: {body['msg']!r}"
        # data 里也不应有中文（仅检查 None/对象/数组）
        if isinstance(body["data"], dict):
            for k, v in body["data"].items():
                if isinstance(v, str):
                    assert not cn_pattern.search(v), \
                        f"{method} {path} data.{k} 含中文: {v!r}"
    print("✓ test_msg_is_english_no_chinese (P2 msg 统一化)")


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
        # 2026-09-06 P2 新增
        test_iso_timestamps_present_and_format()
        test_iso_timestamps_null_for_zero()
        test_batch_partial_ok_code_when_verify_short()
        test_batch_full_ok_code_when_verify_satisfied()
        test_msg_is_english_no_chinese()
        print("\n=== API 测试全部通过（含 8 个 P0 回归 + 5 个 P2 回归） ===")
    finally:
        teardown_module(None)
