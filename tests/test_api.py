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
        print("\n=== API 测试全部通过 ===")
    finally:
        teardown_module(None)
