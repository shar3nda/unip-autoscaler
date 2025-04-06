from unittest.mock import MagicMock, patch

import pytest

from src.functions import (
    autoscale_target,
    get_new_replica_count,
    get_retry_redirect_url,
)


@pytest.mark.parametrize(
    "original_url,retries,expected_query",
    [
        (
            "https://platform.stratpro.hse.ru/predict",
            1,
            "retries=1",
        ),
        (
            "https://platform.stratpro.hse.ru/pu-test/pa-test/predict?foo=bar",
            2,
            "foo=bar&retries=2",
        ),
        (
            "https://platform.stratpro.hse.ru/?retries=5",
            6,
            "retries=6",
        ),
    ],
)
def test_build_retry_redirect_url(original_url, retries, expected_query):
    request = MagicMock()
    request.url = original_url
    new_url = get_retry_redirect_url(request, retries)
    assert expected_query in new_url


@pytest.mark.asyncio
async def test_get_new_replica_count(get_simple_scaling_config):
    config = get_simple_scaling_config()
    with patch("unip_autoscaler.functions.fetch_prometheus_metric", return_value=80):
        result = await get_new_replica_count(config, 1)
        assert result == 2
    with patch("unip_autoscaler.functions.fetch_prometheus_metric", return_value=70):
        result = await get_new_replica_count(config, 1)
        assert result is None
    with patch("unip_autoscaler.functions.fetch_prometheus_metric", return_value=30):
        result = await get_new_replica_count(config, 2)
        assert result == 1
    with patch("unip_autoscaler.functions.fetch_prometheus_metric", return_value=90):
        result = await get_new_replica_count(config, 2)
        assert result is None


@pytest.mark.asyncio
async def test_autoscale_target(get_simple_scaling_config):
    config = get_simple_scaling_config()
    with (
        patch(
            "unip_autoscaler.functions.get_deployment_from_config"
        ) as get_deployment_from_config,
        patch("unip_autoscaler.functions.fetch_prometheus_metric", return_value=80),
        patch("unip_autoscaler.functions.has_cooldown", return_value=False),
        patch("unip_autoscaler.functions.scale_deployment") as scale,
        patch("unip_autoscaler.functions.set_scaling_timestamp"),
    ):
        get_deployment_from_config.return_value.spec.replicas = 1
        get_deployment_from_config.return_value.metadata.name = "test-deploy"
        get_deployment_from_config.return_value.metadata.namespace = "pu-test-pa-test"

        await autoscale_target(config)

        scale.assert_awaited_once()
