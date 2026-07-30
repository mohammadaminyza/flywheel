from typing import Any, Self, TypeVar

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)


class BaseResponse(BaseModel):
    """Router-facing response. Mapping from domain models happens here, never in a service."""

    @classmethod
    def from_model(cls, model: BaseModel) -> Self:
        return cls.model_validate(model.model_dump())

    @classmethod
    def from_list_model(cls, models: list[Any]) -> list[Self]:
        return [cls.from_model(model) for model in models]
