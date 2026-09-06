# IP 代理池

基于 Python + FastAPI 的免费 IP 代理池，**实时打分制**自动维护代理质量。

> 打分规则：初始 10 分 · 可用 +1 / 不可用 -3 · 分数 ≤ 0 自动从池中删除 · 上限 20 分

> 🌐 **在线体验地址（已部署）：https://proxy.xiaowang.kdns.fr/**
> 
> - Swagger 文档：https://proxy.xiaowang.kdns.fr/docs
> - 健康检查：https://proxy.xiaowang.kdns.fr/health
> - 当前池大小（实时）：`{"size": 33473}`（截至 2026-09-06 验证时）
> - 接口与本地完全一致，**Base URL 替换为 `https://proxy.xiaowang.kdns.fr`** 即可直接调用

## 核心特性

- **19 个内置源 + 无限自定义源**（共 ~31000 条/次，详见下方「内置代理源」表）
- **手动添加代理源（零代码）**：通过 `POST /admin/sources` 动态接入新平台，支持 text/table/api 三种类型，详见 [手动添加代理源教程](docs/手动添加代理源教程.md)
- **代理养代理**：池里代理优先被用作爬源通道（突破风控）
- **智能降级链**：HYBRID → DIRECT → SKIP，避免死循环
- 实时打分引擎，支持**软扣分**（爬源专用 -1，区别于业务 -3）
- **爬源代理冷却**（持久化），防止同一代理被反复用
- 单源最多 2 个代理轮换，避免代理污染
- 异步高并发验证（asyncio + httpx）
- 加权随机 + 协议过滤 + 最低分门槛
- FastAPI 提供 RESTful 接口 + CORS
- APScheduler 定时调度（采集 / 验证 / 持久化）
- **SQLite 持久化**（WAL 模式，增量写入，断电安全，自动迁移旧 JSON）
- **管理接口鉴权**（`/admin/*` 需 `ADMIN_TOKEN` Bearer Token）
- **按行数轮转日志**（默认保留最近 10 万行 + 时间兜底）
- 线程 + 协程双重并发安全（池锁 + 物品 RLock + sink 互斥锁）

## 内置代理源（共 19 个）

| 源 | 类型 | 协议 | 典型量 | 状态 |
|----|------|------|-------|------|
| kuaidaili-free | HTML 表格 | http/https | 36 | ✅ |
| kuaidaili-inha | HTML 表格 | http/https | 0 | ⚠ Cloudflare |
| 66ip-free | HTML 表格 | http | 0 | ⚠ 改版 |
| 89ip-free | HTML 表格 | http | 80 | ✅ |
| iphai-free | HTML 表格 | http | 0 | ⚠ 改版 |
| proxydaily-http | HTML | http | — | ❌ 下线 |
| qiyunip-free | HTML/文本 | http | 0 | ⚠ 改版 |
| zhandaye-free | HTML 表格 | http/https | 0 | ⚠ 改版 |
| kuaidaili-https | HTML 表格 | https | 24 | ✅ |
| xiaohuan-http | HTML 表格 | http | 18 | ✅ |
| **proxyscrape-api** | **API/文本** | **http/https** | **1112** | ✅ |
| **openproxylist-http** | **API/文本** | **http** | **5953** | ✅ |
| proxylist-download-http | API | http/https | 0 | ⚠ 改版 |
| free-proxy-list-net | HTML 表格 | http/https | 308 | ✅ |
| **databay-api** 🆕 | **API/文本** | **http** | **6361** 🔥 | ✅ |
| **proxyscrape-v4** 🆕 | **API/文本** | **http/https** | **1973** 🔥 | ✅ |
| **proxyscrape-v2-https** 🆕 | **API/文本** | **https** | **841** 🔥 | ✅ |
| **proxmint-api** 🆕 | **API/文本** | **http** | **50** | ✅ |
| **github-proxylist-http** 🆕 | **GitHub raw 聚合** | **http/https** | **27867** 🔥 | ✅ |

**实际单次采集：~31000 条**（去重后约 8000-15000 条）
**10 个有效源** + 9 个失效源（已标记，不影响使用）

### GitHub 聚合源覆盖 11 个 raw 仓库

| 仓库 | 单次条数 |
|------|---------|
| proxy4parsing/proxy-list (http) | ~19000 |
| TheSpeedX/PROXY-List (http) | ~3000 |
| sunny9577/proxy-scraper (http) | ~2000 |
| proxmint/free-proxy-list (all) | ~1400 |
| zloi-user/hideip.me (https) | ~1000 |
| monosans/proxy-list (http) | ~400 |
| vakhov/fresh-proxy-list (http) | ~500 |
| roosterkid/openproxylist (https) | ~45 |
| ShiftyTR/Proxy-List (http/https) | ~50 |
| hookzof/socks5_list | ~260 |

