from kubernetes import client
from kubernetes.client import V1Deployment, ApiException, V1Service, V1Ingress
import re
import base64

from .lock import get_resource_lock
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
from .__init__ import coreV1API, appsV1Api, networkingV1Api


async def get_deployment(name: str, namespace: str):
    return await appsV1Api.read_namespaced_deployment(
        name=name,
        namespace=namespace
    )


async def check_readiness_probe(dep: V1Deployment, srvc: V1Service):
        lock = await get_resource_lock(dep.metadata.namespace, dep.metadata.name, "deployment")
        async with lock:
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
                    print("CONTAINER_NAME: ", container.name)
                    patch_body = {
                        "spec": {
                            "template": {
                                "spec": {
                                    "containers": [{
                                        "name": container.name, # Это используется как идентификатор, а не меняет имя контейнера
                                        "readinessProbe": readiness_probe
                                    }]
                                }
                            }
                        }
                    }
                    await appsV1Api.patch_namespaced_deployment(
                        name=dep.metadata.name,
                        namespace=dep.metadata.namespace,
                        body=patch_body
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


async def get_deployment_by_service(service: V1Service):
    service_labels = service.spec.selector  # Получаем метки из селектора сервиса

    if not service_labels:
        raise ValueError("Service is missing labels")

    # Ищем деплойменты в том же namespace
    deployments = await appsV1Api.list_namespaced_deployment(service.metadata.namespace)

    matching_deployments = list(filter(
        lambda dep: dep.spec.selector.match_labels.get(AUTOSCALER_APP_SELECTOR_NAME) == service_labels.get(
            AUTOSCALER_APP_SELECTOR_NAME),
        deployments.items
    ))

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
                        if path.backend.service and path.backend.service.name == service_name:
                            return ingress


async def get_hibernated_service(srvc: V1Service):
    hibernatedService = None

    if srvc is not None and srvc.spec.type == 'ClusterIP':
        lock = await get_resource_lock(srvc.metadata.namespace, srvc.metadata.name, "service")
        async with lock:
            try:
                hibernatedService = await coreV1API.read_namespaced_service(
                    name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
                    namespace=srvc.metadata.namespace
                )
            # except ApiException as e:
            #     if e.status == 404:
            #         print("EXCEPTION TYPE: APIEXCEPTION")
            #         hibernatedService = await create_hibernated_service(srvc)
            except Exception as e:
                if type(e).__name__ == "ApiException" and e.status == 404:
                    print("CREATING HIBERNATED SERVICE")
                    hibernatedService = await create_hibernated_service(srvc)
                #print("get_hibernated_service EXCEPTION TYPE: ",type(e).__name__)
                else:
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
    lock = await get_resource_lock(dep.metadata.namespace, dep.metadata.name, "deployment")

    async with lock:
        try:
            patch_body = {"spec": {"replicas": replicas}}
            await appsV1Api.patch_namespaced_deployment(
                name=dep.metadata.name,
                namespace=dep.metadata.namespace,
                body=patch_body
            )
        except ApiException as e:
            print(f"Scaling Deployment error: {e}")
        except Exception as e:
            print(f"Error: {e}")
        print("Deployment Scaled Successfully")
        return ""

async def hibernate_by_service(namespace: str, service: str):
    service = await get_service(service, namespace)
    print("SERVICE TYPE: ", service.spec.type)
    if service.spec.type == "ExternalName":
        print("ExternalName service encountered")
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
        print("Ingress not found, ", namespace, " is probably already hibernated")
        return ""
    hibernatedService = await get_hibernated_service(service)
    updated_rules = ingress.spec.rules
    for rule in updated_rules:
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
    new_annotations = {
        "nginx.ingress.kubernetes.io/configuration-snippet": nginxConfig,
        "nginx.ingress.kubernetes.io/rewrite-target": None
    }
    lock = await get_resource_lock(namespace, ingress.metadata.name, "ingress")
    async with lock:
        await patch_ingress(
            namespace=namespace,
            ingName=ingress.metadata.name,
            new_annotations=new_annotations,
            updated_rules=updated_rules
        )

    return await scale_deployment(dep=deployment, replicas=0)


async def patch_ingress(namespace: str, ingName: str, new_annotations: dict, updated_rules):
    print("PATCHING INGRESS")
    ingress_patch = {
        "metadata": {
            "annotations": new_annotations
        },
        "spec": {
            "rules": updated_rules
        }
    }
    return await networkingV1Api.patch_namespaced_ingress(
        name=ingName,
        namespace=namespace,
        body=ingress_patch
    )


async def wakeup_ingress(namespace: str, serviceName: str, ingName: str, rewriteRule: str):
    lock = await get_resource_lock(namespace, ingName, "ingress")
    async with lock:
        print("wakeup_ingress")
        ingress = await networkingV1Api.read_namespaced_ingress(namespace=namespace, name=ingName)

        updated_rules = ingress.spec.rules
        for rule in updated_rules:
            for path in rule.http.paths:
                if path.backend.service.name == serviceName + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX:
                    path.backend.service.name = serviceName

        nginxConfig = ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"]
        pattern = '{}.*?{}'.format(f"#{AUTOSCALER_HEADERS_PREFIX}", f"#{AUTOSCALER_HEADERS_SUFFIX}")
        nginxConfig = re.sub(pattern, '', nginxConfig, flags=re.DOTALL).strip()
        new_annotations = {
            "nginx.ingress.kubernetes.io/configuration-snippet": nginxConfig,
            "nginx.ingress.kubernetes.io/rewrite-target": rewriteRule
        }

        await patch_ingress(namespace=namespace, ingName=ingName, new_annotations=new_annotations,
                            updated_rules=updated_rules)
        print("INGRESS PATCHED")


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

