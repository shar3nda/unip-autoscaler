import base64
import time
from fastapi import FastAPI, Header
from typing_extensions import Annotated
from typing import Union
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse


from .functions import *

app = FastAPI(docs_url=None, redoc_url=None)

class Deployment(BaseModel):
    namespace:str
    name:str


@app.post("/hibernate")
async def hibernate(deployment:Deployment):
    return hibernate_deployment(deployment.name, deployment.namespace)

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(path: str,  request: Request,
                 autoscaler_app_namespace: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_deployment: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_service: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_ingress: Annotated[Union[str, None], Header(convert_underscores=False)] = None,
                 autoscaler_app_ingress_rewrite: Annotated[Union[str, None], Header(convert_underscores=False)] = None
                 ):
    scale_deployment(dep = get_deployment(name = autoscaler_app_deployment, namespace = autoscaler_app_namespace),
                     replicas = 1)

    if is_service_ready(namespace = autoscaler_app_namespace, name = autoscaler_app_service):
        wakeup_ingress(namespace = autoscaler_app_namespace,
                       serviceName = autoscaler_app_service,
                       ingName = autoscaler_app_ingress,
                       rewriteRule = base64.b64decode(autoscaler_app_ingress_rewrite.encode("utf-8")).decode("utf-8"))
    else:
        time.sleep(AUTOSCALER_READINESS_PROBE_TIMEOUT)

    return JSONResponse(status_code = 307, content ={
        "path": path,
        "autoscaler_app_namespace": autoscaler_app_namespace,
        "autoscaler_app_deployment": autoscaler_app_deployment,
        "autoscaler_app_service": autoscaler_app_service,
        "autoscaler_app_ingress": autoscaler_app_ingress,
        "autoscaler_app_ingress_rewrite": base64.b64decode(autoscaler_app_ingress_rewrite.encode("utf-8")).decode("utf-8"),
        "headers": dict(request.headers)
    })
