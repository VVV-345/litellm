"""校验 OpenAI 兼容 GET /models 接口的最小响应结构。"""

from pydantic import BaseModel, ConfigDict, Field


class OpenAICompatibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class OpenAICompatibleModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[OpenAICompatibleModel, ...]
