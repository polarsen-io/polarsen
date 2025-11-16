from collections.abc import Mapping

import asyncpg

from .db import db_group
from .embeddings import ai_group
from .run import chat_group

# See: https://github.com/pydantic/pydantic/issues/9406#issuecomment-2104224328
Mapping.register(asyncpg.Record)  # type: ignore[method-assign]
