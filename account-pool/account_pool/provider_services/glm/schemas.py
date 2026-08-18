"""校验 GLM 官方 OpenAI 兼容模型列表响应。"""

from pydantic import BaseModel, ConfigDict


class GlmModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class GlmModelList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    data: tuple[GlmModel, ...]
