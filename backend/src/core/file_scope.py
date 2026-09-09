"""Explicit file-scope states. Legacy sentinel values stay at retrieval adapters."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FileScope:
    mode: Literal["unrestricted", "restricted", "empty"]
    ids: tuple[int, ...] = ()

    def __post_init__(self):
        if self.mode not in {"unrestricted", "restricted", "empty"}:
            raise ValueError("Unknown file scope mode")
        if any(type(i) is not int or i <= 0 for i in self.ids):
            raise ValueError("File scope IDs must be positive integers")
        if bool(self.ids) != (self.mode == "restricted"):
            raise ValueError("Only restricted scopes carry IDs")

    @classmethod
    def from_legacy(cls, ids):
        if ids == [-1] or ids == (-1,):
            return cls("empty")
        return cls("restricted", tuple(sorted(set(ids)))) if ids else cls("unrestricted")

    def retrieval_ids(self):
        # Existing vector/SQL adapters interpret [] as unrestricted.
        return [-1] if self.mode == "empty" else list(self.ids) or None

    def to_dict(self):
        return {"mode": self.mode, "ids": list(self.ids)}
