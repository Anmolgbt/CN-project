"""Pydantic request/response models and the structured error envelope."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_CHUNK = 8 * 1024 * 1024


class StartTransfer(BaseModel):
    file_id: str = Field(min_length=1, max_length=300)
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=9000, ge=1, le=65535)
    connections: int = Field(default=4, ge=1, le=16)
    chunk_size: int = Field(default=1024 * 1024, ge=1024, le=MAX_CHUNK)
    restart: bool = False


class StartBenchmark(BaseModel):
    file_id: Optional[str] = Field(default=None, max_length=300)
    size_mb: Optional[int] = Field(default=None, ge=1, le=1024)
    host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: int = Field(default=9000, ge=1, le=65535)
    chunk_size: int = Field(default=1024 * 1024, ge=1024, le=MAX_CHUNK)
    connections: List[int] = Field(default_factory=lambda: [1, 2, 4, 8], min_length=1, max_length=6)
    repeats: int = Field(default=1, ge=1, le=3)

    @field_validator("connections")
    @classmethod
    def _conns(cls, v: List[int]) -> List[int]:
        if any(not 1 <= c <= 16 for c in v) or len(set(v)) != len(v):
            raise ValueError("connections must be unique values between 1 and 16")
        return v

    @model_validator(mode="after")
    def _one_source(self) -> "StartBenchmark":
        if (self.file_id is None) == (self.size_mb is None):
            raise ValueError("provide exactly one of file_id or size_mb")
        return self


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raised by routes/manager; rendered as {"error": {"code", "message"}}."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
