"""测试 admin 接口鉴权"""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 设置 token（必须在 config import 之前）
os.environ["ADMIN_TOKEN"] = "secret-123"

from api.app import create_app
from fastapi.testclient import TestClient

# 构造一个 app，关掉 lifespan（避免 scheduler 干扰输出）
app = create_app()


@asynccontextmanager
async def _no_lifespan(_application):
    yield


app.router.lifespan_context = _no_lifespan


def main():
    print("=== admin 鉴权测试 ===", flush=True)
    with TestClient(app) as c:
        # 1. 无 Authorization → 401
        r = c.post("/admin/clear")
        assert r.status_code == 401, f"期望 401，实际 {r.status_code}"
        assert "Bearer" in r.text
        print(f"✓ [1] 无 Authorization: 401 + 友好提示", flush=True)

        # 2. 错 token → 403
        r = c.post(
            "/admin/clear", headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 403, f"期望 403，实际 {r.status_code}"
        print(f"✓ [2] 错 token: 403", flush=True)

        # 3. 正确 token → 200
        r = c.post(
            "/admin/clear", headers={"Authorization": "Bearer secret-123"}
        )
        assert r.status_code == 200, f"期望 200，实际 {r.status_code}"
        print(f"✓ [3] 正确 token: 200", flush=True)

        # 4. 公开接口不需要 token
        r = c.get("/proxy/count")
        assert r.status_code == 200, f"期望 200，实际 {r.status_code}"
        print(f"✓ [4] 公开 /proxy/count: 200", flush=True)

    print("\n=== 全部通过 ===", flush=True)


if __name__ == "__main__":
    main()
