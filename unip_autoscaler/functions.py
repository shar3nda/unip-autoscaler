import base64
import re
from datetime import datetime
from typing import List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import aiofiles
import aiohttp
import yaml
from fastapi import Request
from jinja2 import Environment, Template, meta
from kubernetes import client
from kubernetes.client import ApiException, V1Deployment, V1Service
from pydantic import ValidationError

from .autoscaling_config import (
    Condition,
    ScalingConfig,
    State,
)
from .cooldown import has_cooldown, set_scaling_timestamp
from .k8s_client import k8s
from .lock import get_resource_lock
from .logger import logger
from .settings import (
    AUTOSCALER_APP_SELECTOR_NAME,
    AUTOSCALER_HEADERS_PREFIX,
    AUTOSCALER_HEADERS_SUFFIX,
    AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
    AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD,
    AUTOSCALER_READINESS_PROBE_INITIAL_DELAY,
    AUTOSCALER_READINESS_PROBE_PERIOD,
    AUTOSCALER_SERVICE_EXTERNAL_NAME,
    AUTOSCALER_SPEC_FILE,
    NAMESPACE_REGEX,
    PROMETHEUS_URL,
)


def set_https_prefix(url: str) -> str:
    return url.replace("http://", "https://", 1)


def get_retry_redirect_url(request: Request, retries: int) -> str:
    url_parts = list(urlparse(str(request.url)))
    query = dict(parse_qsl(url_parts[4]))
    query.update({"retries": retries})
    url_parts[4] = urlencode(query)
    return urlunparse(url_parts)


async def get_deployment(name: str, namespace: str):
    return await k8s.appsV1Api.read_namespaced_deployment(
        name=name, namespace=namespace
    )


async def check_readiness_probe(dep: V1Deployment, srvc: V1Service):
    lock = await get_resource_lock(
        dep.metadata.namespace,
        dep.metadata.name,
        "deployment",
    )
    async with lock:
        if (
            dep
            and dep.spec.template.metadata.labels[AUTOSCALER_APP_SELECTOR_NAME]
            == srvc.spec.selector[AUTOSCALER_APP_SELECTOR_NAME]
        ):
            container = dep.spec.template.spec.containers[0]
            if not container.readiness_probe:
                readiness_probe = client.V1Probe(
                    tcp_socket=client.V1TCPSocketAction(
                        port=srvc.spec.ports[0].target_port
                    ),
                    initial_delay_seconds=AUTOSCALER_READINESS_PROBE_INITIAL_DELAY,
                    period_seconds=AUTOSCALER_READINESS_PROBE_PERIOD,
                    failure_threshold=AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD,
                )
                logger.info(f"CONTAINER_NAME: {container.name}")
                patch_body = {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": container.name,  # Это используется как идентификатор, а не меняет имя контейнера
                                        "readinessProbe": readiness_probe,
                                    }
                                ]
                            }
                        }
                    }
                }
                await k8s.appsV1Api.patch_namespaced_deployment(
                    name=dep.metadata.name,
                    namespace=dep.metadata.namespace,
                    body=patch_body,
                )


async def get_service(name: str, namespace: str):
    return await k8s.coreV1API.read_namespaced_service(name=name, namespace=namespace)


async def get_service_by_deployment(dep: V1Deployment):
    dep_labels = dep.spec.selector.match_labels
    srvcs = await k8s.coreV1API.list_namespaced_service(dep.metadata.namespace)
    srvcs = list(
        filter(
            lambda srvc: srvc.spec.type == "ClusterIP"
            and srvc.spec.selector is not None
            and srvc.spec.selector[AUTOSCALER_APP_SELECTOR_NAME]
            == dep_labels[AUTOSCALER_APP_SELECTOR_NAME],
            srvcs.items,
        )
    )

    return srvcs[0]  # Предполагаем, что найден хотя бы один сервис


async def get_deployment_by_service(service: V1Service):
    service_labels = service.spec.selector  # Получаем метки из селектора сервиса

    if not service_labels:
        raise ValueError("Service is missing labels")

    # Ищем деплойменты в том же namespace
    deployments = await k8s.appsV1Api.list_namespaced_deployment(
        service.metadata.namespace
    )

    matching_deployments = list(
        filter(
            lambda dep: dep.spec.selector.match_labels.get(AUTOSCALER_APP_SELECTOR_NAME)
            == service_labels.get(AUTOSCALER_APP_SELECTOR_NAME),
            deployments.items,
        )
    )

    if not matching_deployments:
        raise ValueError("Deployment not found")

    return matching_deployments[0]


