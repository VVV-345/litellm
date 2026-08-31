"""校验通用解析器使用的模型列表与兼容价格响应结构。"""

from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class CompatibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str = Field(min_length=1)


class CompatibleModelList(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    data: tuple[CompatibleModel, ...]


class CompatiblePricingEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    model_name: str | None = Field(default=None, min_length=1)
    channel_type: int | None = None
    quota_type: int | None = None
    model_ratio: float | None = None
    model_price: float | None = None
    model_ratio_2: float | None = None
    model_ratio_3: float | None = None
    group_ratio: float | None = None
    completion_ratio: float | None = None
    cache_ratio: float | None = None
    cache_write_ratio: float | None = Field(
        default=None,
        validation_alias=AliasChoices("cache_write_ratio", "create_cache_ratio"),
    )


class CompatiblePricingResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    success: bool
    data: dict[str, CompatiblePricingEntry] | tuple[CompatiblePricingEntry, ...] = Field(default_factory=dict)
    group_ratio: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_list_model_names(self) -> Self:
        if isinstance(self.data, tuple) and any(item.model_name is None for item in self.data):
            raise ValueError("pricing list entries require model_name")
        return self

    def entries(self) -> tuple[tuple[str, CompatiblePricingEntry], ...]:
        if isinstance(self.data, dict):
            return tuple(self.data.items())
        return tuple((item.model_name, item) for item in self.data if item.model_name is not None)


class CompatibleLoginData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int | None = None


class CompatibleLoginResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    success: bool
    message: str | None = None
    data: CompatibleLoginData | None = None
