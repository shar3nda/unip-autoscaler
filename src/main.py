import asyncio
import base64
from contextlib import asynccontextmanager
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing_extensions import Annotated
from ua_parser import user_agent_parser

from src.autoscaler.core import autoscale_target
from src.config.loader import watch_config
from src.config.manager import ConfigManager
from src.k8s.actions import (
    scale_deployment,
    wakeup_ingress,
)
from src.k8s.healthcheck import check_readiness_probe, is_service_ready
from src.k8s.k8s_client import k8s
from src.k8s.resolver import get_deployment, get_service
from src.network.url import (
    get_retry_redirect_url,
    set_https_prefix,
)
from src.network.user_agents import USER_AGENTS_CONFIG
from src.settings import (
    AUTOSCALER_CHECK_INTERVAL,
    AUTOSCALER_READINESS_LIMIT,
    AUTOSCALER_READINESS_TIMEOUT,
)
from src.utils.logger import logger

scheduler = AsyncIOScheduler()
config_mgr = ConfigManager()


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

    watch_task = asyncio.create_task(watch_config(init_scheduler))
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

    redirect_url = set_https_prefix(str(request.url))

    logger.info(f"Redirecting to {redirect_url}")
    return RedirectResponse(url=redirect_url, status_code=307)