async def get_ingress_by_service(srvc: V1Service):
    ingresses = await k8s.networkingV1Api.list_namespaced_ingress(
        srvc.metadata.namespace
    )
    service_name = srvc.metadata.name

    for ingress in ingresses.items:
        if ingress.spec.rules:
            for rule in ingress.spec.rules:
                if rule.http and rule.http.paths:
                    for path in rule.http.paths:
                        if (
                            path.backend.service
                            and path.backend.service.name == service_name
                        ):
                            return ingress


async def get_hibernated_service(srvc: V1Service):
    hibernatedService = None

    if srvc is not None and srvc.spec.type == "ClusterIP":
        lock = await get_resource_lock(
            srvc.metadata.namespace, srvc.metadata.name, "service"
        )
        async with lock:
            try:
                hibernatedService = await k8s.coreV1API.read_namespaced_service(
                    name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
                    namespace=srvc.metadata.namespace,
                )
            # except ApiException as e:
            #     if e.status == 404:
            #         logger.info("EXCEPTION TYPE: APIEXCEPTION")
            #         hibernatedService = await create_hibernated_service(srvc)
            except Exception as e:
                if type(e).__name__ == "ApiException" and e.status == 404:
                    logger.info("CREATING HIBERNATED SERVICE")
                    hibernatedService = await create_hibernated_service(srvc)
                # logger.info("get_hibernated_service EXCEPTION TYPE: ",type(e).__name__)
                else:
                    logger.info(f"Error: {e}")
        return hibernatedService


async def create_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == "ClusterIP":
        metadata = client.V1ObjectMeta(
            name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
        )
        spec = client.V1ServiceSpec(
            type="ExternalName", external_name=AUTOSCALER_SERVICE_EXTERNAL_NAME
        )
        hibernatedService = client.V1Service(metadata=metadata, spec=spec)
        hibernatedService = await k8s.coreV1API.create_namespaced_service(
            namespace=srvc.metadata.namespace, body=hibernatedService
        )
    return hibernatedService


async def scale_deployment(dep: V1Deployment, replicas: int):
    lock = await get_resource_lock(
        dep.metadata.namespace, dep.metadata.name, "deployment"
    )

    async with lock:
        try:
            patch_body = {"spec": {"replicas": replicas}}
            await k8s.appsV1Api.patch_namespaced_deployment(
                name=dep.metadata.name,
                namespace=dep.metadata.namespace,
                body=patch_body,
            )
        except ApiException as e:
            logger.info(f"Scaling Deployment error: {e}")
        except Exception as e:
            logger.info(f"Error: {e}")
        logger.info("Deployment Scaled Successfully")
        return ""


async def hibernate_by_service(namespace: str, service: str):
    service = await get_service(service, namespace)
    logger.info(f"SERVICE TYPE: {service.spec.type}")
    if service.spec.type == "ExternalName":
        logger.info("ExternalName service encountered")
        return ""
    deployment = await get_deployment_by_service(service)
    return await hibernate(deployment, service, namespace)


async def hibernate_by_deployment(name: str, namespace: str):
    deployment = await get_deployment(name=name, namespace=namespace)
    service = await get_service_by_deployment(deployment)
    return await hibernate(deployment, service, namespace)


