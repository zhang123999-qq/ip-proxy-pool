# IP 代理池

基于 Python + FastAPI 的免费 IP 代理池，**实时打分制**自动维护代理质量。

> 打分规则：初始 10 分 · 可用 +1 / 不可用 -3 · 分数 ≤ 0 自动从池中删除 · 上限 20 分

> 🌐 **在线体验地址（已部署）：https://proxy.xiaowang.kdns.fr/**
> 
> - Swagger 文档：https://proxy.xiaowang.kdns.fr/docs
> - 健康检查：https://proxy.xiaowang.kdns.fr/health
> - 当前池大小（实时）：`{"size": 33473}`（截至 2026-09-06 验证时）
> - 接口与本地完全一致，**Base URL 替换为 `https://proxy.xiaowang.kdns.fr`** 即可直接调用

---

> ## ⚠️ 免责声明 / Disclaimer
>
> **本项目使用的全部代理 IP 源均为公开、免费的第三方代理列表。**
>
> 1. **不保证可靠性（Reliability）**：免费代理源经常改版、下线、被风控、被目标站 ban IP，单源随时可能完全不可用。本项目已内置「熔断 + 指数退避 + SKIP 降级」机制自动避开，但**无法保证 7×24 小时持续可用**。
> 2. **不保证质量（Quality）**：免费代理 IP 的可用率、匿名度、稳定性、延迟、带宽都不一致，可能存在：
>    - 高延迟 / 高丢包（免费代理常见）
>    - 透明代理 / 伪代理（已被 v3 透明代理剔除机制过滤大部分，但仍有残留概率）
>    - **安全风险**（中间人攻击、数据被记录 / 篡改、撞库、Cookie 窃取、IP 被目标站拉黑等）
>    - **隐私风险**（免费代理拥有者可能记录并出售你的请求内容）
> 3. **不保证匿名性**：本池**不承诺匿名 / 不承诺保护用户隐私**。请勿用于登录账号、提交敏感信息、未加密的隐私通信等场景。
> 4. **不保证合规性**：请遵守所在国家/地区的法律法规、《网络安全法》《数据安全法》《个人信息保护法》等。仅用于合法场景（如学习、爬自己拥有数据的网站、或已获授权的用途）。
> 5. **不保证来源合法性**：本项目仅作「免费代理列表的采集与验证」工具，不对单个代理的来源、归属、用途做任何背书。如果某个代理 IP 涉及非法用途，与本项目无关。
> 6. **生产建议**：生产环境强烈建议接入**付费代理服务**（阿里云、腾讯云、ScraperAPI、Oxylabs、SmartProxy 等），或自建代理池。本项目**仅作学习 / 测试 / 内部工具**使用。
>
> **使用本项目即视为同意以上条款。本项目作者不对任何使用本项目造成的任何直接或间接损失承担责任。**
>
> 完整条款见 **[DISCLAIMER.md](DISCLAIMER.md)**
>
> ---
> *Last updated: 2026-09-06 / 加入版本：debug-after-zero-bug-fix release*

## 核心特性

