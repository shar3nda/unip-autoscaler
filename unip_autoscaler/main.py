import asyncio
import base64
import math
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing_extensions import Annotated
from ua_parser import user_agent_parser

from .config_manager import ConfigManager
from .functions import (
    autoscale_target,
    get_retry_redirect_url,
    check_readiness_probe,
    get_deployment,
    get_service,
    get_service_from_config,
    hibernate_by_deployment,
    hibernate_by_service,
    is_service_ready,
    scale_deployment,
    set_https_prefix,
    wakeup_ingress,
)
from .k8s_client import k8s
from .logger import logger
from .settings import (
    AUTOSCALER_CHECK_INTERVAL,
    AUTOSCALER_READINESS_LIMIT,
    AUTOSCALER_READINESS_TIMEOUT,
    AUTOSCALER_SPEC_FILE,
    NAMESPACE_REGEX,
)
from .user_agents import USER_AGENTS_CONFIG

scheduler = AsyncIOScheduler()
config_mgr = ConfigManager()


async def watch_configmap():
    # TODO move to APSched timed job
    if not AUTOSCALER_SPEC_FILE:
        logger.error("AUTOSCALER_SPEC_FILE not set")
        return

    while True:
        try:
            prev = await config_mgr.get_modified()
            await config_mgr.load_modified()
            cur = await config_mgr.get_modified()
            if prev is not None and not math.isclose(prev, cur):
                logger.info("configMap changed, reloading autoscaler configurations")
                await init_scheduler()
        except Exception as e:
            logger.error(f"Error watching ConfigMap file: {e}")
        await asyncio.sleep(5)


async def init_scheduler():
    logger.info("Initializing scheduler")
    logger.info("Loading autoscaler configurations")
    await config_mgr.load_configs()

    scheduler.remove_all_jobs()

    configs = await config_mgr.get_configs()

    for cfg in configs:
        logger.info(f"Adding job for {cfg.target}")
        scheduler.add_job(
            autoscale_target,
            trigger="interval",
            seconds=AUTOSCALER_CHECK_INTERVAL,
            kwargs={"config": cfg},
        )
    logger.info(f"Scheduler initialized, {AUTOSCALER_CHECK_INTERVAL=}")
    return


@asynccontextmanager
async def lifespan(app: FastAPI):
    await k8s.init_client()

    await init_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    watch_task = asyncio.create_task(watch_configmap())
    try:
        yield
    finally:
        watch_task.cancel()
        scheduler.shutdown()
        logger.info("Scheduler stopped")


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)


class Deployment(BaseModel):
    namespace: str
    name: str


class AnnotationsModel(BaseModel):
    namespace: str
    service: str


class AlertRequestModel(BaseModel):
    commonAnnotations: AnnotationsModel


@app.post("/alert")
async def alert(alert: AlertRequestModel):
    namespace = alert.commonAnnotations.namespace
    service = alert.commonAnnotations.service
    logger.info(f"ALERT: {namespace}, {service}")
    if not NAMESPACE_REGEX.match(namespace):
        logger.info("Namespace does not match regex, skipping hibernation")
        return

    for cfg in await config_mgr.get_configs():
        svc = await get_service_from_config(cfg)
        svc_name = svc.metadata.name
        if not (cfg.target.namespace == namespace and svc_name == service):
            continue
        if not cfg.scalingOptions.hibernationEnabled:
            logger.info("Hibernation disabled for service, skipping hibernation")
        return await hibernate_by_service(namespace, svc_name)
    logger.info("No matching configuration found, skipping hibernation")
    return


@app.post("/hibernate")
async def hibernate(deployment: Deployment):
    if not NAMESPACE_REGEX.match(deployment.namespace):
        logger.info("Namespace does not match regex, skipping hibernation")
        return
    logger.info(f"Hibernating deployment {deployment.name}")
    return await hibernate_by_deployment(deployment.name, deployment.namespace)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(
    path: str,
    request: Request,
    retries: Annotated[Optional[int], Query()] = 0,
    autoscaler_app_namespace: Annotated[
        Optional[str], Header(convert_underscores=False)
    ] = None,
    autoscaler_app_deployment: Annotated[
        Optional[str], Header(convert_underscores=False)
    ] = None,
    autoscaler_app_service: Annotated[
        Optional[str], Header(convert_underscores=False)
    ] = None,
    autoscaler_app_ingress: Annotated[
        Optional[str], Header(convert_underscores=False)
    ] = None,
    autoscaler_app_ingress_rewrite: Annotated[
        Optional[str], Header(convert_underscores=False)
    ] = None,
):
    user_agent = (
        user_agent_parser.Parse(request.headers["user-agent"])
        .get("user_agent", {})
        .get("family", "default")
    )
    if user_agent not in USER_AGENTS_CONFIG:
        user_agent = "default"
    logger.info(f"User-Agent: {user_agent}")
    max_retries = USER_AGENTS_CONFIG[user_agent]["redirects"]
    max_timeout = USER_AGENTS_CONFIG[user_agent]["timeout"]

    if retries >= max_retries:
        logger.info(f"Max retries reached {retries}")
        raise Exception("Max retries reached")

    retries += 1
    deployment = await get_deployment(
        name=autoscaler_app_deployment, namespace=autoscaler_app_namespace
    )
    service = await get_service(
        name=autoscaler_app_service, namespace=autoscaler_app_namespace
    )
    await check_readiness_probe(deployment, service)

    await scale_deployment(dep=deployment, replicas=1)
    for i in range(AUTOSCALER_READINESS_LIMIT):
        if AUTOSCALER_READINESS_TIMEOUT * i * 1.2 >= max_timeout:
            logger.info(f"Timeout reached {max_timeout}")

            redirect_url = set_https_prefix(get_retry_redirect_url(request, retries))

            logger.info(f"Redirecting to {redirect_url}")

            return RedirectResponse(url=redirect_url, status_code=307)
        if await is_service_ready(
            namespace=autoscaler_app_namespace, name=autoscaler_app_service
        ):
            logger.info(f"Service {autoscaler_app_service}  is ready")
            await wakeup_ingress(
                namespace=autoscaler_app_namespace,
                serviceName=autoscaler_app_service,
                ingName=autoscaler_app_ingress,
                rewriteRule=base64.b64decode(
                    autoscaler_app_ingress_rewrite.encode("utf-8")
                ).decode("utf-8"),
            )
            await asyncio.sleep(3)
            break
        else:
            logger.info(f"Service {autoscaler_app_service} is not ready {i}")
            await asyncio.sleep(AUTOSCALER_READINESS_TIMEOUT)

    redirect_url = set_https_prefix(request.url)

    logger.info(f"Redirecting to {redirect_url}")
    return RedirectResponse(url=redirect_url, status_code=307)
