from typing import Optional

import aiohttp
from jinja2 import Environment, Template, meta

from src.config.model import ScalingConfig
from src.k8s.resolver import get_deployment_from_config, get_service_from_config
from src.settings import HIBERNATION_TIMEOUT_SECONDS, PROMETHEUS_URL
from src.utils.logger import logger


async def render_query_template(config: ScalingConfig, query: str) -> str:
    """
    Рендерит шаблон запроса к Prometheus.
    """

    values = {
        "DEPLOYMENT_NAME": None,
        "SERVICE_NAME": None,
        "NAMESPACE": None,
        "HIBERNATION_TIMEOUT_SECONDS": None,
        # TODO: implement this
        # "SERVICE_API_INGRESS_NAME": None,
        # "FILES_API_INGRESS_NAME": None,
    }

    env = Environment()
    ast = env.parse(query)
    # get variables from query
    variables = meta.find_undeclared_variables(ast)

    for v in variables:
        if v == "DEPLOYMENT_NAME":
            values["DEPLOYMENT_NAME"] = (
                await get_deployment_from_config(config)
            ).metadata.name
        elif v == "SERVICE_NAME":
            values["SERVICE_NAME"] = (
                await get_service_from_config(config)
            ).metadata.name
        elif v == "NAMESPACE":
            values["NAMESPACE"] = config.target.namespace
        elif v == "HIBERNATION_TIMEOUT_SECONDS":
            values["HIBERNATION_TIMEOUT_SECONDS"] = HIBERNATION_TIMEOUT_SECONDS
        else:
            raise ValueError(f"unknown variable: {v}")

    logger.debug(f"rendering template {query} with {values=}")
    template = Template(query.strip())

    return template.render(values)


def get_memory_query(deployment_name: str, time_window=300):
    return (
        "avg (avg_over_time(container_memory_working_set_bytes{"
        f'pod=~"{deployment_name}-.*",container!=""'
        "}"
        f"[{time_window}s:])) / 1024 / 1024"
    )


def get_cpu_query(deployment_name: str, time_window=300):
    return (
        "sum(rate(container_cpu_usage_seconds_total{"
        f'pod=~"{deployment_name}-.*",container!=""'
        "}"
        f"[{time_window}s])) by (pod) * 100"
    )


async def fetch_prometheus_metric(query: str) -> Optional[float]:
    """
    Функция для запроса метрики из Prometheus.
    Ожидается, что метрика возвращает одно вещественное число.
    """
    async with aiohttp.ClientSession() as session:
        logger.debug(f"prometheus query: {query}")
        async with session.get(PROMETHEUS_URL, params={"query": query}) as response:
            response_text = await response.text()
            if response.status != 200:
                logger.error(f"prometheus error: {response_text}")
                return None
            logger.debug(f"prometheus response: {response_text}")

            data = await response.json()
            if data["status"] == "success":
                result = data["data"]["result"]
                if len(result) != 1:
                    logger.error(
                        f"expected single metric in prometheus response, found {result}"
                    )
                    return None
                metric_value = result[0].get("value")
                if not metric_value:
                    logger.error(f"no metric value found in {result=}")
                return float(metric_value[1])
            else:
                logger.error(f"prometheus error: {data['error']}")
                return None
