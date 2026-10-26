"""统一的业务异常，以及向 OpenAI 错误格式转换的方法。"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ServiceError(Exception):
    """所有可预期错误的基类。

    status_code 用于 HTTP 状态码；error_type/code 用于 OpenAI 风格的 error 对象，
    方便前端和 OpenAI SDK 统一处理。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        error_type: str = "server_error",
        code: str = "internal_error",
        param: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param

    def to_openai(self) -> Dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": self.error_type,
                "param": self.param,
                "code": self.code,
            }
        }


class BadRequestError(ServiceError):
    def __init__(self, message: str, *, code: str = "invalid_request", param: Optional[str] = None):
        super().__init__(
            message,
            status_code=400,
            error_type="invalid_request_error",
            code=code,
            param=param,
        )


class AuthenticationError(ServiceError):
    def __init__(self, message: str = "缺少或无效的 API Key"):
        super().__init__(
            message,
            status_code=401,
            error_type="authentication_error",
            code="invalid_api_key",
        )


class NotFoundError(ServiceError):
    def __init__(self, message: str, *, code: str = "not_found"):
        super().__init__(message, status_code=404, error_type="not_found_error", code=code)


class ProviderError(ServiceError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "provider_error",
    ):
        super().__init__(message, status_code=status_code, error_type="server_error", code=code)


class ProviderNotConfiguredError(ProviderError):
    def __init__(self, message: str):
        super().__init__(message, status_code=503, code="provider_not_configured")


class ConfigurationError(ServiceError):
    def __init__(self, message: str):
        super().__init__(
            message,
            status_code=503,
            error_type="server_error",
            code="configuration_error",
        )
