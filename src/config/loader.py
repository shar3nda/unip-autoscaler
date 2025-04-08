from typing import List

from kubernetes_asyncio import watch
from pydantic import ValidationError

from src.config.model import ScalingConfig
from src.k8s.k8s_client import k8s
from src.utils.logger import logger


async def load_autoscaler_configs() -> List[ScalingConfig]:
    group = "autoscaler.unified-platform.cs.hse.ru"
    version = "v1alpha1"
    plural = "scalingconfigs"

    result = []
    config_instances = await k8s.customObjectsApi.list_custom_object_for_all_namespaces(
        group=group,
        version=version,
        resource_plural=plural,
    )
    for cfg in config_instances.get("items", []):
        try:
            instance = ScalingConfig(**cfg["spec"])
            result.append(instance)
            logger.info(
                f"Loaded config {cfg['metadata']['name']} from namespace {cfg['metadata']['namespace']}"
            )
        except ValidationError as e:
            logger.error(f"Invalid config: {e}")
    return result


async def watch_config(callback: callable = None) -> None:
    group = "autoscaler.unified-platform.cs.hse.ru"
    version = "v1alpha1"
    plural = "scalingconfigs"

    w = watch.Watch()
    async for event in w.stream(
        func=k8s.customObjectsApi.list_namespaced_custom_object,
        group=group,
        version=version,
        plural=plural,
        namespace="",
    ):
        obj = event["object"]
        event_type = event["type"]
        logger.info(f"{event_type} event received for: {obj['metadata']['name']}")

        if callback is not None:
            try:
                await callback()
            except Exception as e:
                logger.error(f"Error in callback: {e}")
