"""
灵枢异常体系 — 结构化错误分层。

所有业务异常继承 AppError，自带 code/recoverable/detail/suggestion。
中间件自动捕获并返回统一 JSON 格式。
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """所有业务异常的基类。

    Attributes:
        message: 人类可读的错误描述
        code:    机器可读的错误码（如 VLM_UNAVAILABLE）
        recoverable: 是否可恢复（true=前端可展示重试按钮）
        detail:  技术细节（可选，不暴露给用户）
        suggestion: 恢复建议
    """

    code = "UNKNOWN"
    recoverable = False

    def __init__(
        self,
        message: str = "",
        *,
        code: str | None = None,
        recoverable: bool | None = None,
        detail: Any = None,
        suggestion: str = "",
    ):
        self.message = message or self.__doc__ or ""
        if code:
            self.code = code
        if recoverable is not None:
            self.recoverable = recoverable
        self.detail = detail
        self.suggestion = suggestion
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "recoverable": self.recoverable,
                "detail": self.detail,
                "suggestion": self.suggestion,
            },
        }


# ── 基础设施异常 ──────────────────────────────────


class ConfigError(AppError):
    """配置缺失或无效"""
    code = "CONFIG_INVALID"
    recoverable = True


class DependencyUnavailable(AppError):
    """外部依赖（ComfyUI/Ollama/模型）不可用"""
    code = "DEP_UNAVAILABLE"
    recoverable = True


class DiskSpaceLow(AppError):
    """磁盘空间不足"""
    code = "DISK_LOW"
    recoverable = False


# ── 模型异常 ──────────────────────────────────────


class VLMUnavailable(AppError):
    """VLM 视觉模型不可用"""
    code = "VLM_UNAVAILABLE"
    recoverable = True


class LLMUnavailable(AppError):
    """LLM 推理后端不可用"""
    code = "LLM_UNAVAILABLE"
    recoverable = True


class ModelOutOfMemory(AppError):
    """显存不足"""
    code = "OOM"
    recoverable = False


# ── 生成异常 ──────────────────────────────────────


class ComfyUIDown(AppError):
    """ComfyUI 服务未运行"""
    code = "COMFYUI_DOWN"
    recoverable = True


class ImageGenerationFailed(AppError):
    """图片生成失败（ComfyUI 返回错误）"""
    code = "GEN_FAILED"
    recoverable = True


# ── 安全异常 ──────────────────────────────────────


class AuthError(AppError):
    """认证失败"""
    code = "AUTH_FAILED"
    recoverable = True


class RateLimitExceeded(AppError):
    """速率限制"""
    code = "RATE_LIMITED"
    recoverable = True
    suggestion = "请稍后重试"


class InputValidationError(AppError):
    """输入校验不通过"""
    code = "INVALID_INPUT"
    recoverable = False


class SecurityError(AppError):
    """安全限制（路径遍历、注入等）"""
    code = "SECURITY"
    recoverable = False


# ── 调度异常 ──────────────────────────────────────


class TaskTimeout(AppError):
    """任务执行超时"""
    code = "TASK_TIMEOUT"
    recoverable = True


class TaskFailed(AppError):
    """任务执行失败"""
    code = "TASK_FAILED"
    recoverable = True


class AllFallbacksExhausted(AppError):
    """所有降级方案均失败"""
    code = "ALL_FALLBACKS_EXHAUSTED"
    recoverable = False


# ── FastAPI 异常处理器 ────────────────────────────

import logging

logger = logging.getLogger(__name__)


def register_error_handlers(app):
    """注册 AppError 异常处理器到 FastAPI 应用。"""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        logger.warning(
            "AppError %s: %s", exc.code, exc.message,
            extra={"code": exc.code, "recoverable": exc.recoverable, "path": request.url.path},
        )
        return JSONResponse(
            status_code=503 if not exc.recoverable else 200,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        logger.exception("未预期的异常: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "INTERNAL",
                    "message": "服务器内部错误",
                    "recoverable": False,
                    "suggestion": "请查看服务端日志",
                },
            },
        )
