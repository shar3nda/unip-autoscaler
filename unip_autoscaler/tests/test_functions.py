import pytest
from unip_autoscaler.functions import (
    get_retry_redirect_url,
)

class DummyRequest:
    def __init__(self, url: str):
        self.url = url


@pytest.mark.parametrize("original_url,retries,expected_query", [
    ("https://platform.stratpro.hse.ru/predict", 1, "retries=1"),
    ("https://platform.stratpro.hse.ru/pu-test/pa-test/predict?foo=bar", 2, "foo=bar&retries=2"),
    ("https://platform.stratpro.hse.ru/?retries=5", 6, "retries=6"),
])
def test_build_retry_redirect_url(original_url, retries, expected_query):
    request = DummyRequest(url=original_url)
    new_url = get_retry_redirect_url(request, retries)
    assert expected_query in new_url
