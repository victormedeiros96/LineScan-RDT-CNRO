from collections.abc import AsyncGenerator

from fastapi import Depends

from src.core.config import Settings, get_settings


async def get_settings_dep() -> AsyncGenerator[Settings, None]:
    yield get_settings()
