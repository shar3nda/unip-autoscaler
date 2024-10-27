from asyncio import Lock
from datetime import datetime

from .autoscaling_config import ScalingTarget

__scaling_timestamps: dict[tuple[str, str, str], datetime] = {}
__lock = Lock()


def __get_target_key(target: ScalingTarget) -> tuple[str, str, str]:
    return target["kind"], target["name"], target["namespace"]


async def set_scaling_timestamp(target: ScalingTarget, timestamp: datetime) -> None:
    async with __lock:
        __scaling_timestamps[__get_target_key(target)] = timestamp


async def has_cooldown(target: ScalingTarget, cooldown: int) -> bool:
    async with __lock:
        timestamp = __scaling_timestamps.get(__get_target_key(target))
        if timestamp is None:
            return False

        return (datetime.now() - timestamp).total_seconds() < cooldown
