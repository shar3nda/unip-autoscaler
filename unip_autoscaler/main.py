import base64
import time
from fastapi import FastAPI, Header
from typing_extensions import Annotated
from typing import Union
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import RedirectResponse

from .functions import *

app = FastAPI(docs_url=None, redoc_url=None)

class Deployment(BaseModel):
    namespace:str
    name:str


@app.post("/hibernate")
async def hibernate(deployment:Deployment):
    print(f"Hibernating deployment {deployment.name}")
    return hibernate_deployment(deployment.name, deployment.namespace)

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(path: str,  request: Request,
                 autoscaler_app_namespace: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_deployment: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_service: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_ingress: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_ingress_rewrite: Annotated[Union[str, None], Header(convert_underscores=False)] = None
                 ):
    deployment = get_deployment(name = autoscaler_app_deployment, namespace = autoscaler_app_namespace)
    service = get_service(name = autoscaler_app_service, namespace = autoscaler_app_namespace)
    check_readiness_probe(deployment, service)

    scale_deployment(dep = deployment, replicas = 1)
    for i in range(AUTOSCALER_READINESS_LIMIT):
        if is_service_ready(namespace = autoscaler_app_namespace, name = autoscaler_app_service):
            print(f"Service {autoscaler_app_service}  is ready")
            wakeup_ingress(namespace = autoscaler_app_namespace,
                       serviceName = autoscaler_app_service,
                       ingName = autoscaler_app_ingress,
                       rewriteRule = base64.b64decode(autoscaler_app_ingress_rewrite.encode("utf-8")).decode("utf-8"))
            time.sleep(3)
            break
        else:
            print(f"Service {autoscaler_app_service} is not ready {i}")
            time.sleep(AUTOSCALER_READINESS_TIMEOUT)

    return RedirectResponse(url = request.url, status_code = 307)
