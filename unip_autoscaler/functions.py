from kubernetes import client
from kubernetes.client import V1Deployment, ApiException, V1Service
import re
import base64
from .settings import (
    AUTOSCALER_APP_SELECTOR_NAME,
    AUTOSCALER_READINESS_PROBE_INITIAL_DELAY,
    AUTOSCALER_READINESS_PROBE_PERIOD,
    AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD,
    AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
    AUTOSCALER_SERVICE_EXTERNAL_NAME,
    AUTOSCALER_HEADERS_PREFIX,
    AUTOSCALER_HEADERS_SUFFIX,
)
from .settings import coreV1API, appsV1Api, networkingV1Api


async def get_deployment(name: str, namespace: str):
    return await appsV1Api.read_namespaced_deployment(
        name=name,
        namespace=namespace
    )


async def check_readiness_probe(dep: V1Deployment, srvc: V1Service):
    if dep and dep.spec.template.metadata.labels[AUTOSCALER_APP_SELECTOR_NAME] == srvc.spec.selector[
        AUTOSCALER_APP_SELECTOR_NAME]:
        container = dep.spec.template.spec.containers[0]
        if not container.readiness_probe:
            readiness_probe = client.V1Probe(
                tcp_socket=client.V1TCPSocketAction(
                    port=srvc.spec.ports[0].target_port
                ),
                initial_delay_seconds=AUTOSCALER_READINESS_PROBE_INITIAL_DELAY,
                period_seconds=AUTOSCALER_READINESS_PROBE_PERIOD,
                failure_threshold=AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD
            )
            container.readiness_probe = readiness_probe
            await appsV1Api.patch_namespaced_deployment(
                name=dep.metadata.name,
                namespace=dep.metadata.namespace,
                body=dep
            )


async def get_service(name: str, namespace: str):
    return await coreV1API.read_namespaced_service(
        name=name,
        namespace=namespace
    )


async def get_service_by_deployment(dep: V1Deployment):
    dep_labels = dep.spec.selector.match_labels
    srvcs = await coreV1API.list_namespaced_service(dep.metadata.namespace)
    srvcs = list(filter(
        lambda srvc: srvc.spec.type == 'ClusterIP'
                     and srvc.spec.selector is not None
                     and srvc.spec.selector[AUTOSCALER_APP_SELECTOR_NAME] == dep_labels[AUTOSCALER_APP_SELECTOR_NAME],
        srvcs.items
    ))

    return srvcs[0]  # Предполагаем, что найден хотя бы один сервис


async def get_ingress_by_service(srvc: V1Service):
    ingresses = await networkingV1Api.list_namespaced_ingress(srvc.metadata.namespace)
    service_name = srvc.metadata.name

    for ingress in ingresses.items:
        if ingress.spec.rules:
            for rule in ingress.spec.rules:
                if rule.http and rule.http.paths:
                    for path in rule.http.paths:
                        if path.backend.service and path.backend.service.name == service_name:
                            return ingress


async def get_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == 'ClusterIP':
        try:
            hibernatedService = await coreV1API.read_namespaced_service(
                name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
                namespace=srvc.metadata.namespace
            )
        except ApiException as e:
            if e.status == 404:
                hibernatedService = await create_hibernated_service(srvc)
        except Exception as e:
            print(f"Error: {e}")
    return hibernatedService


async def create_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == 'ClusterIP':
        metadata = client.V1ObjectMeta(
            name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
        )
        spec = client.V1ServiceSpec(
            type="ExternalName",
            external_name=AUTOSCALER_SERVICE_EXTERNAL_NAME
        )
        hibernatedService = client.V1Service(
            metadata=metadata,
            spec=spec
        )
        hibernatedService = await coreV1API.create_namespaced_service(
            namespace=srvc.metadata.namespace,
            body=hibernatedService
        )
    return hibernatedService


async def scale_deployment(dep: V1Deployment, replicas: int):
    try:
        dep.spec.replicas = replicas
        await appsV1Api.patch_namespaced_deployment(
            name=dep.metadata.name,
            namespace=dep.metadata.namespace,
            body=dep
        )
    except ApiException as e:
        print(f"Scaling Deployment error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    return ""


async def hibernate_deployment(name: str, namespace: str):
    deployment = await get_deployment(name=name, namespace=namespace)
    service = await get_service_by_deployment(deployment)
    ingress = await get_ingress_by_service(service)
    hibernatedService = await get_hibernated_service(service)

    for rule in ingress.spec.rules:
        for path in rule.http.paths:
            if path.backend.service.name == service.metadata.name:
                path.backend.service.name = hibernatedService.metadata.name

    rewriteTarget = ingress.metadata.annotations.get("nginx.ingress.kubernetes.io/rewrite-target")
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
        rewriteTarget)
    nginxConfig = ingress.metadata.annotations.get(
        "nginx.ingress.kubernetes.io/configuration-snippet") + additionalHeaders
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = nginxConfig
    del ingress.metadata.annotations["nginx.ingress.kubernetes.io/rewrite-target"]

    await networkingV1Api.replace_namespaced_ingress(
        name=ingress.metadata.name,
        namespace=ingress.metadata.namespace,
        body=ingress
    )

    return await scale_deployment(dep=deployment, replicas=0)


async def wakeup_ingress(namespace: str, serviceName: str, ingName: str, rewriteRule: str):
    print("wakeup_ingress")
    ingress = await networkingV1Api.read_namespaced_ingress(namespace=namespace, name=ingName)
    for rule in ingress.spec.rules:
        for path in rule.http.paths:
            if path.backend.service.name == serviceName + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX:
                path.backend.service.name = serviceName

    nginxConfig = ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"]
    pattern = '{}.*?{}'.format(f"#{AUTOSCALER_HEADERS_PREFIX}", f"#{AUTOSCALER_HEADERS_SUFFIX}")
    nginxConfig = re.sub(pattern, '', nginxConfig, flags=re.DOTALL).strip()
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = nginxConfig
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/rewrite-target"] = rewriteRule

    return await networkingV1Api.replace_namespaced_ingress(
        name=ingress.metadata.name,
        namespace=ingress.metadata.namespace,
        body=ingress
    )


async def is_service_ready(namespace: str, name: str):
    try:
        service = await coreV1API.read_namespaced_service(namespace=namespace, name=name)
        return await is_any_pod_ready(service)
    except ApiException as e:
        print(f"ApiException: {e}")
        return False


async def is_any_pod_ready(srvc: V1Service):
    try:
        selectors = srvc.spec.selector
        label_selector = ",".join([f"{key}={value}" for key, value in selectors.items()])
        pods = await coreV1API.list_namespaced_pod(
            namespace=srvc.metadata.namespace,
            label_selector=label_selector
        )
        for pod in pods.items:
            pod_ready = all(container.ready for container in pod.status.container_statuses)
            if pod_ready:
                return True
        return False
    except ApiException as e:
        return False

