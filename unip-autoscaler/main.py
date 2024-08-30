from fastapi import FastAPI, Body
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from functions import hibernate_deployment

app = FastAPI(docs_url=None, redoc_url=None)

class Deployment(BaseModel):
    namespace:str
    name:str


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(request: Request, path: str):
    print(path)
    return JSONResponse(status_code = 307, content = "")

@app.post("/hibernate")
async def hibernate(deployment:Deployment):
    hibernate_deployment(deployment.name, deployment.namespace)
    return JSONResponse(status_code = 307, content = "")
