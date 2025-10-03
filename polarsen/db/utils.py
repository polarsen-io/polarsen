from dataclasses import dataclass, field, asdict

__all__ = ("TableID",)


@dataclass
class TableID:
    _id: int | None = field(default=None, init=False)

    @property
    def data(self):
        _data = asdict(self)
        _id = _data.pop("_id")
        if _id is not None:
            _data["id"] = _id
        return _data

    @property
    def id(self):
        if self._id is None:
            raise ValueError("Data not been saved yet")
        return self._id
