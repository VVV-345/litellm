"""校验 LMU 公开页面允许的 JSON-LD 模型清单形状。"""

from pydantic import BaseModel, ConfigDict, Field


class LmuJsonLdModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(alias="@type", pattern="^Product$")
    identifier: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:/-]+$")


class LmuJsonLdModelList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(alias="@type", pattern="^ItemList$")
    item_list_element: tuple[LmuJsonLdModel, ...] = Field(alias="itemListElement", min_length=1)
