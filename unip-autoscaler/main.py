from fastapi import FastAPI, Body
from starlette.requests import Request
from starlette.responses import JSONResponse

app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def wakeup(request: Request, path: str):
    print(path)
    return JSONResponse(status_code = 307, content = "")
