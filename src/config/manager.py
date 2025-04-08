from asyncio import Lock
from typing import List

from src.config.loader import load_autoscaler_configs
from src.config.model import ScalingConfig


class ConfigManager:
    def __init__(self):
        self._configs: List[ScalingConfig] | None = None
        self._configs_lock = Lock()
        self._modified: float | None = None
        self._modified_lock = Lock()

    async def load_configs(self) -> None:
        async with self._configs_lock:
            self._configs = await load_autoscaler_configs()

    async def get_configs(self) -> List[ScalingConfig]:
        async with self._configs_lock:
            return self._configs
