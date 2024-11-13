import asyncio
import base64
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, Query
from kubernetes_asyncio import watch
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import RedirectResponse
from typing_extensions import Annotated
from ua_parser import user_agent_parser

from .functions import (
    autoscale_target,
    check_readiness_probe,
    get_deployment,
    get_service,
    get_service_from_config,
    hibernate_by_deployment,
    hibernate_by_service,
    is_service_ready,
    load_autoscaler_configs,
    scale_deployment,
    wakeup_ingress,
)
from .k8s_client import k8s
from .logger import logger
from .settings import (
    AUTOSCALER_CHECK_INTERVAL,
    AUTOSCALER_READINESS_LIMIT,
    AUTOSCALER_READINESS_TIMEOUT,
)
from .user_agents import USER_AGENTS_CONFIG

scheduler = AsyncIOScheduler()
CONFIGS = None


async def watch_configmap():
    w = watch.Watch()

    # FIXME: doesn't work for some reason
    async for event in w.stream(
        k8s.coreV1API.read_namespaced_config_map,
        name="autoscaler-props",
        namespace="unip-system-autoscaler",
    ):
        logger.info(f"Event: {event}")
        if event["type"] in ("MODIFIED", "ADDED"):
            logger.info("configMap changed, reloading autoscaler configurations")
            await init_scheduler()


async def init_scheduler():
    logger.info("Initializing scheduler")
    logger.info("Loading autoscaler configurations")
    configs = await load_autoscaler_configs()

    scheduler.remove_all_jobs()

    for cfg in configs:
        logger.info(f"Adding job for {cfg.target}")
        logger.debug(f"{AUTOSCALER_CHECK_INTERVAL=}")
        scheduler.add_job(
            autoscale_target,
            trigger="interval",
            seconds=AUTOSCALER_CHECK_INTERVAL,
            kwargs={"config": cfg},
        )
    global CONFIGS
    CONFIGS = configs
    logger.info("Scheduler initialized")
    return


@asynccontextmanager
async def lifespan(app: FastAPI):
    await k8s.init_client()

    await init_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    # watch_task = asyncio.create_task(watch_configmap())
    try:
        yield
    finally:
        # watch_task.cancel()
        # await watch_task
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

    for cfg in CONFIGS:
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

            url_parts = list(urlparse(str(request.url)))
            query = dict(parse_qsl(url_parts[4]))
            query.update({"retries": retries})
            url_parts[4] = urlencode(query)
            redirect_url = urlunparse(url_parts)

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

    logger.info(f"Redirecting to {request.url}")
    return RedirectResponse(url=request.url, status_code=307)