async def hibernate(deployment: V1Deployment, service: V1Service, namespace: str):
    ingress = await get_ingress_by_service(service)
    if ingress is None and deployment is not None and service is not None:
        logger.info(f"Ingress not found, {namespace} is probably already hibernated")
        return ""
    hibernatedService = await get_hibernated_service(service)
    updated_rules = ingress.spec.rules
    for rule in updated_rules:
        for path in rule.http.paths:
            if path.backend.service.name == service.metadata.name:
                path.backend.service.name = hibernatedService.metadata.name

    rewriteTarget = ingress.metadata.annotations.get(
        "nginx.ingress.kubernetes.io/rewrite-target"
    )
    rewriteTarget = base64.b64encode(f"{rewriteTarget}".encode("utf-8")).decode("utf-8")

    additionalHeaders = f"""
#{AUTOSCALER_HEADERS_PREFIX}
proxy_set_header AUTOSCALER_APP_NAMESPACE %s;
proxy_set_header AUTOSCALER_APP_DEPLOYMENT %s;
proxy_set_header AUTOSCALER_APP_SERVICE %s;
proxy_set_header AUTOSCALER_APP_INGRESS %s;
proxy_set_header AUTOSCALER_APP_INGRESS_REWRITE %s;
#{AUTOSCALER_HEADERS_SUFFIX}""" % (
        namespace,
        deployment.metadata.name,
        service.metadata.name,
        ingress.metadata.name,
        rewriteTarget,
    )
    nginxConfig = (
        ingress.metadata.annotations.get(
            "nginx.ingress.kubernetes.io/configuration-snippet",
            "",
        )
        + additionalHeaders
    )
    new_annotations = {
        "nginx.ingress.kubernetes.io/configuration-snippet": nginxConfig,
        "nginx.ingress.kubernetes.io/rewrite-target": None,
    }
    lock = await get_resource_lock(namespace, ingress.metadata.name, "ingress")
    async with lock:
        await patch_ingress(
            namespace=namespace,
            ingName=ingress.metadata.name,
            new_annotations=new_annotations,
            updated_rules=updated_rules,
        )

    return await scale_deployment(dep=deployment, replicas=0)


async def patch_ingress(
    namespace: str, ingName: str, new_annotations: dict, updated_rules
):
    logger.info("PATCHING INGRESS")
    ingress_patch = {
        "metadata": {"annotations": new_annotations},
        "spec": {"rules": updated_rules},
    }
    return await k8s.networkingV1Api.patch_namespaced_ingress(
        name=ingName, namespace=namespace, body=ingress_patch
    )


async def wakeup_ingress(
    namespace: str, serviceName: str, ingName: str, rewriteRule: str
):
    lock = await get_resource_lock(namespace, ingName, "ingress")
    async with lock:
        logger.info("wakeup_ingress")
        ingress = await k8s.networkingV1Api.read_namespaced_ingress(
            namespace=namespace, name=ingName
        )

        updated_rules = ingress.spec.rules
        for rule in updated_rules:
            for path in rule.http.paths:
                if (
                    path.backend.service.name
                    == serviceName + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX
                ):
                    path.backend.service.name = serviceName

        nginxConfig = ingress.metadata.annotations[
            "nginx.ingress.kubernetes.io/configuration-snippet"
        ]
        pattern = "{}.*?{}".format(
            f"#{AUTOSCALER_HEADERS_PREFIX}", f"#{AUTOSCALER_HEADERS_SUFFIX}"
        )
        nginxConfig = re.sub(pattern, "", nginxConfig, flags=re.DOTALL).strip()
        new_annotations = {
            "nginx.ingress.kubernetes.io/configuration-snippet": nginxConfig,
            "nginx.ingress.kubernetes.io/rewrite-target": rewriteRule,
        }

        await patch_ingress(
            namespace=namespace,
            ingName=ingName,
            new_annotations=new_annotations,
            updated_rules=updated_rules,
        )
        logger.info("INGRESS PATCHED")


async def is_service_ready(namespace: str, name: str):
    try:
        service = await k8s.coreV1API.read_namespaced_service(
            namespace=namespace, name=name
        )
        return await is_any_pod_ready(service)
    except ApiException as e:
        logger.info(f"ApiException: {e}")
        return False


async def is_any_pod_ready(srvc: V1Service):
    try:
        selectors = srvc.spec.selector
        label_selector = ",".join(
            [f"{key}={value}" for key, value in selectors.items()]
        )
        pods = await k8s.coreV1API.list_namespaced_pod(
            namespace=srvc.metadata.namespace, label_selector=label_selector
        )
        for pod in pods.items:
            pod_ready = all(
                container.ready for container in pod.status.container_statuses
            )
            if pod_ready:
                return True
        return False
    except ApiException as e:
        logger.info(f"ApiException: {e}")
        return False


async def fetch_prometheus_metric(query) -> Optional[float]:
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


async def load_autoscaler_configs() -> List[ScalingConfig]:
    result = []

    async with aiofiles.open(AUTOSCALER_SPEC_FILE) as f:
        spec = await f.read()
    configs = list(yaml.safe_load_all(spec))

    for config in configs:
        try:
            instance = ScalingConfig(**config)
            result.append(instance)
        except ValidationError as e:
            logger.error(f"Invalid config: {e}")
            continue

    return result


