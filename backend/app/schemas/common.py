from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class ListResponse(BaseModel, Generic[ItemT]):
    """A collection response, wrapped so pagination can be added later."""

    items: list[ItemT]
