from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.catalog import OrderLine


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    message: str = Field(min_length=1, max_length=2000)
    cart: list[OrderLine] = Field(default_factory=list, max_length=30)
    locale: Literal["zh-CN", "zh-TW", "en"] = "zh-CN"

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class ChatResponse(BaseModel):
    session_id: str
    message: str
    sandbox_id: str
    model: str


class SessionResponse(BaseModel):
    session_id: str
    deleted: bool
