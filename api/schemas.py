"""
API 数据模型（请求/响应）
========================

用 Pydantic 定义所有 HTTP 请求体和响应体的结构。
Pydantic 会自动：
- 校验字段类型（IP 必须是 IPv4，port 必须在 1-65535）
- 把 JSON 转成 Python 对象
- 在 Swagger 文档里生成示例

阅读建议：
- 5 个请求模型：Feedback / Remove / Batch / SourceCreate / SourceToggle
- 2 个响应模型：ProxyItemResponse / CommonResponse
"""
import ipaddress
from typing import Dict, Optional
from pydantic import BaseModel, Field, field_validator


def _validate_ip(v: str) -> str:
    """
    严格校验 IPv4 地址
    field_validator 会自动调用这个函数
    """
    try:
        ipaddress.IPv4Address(v)
        return v
    except (ValueError, TypeError):
        raise ValueError(f"非法 IP 地址: {v}")


# ============================================================
# 请求体模型
# ============================================================
class FeedbackRequest(BaseModel):
    """
    反馈请求体

    POST /proxy/feedback
    {
      "ip": "1.2.3.4",
      "port": 8080,
      "success": true
    }
    """
    ip: str = Field(..., description="代理 IP")
    port: int = Field(..., gt=0, lt=65536, description="代理端口（1-65535）")
    success: bool = Field(..., description="是否使用成功")

    # Pydantic v2 的字段校验写法
    _v_ip = field_validator("ip")(_validate_ip)


class RemoveRequest(BaseModel):
    """
    主动删除请求体

    POST /proxy/remove
    {"ip": "1.2.3.4", "port": 8080}
    """
    ip: str = Field(..., description="代理 IP")
    port: int = Field(..., gt=0, lt=65536, description="代理端口")

    _v_ip = field_validator("ip")(_validate_ip)


class BatchGetRequest(BaseModel):
    """
    批量获取请求体

    POST /proxy/batch
    {"n": 5, "protocol": "http", "min_score": 1, "verify": false}
    {"n": 3, "verify": true, "verify_timeout": 1.5}  ← 严格模式
    """
    n: int = Field(5, ge=1, le=100, description="获取数量（1-100）")
    protocol: Optional[str] = Field(
        None, description="协议过滤: http / https / both"
    )
    min_score: int = Field(1, ge=0, le=20, description="最低分数门槛")
    verify: bool = Field(
        False, description="是否先 TCP 预筛再返回（保证可用，1~2s 延迟）"
    )
    verify_timeout: float = Field(
        1.5, ge=0.1, le=5.0, description="verify 模式的单次验证超时（秒）"
    )


# ============================================================
# 响应体模型
# ============================================================
class ProxyItemResponse(BaseModel):
    """单个代理的完整信息"""
    ip: str
    port: int
    protocol: str
    score: int
    response_time: float
    success_count: int
    fail_count: int
    last_check: float
    created_at: float


class CommonResponse(BaseModel):
    """
    统一响应结构

    {
      "code": 0,       # 0=成功，1=业务失败
      "msg":  "ok",    # 描述
      "data": {...}    # 实际数据（可选）
    }
    """
    code: int = 0
    msg: str = "ok"
    data: Optional[object] = None


# ============================================================
# 自定义代理源（手动添加源）请求 / 响应模型
# ============================================================
class SourceCreateRequest(BaseModel):
    """
    新增自定义代理源（POST /admin/sources）

    示例 - 纯文本 GitHub raw：
        {
          "name": "my-github-http",
          "type": "text",
          "url":  "https://raw.githubusercontent.com/xxx/list/master/http.txt",
          "proto": "http",
          "config": { "headers": {"X-My-Header": "x"} }
        }

    示例 - HTML 表格（自动列定位）：
        {
          "name": "my-table-site",
          "type": "table",
          "url":  "https://example.com/proxies",
          "proto": "http",
          "config": { "ip_col": 0, "port_col": 1, "proto_col": 3,
                      "pagination": {"param": "page", "start": 1, "end": 5} }
        }

    示例 - 通用 API：
        {
          "name": "my-api",
          "type": "api",
          "url":  "https://example.com/api/proxies",
          "proto": "https",
          "config": { "sub_urls": [
              {"url": ".../http", "proto": "http"},
              {"url": ".../https", "proto": "https"} ] }
        }
    """
    name: str = Field(
        ..., min_length=1, max_length=64,
        description="源唯一名（字母/数字/中划线/下划线）"
    )
    type: str = Field(
        ..., description="text / table / api"
    )
    url: str = Field(
        ..., min_length=8, max_length=2048,
        description="目标 URL（http/https）"
    )
    proto: str = Field(
        "http", description="默认协议：http / https"
    )
    config: Dict = Field(
        default_factory=dict,
        description="可选解析配置（按 type 不同字段含义不同）",
    )
    enabled: bool = Field(
        True, description="是否立即启用"
    )

    @field_validator("type")
    @classmethod
    def _v_type(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"text", "table", "api"}:
            raise ValueError("type 必须是 text / table / api")
        return v

    @field_validator("proto")
    @classmethod
    def _v_proto(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in {"http", "https"}:
            raise ValueError("proto 必须是 http / https")
        return v

    @field_validator("name")
    @classmethod
    def _v_name(cls, v: str) -> str:
        # 名字只允许字母数字中划线下划线（防注入到日志/csv）
        import re
        if not re.fullmatch(r"[A-Za-z0-9._\-]+", v):
            raise ValueError("name 只能包含字母/数字/中划线/下划线/点")
        return v


class SourceToggleRequest(BaseModel):
    """
    启停源（POST /admin/sources/{name}/toggle）

    {"enabled": false}
    """
    enabled: bool = Field(..., description="true=启用, false=停用")