- **22 个内置源 + 无限自定义源**（共 ~31000 条/次，详见下方「内置代理源」表）
- **手动添加代理源（零代码）**：通过 `POST /admin/sources` 动态接入新平台，支持 text/table/api 三种类型，详见 [手动添加代理源教程](docs/手动添加代理源教程.md)
- **代理养代理**：池里代理优先被用作爬源通道（突破风控）
- **智能降级链**：HYBRID → DIRECT → SKIP，避免死循环
- 实时打分引擎，支持**软扣分**（爬源专用 -1，区别于业务 -3）
- **爬源代理冷却**（持久化），防止同一代理被反复用
- 单源最多 2 个代理轮换，避免代理污染
- **验证双阶段加速**（v3 🆕）：L1 TCP 预筛 + L2 HTTP 投票，验证一轮 50 分钟 → 75 秒（**40×**），命中率从 ~10% 提升到 ~25%
- **3 URL 投票**（v3 🆕）：单点抖动误杀 5%→0.5%
- **透明代理剔除**（v3 🆕）：body 不含代理 IP 的判失败，池子更精
- **严格模式 API**（v3.1 🆕）：`/proxy/random?verify=true` 先验证再返回，**保证可用**（1~2s）
- 异步高并发验证（asyncio + httpx）
- 加权随机 + 协议过滤 + 最低分门槛
- FastAPI 提供 RESTful 接口 + CORS
- APScheduler 定时调度（采集 500 分钟 / 验证 8 分钟 / 持久化 1 小时）
- **SQLite 持久化**（WAL 模式，增量写入，断电安全，自动迁移旧 JSON）
- **管理接口鉴权**（`/admin/*` 需 `ADMIN_TOKEN` Bearer Token）
- **按行数轮转日志**（默认保留最近 10 万行 + 时间兜底）
- 线程 + 协程双重并发安全（池锁 + 物品 RLock + sink 互斥锁）

## 内置代理源（共 22 个）

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
**13 个有效源** + 9 个失效源（已标记，不影响使用）