def get_cpu_query(deployment_name: str, time_window=300):
    return (
        "sum(rate(container_cpu_usage_seconds_total{"
        f'pod=~"{deployment_name}-.*",container!=""'
        "}"
        f"[{time_window}s])) by (pod) * 100"
    )


def get_memory_query(deployment_name: str, time_window=300):
    return (
        "avg (avg_over_time(container_memory_working_set_bytes{"
        f'pod=~"{deployment_name}-.*",container!=""'
        "}"
        f"[{time_window}s:])) / 1024 / 1024"
    )


async def get_deployment_from_config(config: ScalingConfig) -> V1Deployment:
    target = config.target

    if target.kind == "deployment":
        return await get_deployment(target.name, target.namespace)

    if target["kind"] == "service":
        service = await get_service(target.name, target.namespace)
        return await get_deployment_by_service(service)


async def get_service_from_config(config: ScalingConfig) -> V1Service:
    target = config.target

    if target["kind"] == "service":
        return await get_service(target.name, target.namespace)

    if target["kind"] == "deployment":
        deployment = await get_deployment_from_config(config)
        return await get_service_by_deployment(deployment)


async def render_query_template(config: ScalingConfig, query: str) -> str:
    """
    Рендерит шаблон запроса к Prometheus.
    """

    values = {
        "DEPLOYMENT_NAME": None,
        "SERVICE_NAME": None,
        "NAMESPACE": None,
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
        else:
            raise ValueError(f"unknown variable: {v}")

    logger.debug(f"rendering template {query} with {values=}")
    template = Template(query.strip())

    return template.render(values)


async def check_condition(config: ScalingConfig, condition: Condition) -> bool:
    if condition.metric == "cpu":
        query = get_cpu_query(config.target.name, config.scalingOptions.cpuTimeWindow)
    elif condition.metric == "memory":
        query = get_memory_query(
            config.target.name, config.scalingOptions.memoryTimeWindow
        )
    else:
        query = config.get_metric_by_name(condition.metric).query
        query = await render_query_template(config, query)
        if query is None:
            raise ValueError(f"Unknown metric: {condition.metric}")

    value = await fetch_prometheus_metric(query)
    if value is None:
        return False

    if condition.operator == "<":
        return value < condition.value

    if condition.operator == ">":
        return value > condition.value

    raise ValueError(f"Unknown operator: {condition.operator}")


async def get_new_state(config: ScalingConfig, current_state: State) -> State:
    for transition in current_state.transitions:
        all_of = transition.conditions.allOf
        any_of = transition.conditions.anyOf
        if all_of:
            for condition in all_of:
                if not await check_condition(config, condition):
                    return None
            return config.get_state_by_number(transition.nextState)
        else:
            for condition in any_of:
                if await check_condition(config, condition):
                    return config.get_state_by_number(transition.nextState)
    return None


async def get_new_replica_count(
    config: ScalingConfig, current_replica_count: int
) -> Optional[int]:
    """
    Возвращает новое количество реплик в соответствии с правилами масштабирования.
    """
    # hibernation is disabled only on api query
    if current_replica_count == 0:
        return 0

    try:
        current_state = config.get_current_state(current_replica_count)
    except ValueError as e:
        logger.error(f"error getting current state: {e}")
        return None

    try:
        new_state = await get_new_state(config, current_state)
    except ValueError as e:
        logger.error(f"error getting new state: {e}")
        return None

    if new_state is None:
        logger.debug("no transition rules matched")
        return None

    return new_state.replicas


async def autoscale_target(config: ScalingConfig) -> None:
    """Масштабирует объект в соответствии с конфигурацией."""

    if not NAMESPACE_REGEX.match(config.target.namespace):
        logger.info(
            f"{config.target.namespace=} does not match regex, skipping scaling"
        )
        return

    if await has_cooldown(config.target, config.scalingOptions.cooldown):
        logger.info("cooldown is active, skip scaling")
        return

    target = config.target

    deployment = await get_deployment_from_config(config)
    if deployment is None:
        logger.error(f"deployment to autoscale not found for {target}")
        return

    current_replica_count = deployment.spec.replicas
    logger.debug(f"{current_replica_count=}")

    new_replica_count = await get_new_replica_count(config, current_replica_count)
    logger.debug(f"{new_replica_count=}")
    if new_replica_count is None:
        return

    logger.info(f"scaling {target} from {current_replica_count} to {new_replica_count}")
    await scale_deployment(deployment, new_replica_count)
    await set_scaling_timestamp(target, datetime.now())
    return