### 源验证脚本

```bash
python tests/verify_sources.py
```

输出每次跑 31 个候选源的实际可用状态（HTTP 200 + 解析到 N 条），
并写到 `data/verified_sources.txt`（CI / 监控可读）。

---

## 快速开始

### 方式 1：在线体验（无需部署，直接用）

服务已部署在公网，直接调用即可：

```bash
# 基础 URL：https://proxy.xiaowang.kdns.fr

# 1. 看池大小
curl https://proxy.xiaowang.kdns.fr/proxy/count

# 2. 随机拿一个代理
curl https://proxy.xiaowang.kdns.fr/proxy/random

# 3. 反馈使用结果
curl -X POST https://proxy.xiaowang.kdns.fr/proxy/feedback \
  -H "Content-Type: application/json" \
  -d '{"ip":"1.2.3.4","port":8080,"success":true}'

# 4. 看完整 API 文档
open https://proxy.xiaowang.kdns.fr/docs
```

> 接口与本地完全一致，只是把 `http://127.0.0.1:8000` 替换成 `https://proxy.xiaowang.kdns.fr`。
> 公网实例管理接口需 `ADMIN_TOKEN` 鉴权（如需请私聊站长）。

### 方式 2：本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python main.py
# 浏览器访问 http://127.0.0.1:8000/docs 看 API 文档
```

### 方式 3：Docker 部署（推荐生产）

```bash
# 一行启动
docker compose up -d --build

# 看健康
docker ps --filter name=ip-proxy-pool
curl http://127.0.0.1:8000/health
```

完整教程（含镜像优化、备份迁移、HTTPS、k8s 适配）：[Docker部署教程](docs/Docker部署教程.md)。

---

## 项目结构

```
ip-proxy-pool/
├── crawler/      # 代理采集
├── validator/    # 代理验证
├── pool/         # 代理池 + 打分引擎
├── api/          # FastAPI 接口
├── scheduler/    # 定时调度
├── models/       # 数据模型
├── utils/        # 工具
├── tests/        # 单元测试
├── config.py     # 全局配置
├── main.py       # 入口
├── Dockerfile    # Docker 镜像（多阶段构建）
└── docker-compose.yml  # 一键编排
```

---

## API 文档

> Base URL：`http://<host>:8000`  
> 启动后访问 `/docs` 看 Swagger 自动文档

### 基础接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查 |
| GET | `/proxy/count` | 当前代理数量 |
| GET | `/proxy/stats` | 池统计（平均/最高/最低分 + 协议分布） |
| GET | `/proxy/random` | 随机获取一个代理（加权） |
| GET | `/proxy/random?protocol=https&min_score=5` | 协议 + 分数过滤 |
| GET | `/proxy/all?page=1&page_size=20&protocol=http` | 分页列表 |
| GET | `/proxy/top?n=10` | 分数最高 N 个 |
| POST | `/proxy/batch` | 批量获取 N 个不重复代理 |
| POST | `/proxy/feedback` | 反馈使用结果（影响分数） |
| POST | `/proxy/remove` | 主动删除代理 |
| POST | `/proxy/refresh?mode=auto` | 手动触发采集 + 验证 |

### 智能爬源接口（代理养代理）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/proxy/crawl-mode` | 当前爬源模式 + 决策依据 + 上次结果 |
| GET | `/proxy/usage` | 代理爬源使用情况 + 冷却剩余 |
| GET | `/proxy/last-crawl` | 上次爬取结果详情 |

### 管理接口（需鉴权）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/admin/save` | 手动刷盘（需 `Authorization: Bearer <ADMIN_TOKEN>`） |
| POST | `/admin/clear` | 清空代理池（需同上） |
| GET | `/admin/sources` | 查看所有自定义代理源 |
| POST | `/admin/sources` | 新增自定义代理源（text/table/api） |
| DELETE | `/admin/sources/{name}` | 删除自定义代理源 |
| POST | `/admin/sources/{name}/toggle` | 启停自定义代理源（`{"enabled": true/false}`） |

未设置 `ADMIN_TOKEN` 时管理接口开放（开发模式），生产环境务必设置。

> 💡 想加新代理平台？三种 type（text/table/api）覆盖 90% 场景：
> ```bash
> curl -X POST http://127.0.0.1:8000/admin/sources \
>   -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
>   -d '{"name":"my-src","type":"text","url":"https://.../list.txt","proto":"http"}'
> ```
> 完整教程：[docs/手动添加代理源教程.md](docs/手动添加代理源教程.md)

### 环境变量