> 💡 **v3 加速后池子数字看起来"变小"，但每个代理更精。** 池从 ~33000 降到 ~12000 不是萎缩，是剔掉了 60% 的透明代理 + 死代理留下的"活的、能用的、稳定的"代理。**[详细对比](#v3-验证加速效果)**

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
| GET | `/proxy/random?verify=true` | **先验证再返回**（保证可用，1~2s）|
| GET | `/proxy/random?verify=true&verify_timeout=2.0` | 严格模式 + 自定义超时 |
| POST | `/proxy/batch` | 批量获取 N 个不重复代理（支持预验证）|
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

### 严格模式（verify）— 先验证再返回

> 适合"拿一个就一定要能用"的场景。

| 模式 | 接口示例 | 耗时 | 说明 |
|---|---|---|---|
| **快模式（默认）** | `/proxy/random` | ~0ms | 加权随机返回（可能拿到刚入库的） |
| **高分模式** | `/proxy/random?min_score=15` | ~0ms | 只返历史多次验证通过的高分代理 |
| **严格模式** | `/proxy/random?verify=true` | 1~2s | 先 L1 TCP 预筛再返回，**保证可用** |
| **批量严格** | `POST /proxy/batch` body `{"n":3,"verify":true}` | 1~2s×N | 批量预验证 |

**参数说明**：
- `verify` (bool, 默认 false)：是否先 TCP 预筛
- `verify_timeout` (float, 默认 1.5s)：单次预筛超时（0.1~5.0）

**严格模式实现要点**：
- **不调用 `update_score`**——只做探测性验证，不影响打分系统
- 最多试 3 次（`max_tries=3`），找到可用代理就立刻返回
- 全失败时返回 `code=1, msg="代理池为空或无符合条件代理"`
- 响应 `msg` 会带 `(verified)` 标记便于区分

**使用建议**：
- ✅ **关键业务**（不允许失败）→ `verify=true`
- ✅ **一次性使用**（拿一个用一个）→ `verify=true`
- ⚠️ **高并发**（每次都测会拖慢）→ 用 `min_score` 过滤代替
- ⚠️ **批量**（拿 100 个）→ 用 `min_score` 过滤

**示例**：
```bash
# 默认（快，0ms）
curl https://proxy.xiaowang.kdns.fr/proxy/random

# 严格模式（保证可用）
curl "https://proxy.xiaowang.kdns.fr/proxy/random?verify=true&verify_timeout=2.0"

# 强制高分（0ms 但筛选高质）
curl "https://proxy.xiaowang.kdns.fr/proxy/random?min_score=15"

# 批量 verify
curl -X POST https://proxy.xiaowang.kdns.fr/proxy/batch \
  -H "Content-Type: application/json" \
  -d '{"n": 3, "verify": true, "verify_timeout": 1.5}'
```

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
| 代理采集 | 每 500 分钟 | 启动后立即 |
| 代理验证 | 每 8 分钟 | 启动后 +30 秒 |
| 持久化 | 每 1 小时 | 启动后 1 小时 |

可在 `config.py` 调整。

---

## 文档

- [⚠️ 免责声明 / Disclaimer](DISCLAIMER.md) **使用前必读**
- [需求文档](docs/需求文档.md)
- [手动添加代理源教程](docs/手动添加代理源教程.md)
- [Docker 部署教程](docs/Docker部署教程.md)
- [验证加速方案 v3](docs/验证加速方案.md)
- [反爬方案](docs/反爬方案.md)
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

✅ 9 个单元测试套件全过（80+ 用例）
✅ 1 个深度压力测试套件全过（8 模块）
✅ **22 个内置代理源**（13 有效 + 9 已知失效）
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
✅ **验证双阶段加速 v3**（50 分钟 → 75 秒，40× 提速）🆕

---

## v3 验证加速效果

池数字看起来"变小了"——这是**质量提升**而不是萎缩。

| 指标 | v2（旧） | v3（新） | 倍数 |
|------|---------|---------|------|
| **验证一轮耗时** | 50 分钟 | **75 秒** | **40×** |
| **实际可用代理命中率** | ~10% | **~25%** | 2.5× |
| **新代理平均入库可用时间** | 30-65 分钟 | **5-15 分钟** | 3-5× |
| **池规模（持久存活数）** | ~33000 | ~12000 | 缩水但**单代理价值 3×** |
| **采集一轮耗时** | 30-60 秒 | **15-20 秒** | 2× |
| **采集间隔** | 10 分钟 | **500 分钟** | 减少无效爬源（v3.1: 100→500）|

**核心改动（4 个文件）**：

1. **`validator/checker.py` 重写为双阶段**：
   - **L1 TCP 预筛**（1000 并发，1s 超时）—— 70-80% 死代理毫秒级判死
   - **L2 HTTP 投票**（500 并发，3 URL，2/3 过）—— 抗单点抖动
   - **复用 AsyncClient** —— 一次 TLS 握手 → 全池代理共享
   - **失败重试 1 次** —— 防瞬时抖动误杀
   - **透明代理剔除** —— body 不含代理 IP 算失败

2. **`config.py` 新增 6 个常量 + 改 2 个默认值**：
   - `VALIDATE_TIMEOUT 10→2`、`VALIDATE_CONCURRENCY 50→500`
   - `JOB_CRAWL_INTERVAL_MIN 10→500`、`JOB_VALIDATE_INTERVAL_MIN 5→8`
   - 新增 `VALIDATE_URLS` / `VALIDATE_PASS_THRESHOLD` / `VALIDATE_TCP_CONCURRENCY` / `VALIDATE_TCP_TIMEOUT` / `VALIDATE_RETRY_ONCE` / `VALIDATE_MAX_OK_RATE`

3. **`.env.example`** 暴露全部 10 个新变量

4. **`tests/test_validator_stages.py`**（新文件）—— 7 用例覆盖新逻辑

### 为什么池规模"变小"是好事

| 类型 | v2 | v3 |
|------|----|----|
| 真活的代理 | ~3000 | **~10000** ✅ |
| 透明代理（访问能通但不真代理） | ~20000 | ~0 ✅ |
| 死代理（端口不通） | ~10000 | ~0 ✅ |
| **池规模（持久存活数）** | **~33000** | **~12000** ✅ |

> v3 把**透明代理**和**死代理**剔掉，留下的都是"真活、真代理"的代理。
> 池规模数字变小 ≠ 能力变弱。每个 `/proxy/random` 返回的代理**可用率从 ~10% 提升到 ~25%**。

详细方案见 [docs/验证加速方案.md](docs/验证加速方案.md)。
