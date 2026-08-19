from pydantic import BaseModel, ConfigDict, Field


class OpenAICompatibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class OpenAICompatibleModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[OpenAICompatibleModel, ...]
