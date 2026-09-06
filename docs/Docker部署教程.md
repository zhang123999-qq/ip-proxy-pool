# Docker 部署教程

> 目标：把 IP 代理池从「开发机」带到「任何一台有 Docker 的机器」上跑，包括服务器、NAS、CI 环境。

## 一、前置

- Docker ≥ 20.10（推荐 24+，跟 docker compose v2 兼容）
- 项目根目录：`D:\ip-proxy-pool`

## 二、最快 30 秒跑起来

```bash
cd D:\ip-proxy-pool

# 1. 构建镜像 + 后台启动
docker compose up -d --build

# 2. 看是否健康
docker ps --filter name=ip-proxy-pool
# STATUS 应显示 "Up X seconds (healthy)"

# 3. 验证服务
curl http://127.0.0.1:8000/health
# {"code":0,"msg":"ok","data":{"size":N}}

# 4. 看日志
docker compose logs -f proxy-pool
```

## 三、文件清单

| 文件 | 作用 |
|------|------|
| `Dockerfile` | 多阶段构建，生产镜像（最终 287MB） |
| `docker-compose.yml` | 编排：端口映射 / 卷挂载 / 健康检查 / 资源限制 |
| `.dockerignore` | 排除缓存、测试、文档，加快构建 |
| `.env`（可选） | 敏感配置（ADMIN_TOKEN 等） |

## 四、Dockerfile 设计要点

### 1. 多阶段构建（节省 60% 体积）

```
阶段 1 (builder)：python:3.13-slim + gcc → 装包到 /install
阶段 2 (runtime)：python:3.13-slim + tzdata → 从 /install 复制包
```

最终镜像 **287MB**，只含运行时需要的依赖，不含编译器。

### 2. 非 root 用户运行

```dockerfile
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app
USER app
```

容器内进程以 `app` 用户身份运行，**降低被入侵后的破坏半径**。

### 3. 时区本地化

```dockerfile
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone
```

容器日志时间戳用本地时间，跟宿主机一致。

### 4. 健康检查

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1
```

Docker 会自动检测健康状态，配合 `restart: unless-stopped` 实现**崩溃自愈**。

### 5. 立即输出日志

```dockerfile
ENV PYTHONUNBUFFERED=1
```

print / loguru 输出不被 Python 缓冲，**`docker logs` 实时看到**。

## 五、docker-compose 设计要点

### 1. 命名卷持久化

```yaml
volumes:
  - proxy_data:/app/data   # SQLite 数据库
  - proxy_logs:/app/logs   # 日志
```

容器被删重建，数据 **不丢**。

### 2. 资源限制

```yaml
deploy:
  resources:
    limits:
      cpus: "2.0"
      memory: 1024M       # 防止内存爆炸影响宿主机
```

防失控，崩溃时不影响同主机其他容器。

### 3. 容器日志大小限制

```yaml
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"        # 最多保留 30MB 日志
```

防 Docker 日志把磁盘塞满。

### 4. 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `TZ` | `Asia/Shanghai` | 时区 |
| `ADMIN_TOKEN` | 空 | 管理接口 Bearer Token；空=关闭鉴权 |
| `API_PORT` | `8000` | 宿主机端口 |
| `JOB_CRAWL_INTERVAL_MIN` | `10` | 采集周期（分钟） |
| `JOB_VALIDATE_INTERVAL_MIN` | `5` | 验证周期 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_MAX_LINES` | `100000` | 单文件最大行数 |

可通过宿主机 `.env` 文件注入（`docker-compose.yml` 已支持 `${VAR:-default}`）。

## 六、常用命令速查

```bash
# 查看运行状态
docker compose ps
docker inspect --format='{{.State.Health.Status}}' ip-proxy-pool

# 实时日志
docker compose logs -f proxy-pool

# 进入容器调试
docker exec -it ip-proxy-pool bash

# 查看数据库（容器内）
docker exec ip-proxy-pool sqlite3 /app/data/proxy_pool.db "SELECT COUNT(*) FROM proxies"

# 重启服务
docker compose restart proxy-pool

# 重新构建（代码改了之后）
docker compose up -d --build

# 停服务（保留数据）
docker compose down

# 清空所有数据
docker compose down -v

# 查看资源占用
docker stats ip-proxy-pool

# 查看镜像大小
docker images ip-proxy-pool
```

