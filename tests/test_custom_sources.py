"""测试自定义源增/查/删 + 鉴权 + 类型校验"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试期内开启 admin token，模拟生产模式
os.environ["ADMIN_TOKEN"] = "test-token"

from api.app import create_app
from fastapi.testclient import TestClient
from storage.dao import custom_source_dao
from crawler.manager import reset_manager

# 关 lifespan 避免起 scheduler
app = create_app()


@asynccontextmanager
async def _no_lifespan(_application):
    yield


app.router.lifespan_context = _no_lifespan

AUTH = {"Authorization": "Bearer test-token"}


def _cleanup(name: str) -> None:
    """测试后清理（不影响其他套件）"""
    try:
        custom_source_dao.delete(name)
    except Exception:
        pass


def main():
    print("=== 自定义代理源测试 ===", flush=True)

    # 测前：清掉一切（避免上次残留）
    for src in custom_source_dao.list_all(enabled_only=False):
        custom_source_dao.delete(src.name)
    reset_manager()

    with TestClient(app) as c:

        # 1. 鉴权：无 token → 401
        r = c.post("/admin/sources", json={
            "name": "t-text", "type": "text",
            "url": "https://example.com/list.txt", "proto": "http",
        })
        assert r.status_code == 401, f"期望 401，实际 {r.status_code}"
        print("✓ [1] 无 token: 401", flush=True)

        # 2. 类型校验：非法 type → 422 (Pydantic)
        r = c.post(
            "/admin/sources", headers=AUTH,
            json={"name": "x", "type": "pdf", "url": "https://x.com"},
        )
        assert r.status_code in (400, 422), \
            f"期望 4xx，实际 {r.status_code}"
        print(f"✓ [2] 非法 type: {r.status_code}", flush=True)

        # 3. 新增 text 源
        r = c.post(
            "/admin/sources", headers=AUTH,
            json={
                "name": "t-text",
                "type": "text",
                "url": "https://raw.githubusercontent.com/example/list/master/http.txt",
                "proto": "http",
                "config": {},
                "enabled": True,
            },
        )
        assert r.status_code == 200, \
            f"新建源 200，实际 {r.status_code}: {r.text}"
        data = r.json()["data"]
        assert data["name"] == "t-text"
        assert data["type"] == "text"
        assert data["enabled"] is True
        print("✓ [3] 新增 text 源: 200", flush=True)

        # 4. 重名 → 400
        r = c.post(
            "/admin/sources", headers=AUTH,
            json={"name": "t-text", "type": "text",
                  "url": "https://x.com/list.txt"},
        )
        assert r.status_code == 400, \
            f"重名期望 400，实际 {r.status_code}"
        print("✓ [4] 重名 source: 400", flush=True)

        # 5. 列表接口能看到
        r = c.get("/admin/sources", headers=AUTH)
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        names = [it["name"] for it in items]
        assert "t-text" in names, f"列表里应有 t-text，实际 {names}"
        print(f"✓ [5] 列表接口: 200 ({len(items)} 个)", flush=True)

        # 6. 工厂：能用 config 构造爬虫
        from crawler.custom import build_crawler_from_source
        src = custom_source_dao.get("t-text")
        assert src is not None
        crawler = build_crawler_from_source(src)
        assert crawler.name == "t-text", \
            f"crawler.name 期望 t-text，实际 {crawler.name}"
        print(f"✓ [6] 工厂: {crawler.name} (type={type(crawler).__name__})", flush=True)

        # 7. 启停：toggle 关闭 → DB enabled=0
        r = c.post(
            "/admin/sources/t-text/toggle",
            headers=AUTH, json={"enabled": False},
        )
        assert r.status_code == 200, f"期望 200，实际 {r.status_code}"
        src2 = custom_source_dao.get("t-text")
        assert src2.enabled is False, \
            f"已禁用但 DB 仍 enabled: {src2.enabled}"
        print("✓ [7] toggle 停用: 200, enabled=False", flush=True)

        # 8. toggle 启回 → DB enabled=1
        r = c.post(
            "/admin/sources/t-text/toggle",
            headers=AUTH, json={"enabled": True},
        )
        assert r.status_code == 200
        assert custom_source_dao.get("t-text").enabled is True
        print("✓ [8] toggle 启用: 200, enabled=True", flush=True)

        # 9. 删除
        r = c.delete("/admin/sources/t-text", headers=AUTH)
        assert r.status_code == 200, f"删除 200，实际 {r.status_code}"
        # 删后再查 → 404
        assert custom_source_dao.get("t-text") is None
        print("✓ [9] 删除: 200 + DB 已空", flush=True)

        # 10. 删不存在的 → 404
        r = c.delete("/admin/sources/ghost-name", headers=AUTH)
        assert r.status_code == 404, \
            f"删不存在期望 404，实际 {r.status_code}"
        print("✓ [10] 删不存在: 404", flush=True)

        # 11. table 类型（带分页配置）也能新增
        r = c.post(
            "/admin/sources", headers=AUTH,
            json={
                "name": "t-table",
                "type": "table",
                "url": "https://example.com/list?page=1",
                "proto": "http",
                "config": {
                    "ip_col": 0, "port_col": 1, "proto_col": 3,
                    "pagination": {"param": "page", "start": 1, "end": 3},
                },
            },
        )
        assert r.status_code == 200, f"table 200，实际 {r.status_code}: {r.text}"
        print("✓ [11] table+分页配置新增: 200", flush=True)

        # 12. api 类型（带 sub_urls）也能新增
        r = c.post(
            "/admin/sources", headers=AUTH,
            json={
                "name": "t-api",
                "type": "api",
                "url": "https://example.com/api/main",
                "proto": "http",
                "config": {
                    "sub_urls": [
                        {"url": "https://example.com/api/http", "proto": "http"},
                        {"url": "https://example.com/api/https", "proto": "https"},
                    ],
                },
            },
        )
        assert r.status_code == 200, f"api 200，实际 {r.status_code}: {r.text}"
        print("✓ [12] api+sub_urls 配置新增: 200", flush=True)

    # 整体收尾
    _cleanup("t-text")
    _cleanup("t-table")
    _cleanup("t-api")
    print("\n=== 全部通过 ===", flush=True)


if __name__ == "__main__":
    main()
