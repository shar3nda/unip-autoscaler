from asyncio import Lock
from datetime import datetime
from typing import Dict, Tuple

from .autoscaling_config import Target

__scaling_timestamps: Dict[Tuple[str, str, str], datetime] = {}
__lock = Lock()


def __get_target_key(target: Target) -> Tuple[str, str, str]:
    return target.kind, target.name, target.namespace


async def set_scaling_timestamp(target: Target, timestamp: datetime) -> None:
    async with __lock:
        __scaling_timestamps[__get_target_key(target)] = timestamp


async def has_cooldown(target: Target, cooldown: int) -> bool:
    async with __lock:
        timestamp = __scaling_timestamps.get(__get_target_key(target))
        if timestamp is None:
            return False

        return (datetime.now() - timestamp).total_seconds() < cooldown
