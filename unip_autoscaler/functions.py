import asyncio
import base64
import re

import aiohttp
import yaml
from kubernetes import client
from kubernetes.client import ApiException, V1Deployment, V1Service
import jsonschema

from .__init__ import appsV1Api, coreV1API, networkingV1Api
from .autoscaling_config import SCALING_CONFIG_SCHEMA, ScalingConfig
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
    PROMETHEUS_URL,
)


async def get_deployment(name: str, namespace: str):
    return await appsV1Api.read_namespaced_deployment(name=name, namespace=namespace)


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
                await appsV1Api.patch_namespaced_deployment(
                    name=dep.metadata.name,
                    namespace=dep.metadata.namespace,
                    body=patch_body,
                )


async def get_service(name: str, namespace: str):
    return await coreV1API.read_namespaced_service(name=name, namespace=namespace)


async def get_service_by_deployment(dep: V1Deployment):
    dep_labels = dep.spec.selector.match_labels
    srvcs = await coreV1API.list_namespaced_service(dep.metadata.namespace)
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
    deployments = await appsV1Api.list_namespaced_deployment(service.metadata.namespace)

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
    ingresses = await networkingV1Api.list_namespaced_ingress(srvc.metadata.namespace)
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
                hibernatedService = await coreV1API.read_namespaced_service(
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
        hibernatedService = await coreV1API.create_namespaced_service(
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
            await appsV1Api.patch_namespaced_deployment(
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
            "nginx.ingress.kubernetes.io/configuration-snippet"
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
    return await networkingV1Api.patch_namespaced_ingress(
        name=ingName, namespace=namespace, body=ingress_patch
    )


async def wakeup_ingress(
    namespace: str, serviceName: str, ingName: str, rewriteRule: str
):
    lock = await get_resource_lock(namespace, ingName, "ingress")
    async with lock:
        logger.info("wakeup_ingress")
        ingress = await networkingV1Api.read_namespaced_ingress(
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
        service = await coreV1API.read_namespaced_service(
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
        pods = await coreV1API.list_namespaced_pod(
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


async def fetch_prometheus_metric(query) -> float | None:
    """
    Функция для запроса метрики из Prometheus.
    Ожидается, что метрика возвращает одно вещественное число.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(PROMETHEUS_URL, params={"query": query}) as response:
            if response.status == 200:
                logger.error(f"prometheus error: {response.text()}")
                return None

            data = await response.json()
            if data["status"] == "success":
                result = data["data"]["result"]
                if result:
                    value = float(result[0]["value"][1])
                    return value
                else:
                    logger.warning("no data received in metric")
                    return None
            else:
                logger.error(f"prometheus error: {data['error']}")
                return None


async def read_file_async(file_path):
    content = await asyncio.to_thread(read_file, file_path)
    return content


def read_file(file_path):
    with open(file_path, "r") as file:
        return file.read()


async def load_autoscaler_configs() -> list[ScalingConfig]:
    result = []

    spec = await read_file_async("autoscaler-config.yaml")
    configs = list(yaml.safe_load(spec))

    validator = jsonschema.Draft202012Validator(SCALING_CONFIG_SCHEMA)

    for config in configs:
        try:
            validator.validate(config)
            result.append(config)
        except jsonschema.ValidationError as e:
            logger.error(f"Invalid config: {e}")

    return result


def get_cpu_ram_query(deployment_name: str, time_window=300):
    queries = {
        "cpu": "sum(rate(container_cpu_usage_seconds_total{"
        f'pod=~"{deployment_name}-.*"'
        "}"
        f"[{time_window}s])) by (pod) * 100",
        "memory": "avg by (pod) (avg_over_time(container_memory_working_set_bytes{"
        f'pod=~"{deployment_name}-.*"'
        "}"
        f"[{time_window}s:])) / 1024 / 1024",
    }
    return queries


async def get_deployment_from_config(config: ScalingConfig) -> V1Deployment:
    target = config["target"]

    if target["kind"] == "deployment":
        return target["name"]

    if target["kind"] == "service":
        service = await get_service(target["name"], target["namespace"])
        deployment = await get_deployment_by_service(service)
        return deployment


async def get_service_from_config(config: ScalingConfig) -> V1Service:
    target = config["target"]

    if target["kind"] == "service":
        return target["name"]

    if target["kind"] == "deployment":
        deployment = await get_deployment_from_config(config)
        return await get_service_by_deployment(deployment)


async def get_replicas_delta(config: ScalingConfig) -> int:
    """
    Возвращает изменение количества реплик в соответствии с правилами масштабирования.
    Не учитывает параметры minReplicas и maxReplicas.
    """
    if config["scalingRules"].get("prometheusMetric"):
        rule = config["scalingRules"]["prometheusMetric"]
        prometheus_query = rule["query"]
        value = fetch_prometheus_metric(prometheus_query)
        logger.debug(f"Prometheus metric: {value}")
        if value is None:
            return 0

        if value > rule["thresholdUp"]:
            return rule["stepUp"]
        elif value < rule["thresholdDown"]:
            return rule["stepDown"]
        else:
            return 0
    else:
        deployment = await get_deployment_from_config(config)
        deployment_name = deployment.metadata.name
        queries = get_cpu_ram_query(deployment_name, config["timeWindow"])
        cpu_value = await fetch_prometheus_metric(queries["cpu"])
        memory_value = await fetch_prometheus_metric(queries["memory"])
        logger.debug(f"CPU: {cpu_value}%, RAM: {memory_value}M")

        rule_up = config["scalingRules"]["scaleUp"]
        rule_down = config["scalingRules"]["scaleDown"]

        if (
            cpu_value > rule_up["cpuThreshold"]
            and memory_value > rule_up["memoryThreshold"]
        ):
            return rule_up["step"]
        elif (
            cpu_value < rule_down["cpuThreshold"]
            and memory_value < rule_down["memoryThreshold"]
        ):
            return rule_down["step"]
        else:
            return 0


async def autoscale_target(config: ScalingConfig) -> None:
    """Масштабирует объект в соответствии с конфигурацией."""

    target = config["target"]

    replicas_delta = await get_replicas_delta(config)
    if replicas_delta == 0:
        logger.info(f"replicas_delta is 0, skip scaling {target}")
        return

    # TODO: maybe cache deployment and service objects
    deployment = await get_deployment_from_config(config)

    if deployment is None:
        logger.error(f"deployment to autoscale not found for {target}")
        return

    current_replicas = deployment.spec.replicas

    new_replicas = current_replicas + replicas_delta
    new_replicas = min(
        config["maxReplicas"],
        max(
            config["minReplicas"],
            new_replicas,
        ),
    )

    if new_replicas == current_replicas:
        logger.info(f"replica count is already desired for {target}")
        return

    if new_replicas != 0:
        logger.info(f"scaling {target} from {current_replicas} to {new_replicas}")
        # await scale_deployment(deployment, new_replicas)
        await asyncio.sleep(10)

    service = await get_service_from_config(config)
    if service is None:
        logger.error(f"service to autoscale not found for {target}")
        return

    logger.info(f"hibernating {target} from {current_replicas}")
    # await hibernate(deployment, service, deployment.metadata.namespace)
    await asyncio.sleep(10)
