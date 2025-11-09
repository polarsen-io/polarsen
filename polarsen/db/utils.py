from dataclasses import dataclass, field, asdict
import datetime as dt

__all__ = ("TableID",)


@dataclass
class TableID:
    _id: int | None = field(default=None, init=False)
    _created_at: dt.datetime | None = field(default=None, init=False)

    @property
    def data(self):
        _data = asdict(self)
        _id = _data.pop("_id", None)
        if _id is not None:
            _data["id"] = _id
        _created_at = _data.pop("_created_at", None)
        if _created_at is not None:
            _data["created_at"] = _created_at

        return _data

    @property
    def id(self):
        if self._id is None:
            raise ValueError("Data not been saved yet")
        return self._id

    @property
    def created_at(self):
        if self._created_at is None:
            raise ValueError("Data not been saved yet")
        return self._created_at