```bash
# .env 复制 .env.example，按需修改
LOG_MAX_LINES=100000        # 单日志文件最多保留行数（默认 10 万）
LOG_RETENTION_DAYS=10       # 兜底保留天数
ADMIN_TOKEN=                # 管理接口 Bearer Token（留空=关闭鉴权）
```

### 客户端示例

#### 1. 获取代理

```bash
curl http://127.0.0.1:8000/proxy/random
```

```json
{
  "code": 0,
  "msg": "ok",
  "data": {
    "ip": "1.2.3.4",
    "port": 8080,
    "protocol": "http",
    "score": 12,
    "response_time": 0.85,
    "success_count": 3,
    "fail_count": 1,
    "last_check": 1788669359.79,
    "created_at": 1788669000.0
  }
}
```

#### 2. 业务使用后反馈

```bash
curl -X POST http://127.0.0.1:8000/proxy/feedback \
  -H "Content-Type: application/json" \
  -d '{"ip":"1.2.3.4","port":8080,"success":true}'
```

```json
{
  "code": 0,
  "msg": "1.2.3.4:8080 分数 13 (success=True)",
  "data": { "ip": "1.2.3.4", "port": 8080, "new_score": 13, "success": true }
}
```

#### 3. Python 客户端

```python
import httpx

API = "http://127.0.0.1:8000"

def get_and_use() -> str:
    """获取代理 + 真实使用 + 反馈结果"""
    r = httpx.get(f"{API}/proxy/random", timeout=5).json()
    if r["code"] != 0:
        raise RuntimeError("代理池为空")
    p = r["data"]
    proxy = f"http://{p['ip']}:{p['port']}"

    # 真实业务调用
    success = False
    try:
        resp = httpx.get("https://httpbin.org/ip", proxy=proxy, timeout=10)
        if resp.status_code == 200:
            success = True
    except Exception:
        pass

    # 反馈分数
    httpx.post(
        f"{API}/proxy/feedback",
        json={"ip": p["ip"], "port": p["port"], "success": success},
        timeout=5,
    )
    return proxy if success else ""
```

---

## 打分机制

| 事件 | 分数变化 | 触发方式 |
|------|---------|---------|
| 入库 | **10 分** | 采集入池 / 验证通过 |
| 可用 | **+1** | 后台定时验证 / 业务反馈 success=true |
| 不可用 | **-3** | 后台定时验证 / 业务反馈 success=false |
| 分数 ≤ 0 | **自动删除** | 池中移除 |
| 分数 ≥ 20 | 封顶 | 不再加分 |

---

## 调度任务

| 任务 | 默认周期 | 首次执行 |
|------|---------|---------|
| 代理采集 | 每 10 分钟 | 启动后立即 |
| 代理验证 | 每 5 分钟 | 启动后 +30 秒 |
| 持久化 | 每 1 小时 | 启动后 1 小时 |

可在 `config.py` 调整。

---

## 文档

- [需求文档](docs/需求文档.md)
- [手动添加代理源教程](docs/手动添加代理源教程.md)
- [Docker 部署教程](docs/Docker部署教程.md)
- [开发方案](开发方案.md)

## 在线体验

🌐 服务已部署在公网：**https://proxy.xiaowang.kdns.fr/**

| 入口 | URL |
|------|-----|
| Swagger 文档 | https://proxy.xiaowang.kdns.fr/docs |
| 健康检查 | https://proxy.xiaowang.kdns.fr/health |
| 当前池数量 | https://proxy.xiaowang.kdns.fr/proxy/count |
| 随机代理 | https://proxy.xiaowang.kdns.fr/proxy/random |
| 池统计 | https://proxy.xiaowang.kdns.fr/proxy/stats |
| 爬源模式 | https://proxy.xiaowang.kdns.fr/proxy/crawl-mode |

公网部署基于 Cloudflare + 自有服务器，24h 在线，池大小实时 30000+ 条。
接口与本地完全兼容，`http://127.0.0.1:8000` 换成上面的域名就能用。

## 状态

✅ 8 个单元测试套件全过（73 用例）
✅ 1 个深度压力测试套件全过（8 模块）
✅ **19 个内置代理源**（10 有效 + 9 已知失效）
✅ 实测单次采集 **~31000 条**（去重后 8000+）
✅ SQLite + WAL + 自动 JSON 迁移
✅ 代理养代理（HYBRID/DIRECT/SKIP 智能降级）
✅ 按行数轮转日志（默认 10 万行）
✅ 管理接口鉴权
✅ 手动添加代理源（零代码）
✅ 并发安全（池锁 + 物品 RLock + sink 锁 + reload swap）
✅ 线程 + 协程双重并发安全
✅ 详细中文注释（新手友好）
✅ **公网在线体验：https://proxy.xiaowang.kdns.fr/**
✅ **Docker 一键部署（287MB 镜像）**