## 七、备份与迁移

### 1. 数据导出（备份）

```bash
# 实时复制数据库（SQLite WAL 安全导出）
docker exec ip-proxy-pool sqlite3 /app/data/proxy_pool.db ".backup '/app/data/backup.db'"
docker cp ip-proxy-pool:/app/data/backup.db ./proxy_pool_$(date +%Y%m%d).db
```

### 2. 迁移到另一台机器

```bash
# 源机器：导出卷
docker run --rm -v ip-proxy-pool_data:/from -v $(pwd):/to alpine tar czf /to/proxy_data.tar.gz -C /from .
docker run --rm -v ip-proxy-pool_logs:/from -v $(pwd):/to alpine tar czf /to/proxy_logs.tar.gz -C /from .

# 目标机器：导入卷
docker volume create ip-proxy-pool_data
docker volume create ip-proxy-pool_logs
docker run --rm -v ip-proxy-pool_data:/to -v $(pwd):/from alpine tar xzf /from/proxy_data.tar.gz -C /to
docker run --rm -v ip-proxy-pool_logs:/to -v $(pwd):/from alpine tar xzf /from/proxy_logs.tar.gz -C /to
```

## 八、生产环境优化建议

### 1. 反向代理 + HTTPS

用 nginx / caddy 套一层：
```nginx
server {
    listen 443 ssl;
    server_name proxy.example.com;
    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 2. 设置 ADMIN_TOKEN

`.env`：
```
ADMIN_TOKEN=$(openssl rand -hex 32)
```

所有 `/admin/*` 调用必须带：
```
Authorization: Bearer <token>
```

### 3. 限制 CORS（开发期是 `*`）

改 `api/app.py`：
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend.com"],
    ...
)
```

### 4. 多实例部署（高可用）

把 `docker-compose.yml` 里的 `container_name` 和 `ports` 注释掉，加 `deploy.replicas: 2`：

```yaml
services:
  proxy-pool:
    # 不要 container_name
    deploy:
      replicas: 2
    ports:
      - "8000-8002:8000"   # 负载均衡
```

> ⚠️ 多实例共享 SQLite 会产生锁竞争。建议生产用 PostgreSQL 或加 Redis 队列分发爬源任务。

## 九、常见问题

### Q1：`docker compose up` 报错 `port is already allocated`
改宿主机端口：
```bash
API_PORT=8888 docker compose up -d
# 或在 .env 里写 API_PORT=8888
```

### Q2：容器重启后数据没了
检查是否用了 `docker compose down -v`（`-v` 会删卷）。
正确做法：只用 `docker compose down`（保留卷）。

### Q3：爬到的代理很多但验证后剩 0
检查宿主机能否访问外网：
```bash
docker exec ip-proxy-pool curl -I https://www.google.com
```
如果失败，需要配置代理或 DNS。

### Q4：健康检查一直 `starting`
说明启动 30s 内服务没起来。查看日志：
```bash
docker logs ip-proxy-pool | tail -50
```
通常是首次爬源网络超时，可调大 `start_period` 或关掉健康检查。

### Q5：磁盘被 Docker 占满
```bash
docker system df               # 看占用
docker system prune -a         # 清理（危险：删所有未用镜像）
docker volume prune            # 清理未用卷（会丢数据）
```

### Q6：如何跑测试套件（不进容器）
```bash
cd D:\ip-proxy-pool
docker compose run --rm proxy-pool python tests/run_all.py
```

## 十、镜像推送到 Docker Hub（远端部署用）

```bash
# 1. 打 tag
docker tag ip-proxy-pool:latest your-dockerhub-user/ip-proxy-pool:latest

# 2. 登录
docker login

# 3. 推送
docker push your-dockerhub-user/ip-proxy-pool:latest
```

远端机器上：
```bash
docker pull your-dockerhub-user/ip-proxy-pool:latest
docker run -d --name proxy-pool \
  -p 8000:8000 \
  -v proxy_data:/app/data \
  -v proxy_logs:/app/logs \
  -e ADMIN_TOKEN=your-secret-token \
  --restart unless-stopped \
  your-dockerhub-user/ip-proxy-pool:latest
```

---

部署完成 🎉。访问 `http://127.0.0.1:8000/docs` 看 Swagger 自动文档。