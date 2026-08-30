"""校验厂商模型列表接口的最小响应结构。"""

from pydantic import BaseModel, ConfigDict, Field


class _OpenAICompatibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class OpenAICompatibleModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[_OpenAICompatibleModel, ...]


class _AnthropicModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class AnthropicModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[_AnthropicModel, ...]
    has_more: bool = False
    last_id: str | None = None


class _GeminiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(min_length=1)


class GeminiModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: tuple[_GeminiModel, ...] = ()
    next_page_token: str | None = Field(default=None, alias="nextPageToken")


class _OllamaModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    name: str = Field(min_length=1)


class OllamaModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    models: tuple[_OllamaModel, ...] = ()
