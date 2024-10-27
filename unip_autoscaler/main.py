import asyncio
import base64
from fastapi import FastAPI, Header, Query
from typing_extensions import Annotated
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import RedirectResponse
from ua_parser import user_agent_parser
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode


from .user_agents import USER_AGENTS_CONFIG
from .logger import logger
from .functions import (
    get_deployment,
    check_readiness_probe,
    get_service,
    is_service_ready,
    wakeup_ingress,
    hibernate_by_deployment,
    scale_deployment,
    hibernate_by_service,
)
from .settings import AUTOSCALER_READINESS_LIMIT, AUTOSCALER_READINESS_TIMEOUT


app = FastAPI(docs_url=None, redoc_url=None)


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
    if service == "kogan-cl-mlcmp-svc":
        return await hibernate_by_service(namespace, service)
    logger.info("Skip service hibernation")


@app.post("/hibernate")
async def hibernate(deployment: Deployment):
    logger.info(f"Hibernating deployment {deployment.name}")
    return await hibernate_by_deployment(deployment.name, deployment.namespace)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(
    path: str,
    request: Request,
    retries: Annotated[int | None, Query()] = 0,
    autoscaler_app_namespace: Annotated[
        str | None, Header(convert_underscores=False)
    ] = None,
    autoscaler_app_deployment: Annotated[
        str | None, Header(convert_underscores=False)
    ] = None,
    autoscaler_app_service: Annotated[
        str | None, Header(convert_underscores=False)
    ] = None,
    autoscaler_app_ingress: Annotated[
        str | None, Header(convert_underscores=False)
    ] = None,
    autoscaler_app_ingress_rewrite: Annotated[
        str | None, Header(convert_underscores=False)
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

    return RedirectResponse(url=request.url, status_code=307)
