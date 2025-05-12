from apscheduler.schedulers.asyncio import AsyncIOScheduler
from kubernetes_asyncio import watch
from pydantic import ValidationError

from src.autoscaler.core import autoscale_target
from src.config.model import ScalingConfig
from src.k8s.k8s_client import k8s
from src.settings import AUTOSCALER_CHECK_INTERVAL
from src.utils.logger import logger


async def handle_config_event(
    event: dict,
    scheduler: AsyncIOScheduler,
) -> None:
    obj = event["object"]
    event_type = event["type"]
    metadata = obj["metadata"]

    uid = metadata["uid"]
    rv = metadata["resourceVersion"]
    name = metadata["name"]
    namespace = metadata["namespace"]

    logger.debug(
        f"Handling event: {event_type} for {name=} {namespace=}, {uid=}, {rv=}"
    )

    if event_type == "DELETED":
        try:
            scheduler.remove_job(uid)
            logger.info(f"Removed job for {name=} {namespace=}")
        except Exception as e:
            logger.warning(
                f"Tried to remove job for {name=} {namespace=}, but got error: {e}"
            )
        return

    try:
        config = ScalingConfig(**obj["spec"])
    except ValidationError as e:
        logger.error(f"Invalid config for {name=} {namespace=}: {e}")
        return

    existing_job = scheduler.get_job(uid)

    if existing_job:
        existing_rv = existing_job.kwargs.get("resourceVersion")
        if existing_rv == rv:
            logger.info(f"No changes for {name=} {namespace=}, skipping job update")
            return

    scheduler.add_job(
        autoscale_target,
        trigger="interval",
        seconds=AUTOSCALER_CHECK_INTERVAL,
        id=uid,
        kwargs={"config": config, "resourceVersion": rv},
        replace_existing=True,
    )
    if existing_job:
        logger.info(f"Updated job for {name=} {namespace=}")
    else:
        logger.info(f"Added job for {name=} {namespace=}")


async def watch_config(
    scheduler: AsyncIOScheduler,
):
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
        await handle_config_event(
            event=event,
            scheduler=scheduler,
        )
