from typing import TypedDict, Dict

class UserAgentSettings(TypedDict):
    redirects: int
    timeout: int

MAX_REDIRECTS = 50
MAX_TIMEOUT = 900

USER_AGENTS_CONFIG: Dict[str, UserAgentSettings] = {
    "default": {
        "redirects": 20,
        "timeout": 300,
    },
    "Safari": {
        "redirects": 16,
        "timeout": 300,
    },
    "Java": {
        "redirects": 20,
        "timeout": MAX_TIMEOUT,
    },
    "Apache-HttpClient": {
        "redirects": 50,
        "timeout": MAX_TIMEOUT,
    },
    "curl": {
        "redirects": 50,
        "timeout": MAX_TIMEOUT,
    },
    "Go-http-client": {
        "redirects": 10,
        "timeout": MAX_TIMEOUT,
    },
    "axios": {
        "redirects": 20,
        "timeout": MAX_TIMEOUT,
    },
    "node": {
        "redirects": 20,
        "timeout": MAX_TIMEOUT,
    },
    "PostmanRuntime": {
        "redirects": 10,
        "timeout": MAX_TIMEOUT,
    },
}
