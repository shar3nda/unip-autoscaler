from kubernetes_asyncio.client import (
    ApiException,
    V1Deployment,
    V1Probe,
    V1Service,
    V1TCPSocketAction,
)

from src.k8s.k8s_client import k8s
from src.settings import (
    AUTOSCALER_APP_SELECTOR_NAME,
    AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD,
    AUTOSCALER_READINESS_PROBE_INITIAL_DELAY,
    AUTOSCALER_READINESS_PROBE_PERIOD,
)
from src.utils.logger import logger
from src.utils.resource_lock import get_resource_lock


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
                readiness_probe = V1Probe(
                    tcp_socket=V1TCPSocketAction(port=srvc.spec.ports[0].target_port),
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


async def is_any_pod_ready(srvc: V1Service):
    try:
        selectors = srvc.spec.selector
        label_selector = ",".join(
            [f"{key}={value}" for key, value in selectors.items()]
        )
        pods = await k8s.coreV1Api.list_namespaced_pod(
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


async def is_service_ready(namespace: str, name: str):
    try:
        service = await k8s.coreV1Api.read_namespaced_service(
            namespace=namespace, name=name
        )
        return await is_any_pod_ready(service)
    except ApiException as e:
        logger.info(f"ApiException: {e}")
        return False
