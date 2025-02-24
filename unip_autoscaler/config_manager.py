from asyncio import Lock
import os
from typing import List

from .autoscaling_config import ScalingConfig
from .functions import load_autoscaler_configs
from .settings import AUTOSCALER_SPEC_FILE


class ConfigManager:
    def __init__(self):
        self._configs: List[ScalingConfig] | None = None
        self._configs_lock = Lock()
        self._modified = None
        self._modified_lock = Lock()

    async def load_configs(self) -> None:
        async with self._configs_lock:
            self._configs = await load_autoscaler_configs()

    async def get_configs(self) -> List[ScalingConfig]:
        async with self._configs_lock:
            return self._configs

    async def get_modified(self) -> float:
        async with self._modified_lock:
            return self._modified

    async def load_modified(self) -> None:
        async with self._modified_lock:
            self._modified = os.stat(AUTOSCALER_SPEC_FILE).st_mtime
