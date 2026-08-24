"""校验 New API GET /models 与 GET /api/pricing 的最小响应结构。"""

from pydantic import BaseModel, ConfigDict, Field


class NewApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class NewApiModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[NewApiModel, ...]


class NewApiPricingEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    channel_type: int | None = None
    model_ratio: float | None = None
    model_ratio_2: float | None = None
    model_ratio_3: float | None = None
    group_ratio: float | None = None
    completion_ratio: float | None = None
    cache_ratio: float | None = None
    cache_write_ratio: float | None = None
    owner_by: int | None = None


class NewApiPricingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    success: bool
    data: dict[str, NewApiPricingEntry] = Field(default_factory=dict)


class NewApiLoginData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int | None = None


class NewApiLoginResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    success: bool
    message: str | None = None
    data: NewApiLoginData | None = None
