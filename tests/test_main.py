from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main import AlertRequestModel, AnnotationsModel, alert


@pytest.mark.asyncio
async def test_alert_triggers_hibernation(get_simple_scaling_config):
    alert_payload = AlertRequestModel(
        commonAnnotations=AnnotationsModel(
            namespace="pu-test-pa-test", service="my-service"
        )
    )

    mock_service = MagicMock()
    mock_service.metadata.name = "my-service"
    mock_service.metadata.namespace = "pu-test-pa-test"

    config_default = get_simple_scaling_config()
    config_hibernation = get_simple_scaling_config(hibernation_enabled=True)

    with (
        patch(
            "unip_autoscaler.main.get_service_from_config",
            return_value=mock_service,
        ),
        patch(
            "unip_autoscaler.main.hibernate_by_service",
            new_callable=AsyncMock,
        ) as mock_hibernate,
    ):
        with patch(
            "unip_autoscaler.main.config_mgr.get_configs",
            return_value=[config_default],
        ):
            await alert(alert_payload)
            mock_hibernate.assert_not_called()
            mock_hibernate.reset_mock()
        with patch(
            "unip_autoscaler.main.config_mgr.get_configs",
            return_value=[config_hibernation],
        ):
            await alert(alert_payload)
            mock_hibernate.assert_called_once_with(
                mock_service.metadata.namespace,
                mock_service.metadata.name,
            )
