import os
from asyncio import Lock
from typing import List

import aiofiles
import yaml
from pydantic import ValidationError

from src.config.model import ScalingConfig
from src.settings import AUTOSCALER_SPEC_FILE
from src.utils.logger import logger


async def _load_autoscaler_configs() -> List[ScalingConfig]:
    result = []

    async with aiofiles.open(AUTOSCALER_SPEC_FILE) as f:
        spec = await f.read()
    configs = list(yaml.safe_load_all(spec))

    for config in configs:
        try:
            instance = ScalingConfig(**config)
            result.append(instance)
        except ValidationError as e:
            logger.error(f"Invalid config: {e}")
            continue

    return result


class ConfigManager:
    def __init__(self):
        self._configs: List[ScalingConfig] | None = None
        self._configs_lock = Lock()
        self._modified: float | None = None
        self._modified_lock = Lock()

    async def load_configs(self) -> None:
        async with self._configs_lock:
            self._configs = await _load_autoscaler_configs()

    async def get_configs(self) -> List[ScalingConfig]:
        async with self._configs_lock:
            return self._configs

    async def get_modified(self) -> float:
        async with self._modified_lock:
            return self._modified

    async def load_modified(self) -> None:
        async with self._modified_lock:
            self._modified = os.stat(AUTOSCALER_SPEC_FILE).st_mtime
