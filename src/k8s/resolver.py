from kubernetes_asyncio.client import V1Deployment, V1Service

from src.config.model import ScalingConfig
from src.k8s.k8s_client import k8s
from src.settings import (
    AUTOSCALER_APP_SELECTOR_NAME,
    AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
)
from src.utils.resource_lock import (
    get_resource_lock,
)


async def get_service(name: str, namespace: str):
    return await k8s.coreV1API.read_namespaced_service(name=name, namespace=namespace)


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


async def get_deployment(name: str, namespace: str):
    return await k8s.appsV1Api.read_namespaced_deployment(
        name=name, namespace=namespace
    )


async def get_deployment_from_config(config: ScalingConfig) -> V1Deployment:
    target = config.target

    if target.kind == "deployment":
        return await get_deployment(target.name, target.namespace)

    if target["kind"] == "service":
        service = await get_service(target.name, target.namespace)
        return await get_deployment_by_service(service)


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


async def get_service_from_config(config: ScalingConfig) -> V1Service:
    target = config.target

    if target["kind"] == "service":
        return await get_service(target.name, target.namespace)

    if target["kind"] == "deployment":
        deployment = await get_deployment_from_config(config)
        return await get_service_by_deployment(deployment)


async def get_hibernated_service(srvc: V1Service):
    if srvc is None or srvc.spec.type != "ClusterIP":
        return None
    lock = await get_resource_lock(
        srvc.metadata.namespace, srvc.metadata.name, "service"
    )
    hibernatedService = None
    async with lock:
        hibernatedService = await k8s.coreV1API.read_namespaced_service(
            name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
            namespace=srvc.metadata.namespace,
        )
    return hibernatedService


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
