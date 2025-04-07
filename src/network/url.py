from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from fastapi import Request


def set_https_prefix(url: str) -> str:
    return url.replace("http://", "https://", 1)


def get_retry_redirect_url(request: Request, retries: int) -> str:
    url_parts = list(urlparse(str(request.url)))
    query = dict(parse_qsl(url_parts[4]))
    query.update({"retries": retries})
    url_parts[4] = urlencode(query)
    return urlunparse(url_parts)
