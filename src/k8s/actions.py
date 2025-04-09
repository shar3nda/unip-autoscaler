import base64
import re

from kubernetes_asyncio.client import (
    ApiException,
    V1Deployment,
    V1ObjectMeta,
    V1Service,
    V1ServiceSpec,
)

from src.k8s.k8s_client import k8s
from src.k8s.resolver import (
    get_deployment,
    get_deployment_by_service,
    get_hibernated_service,
    get_ingress_by_service,
    get_service,
    get_service_by_deployment,
)
from src.settings import (
    AUTOSCALER_HEADERS_PREFIX,
    AUTOSCALER_HEADERS_SUFFIX,
    AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
    AUTOSCALER_SERVICE_EXTERNAL_NAME,
)
from src.utils.logger import logger
from src.utils.resource_lock import get_resource_lock


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


async def create_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == "ClusterIP":
        metadata = V1ObjectMeta(
            name=srvc.metadata.name + AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
        )
        spec = V1ServiceSpec(
            type="ExternalName", external_name=AUTOSCALER_SERVICE_EXTERNAL_NAME
        )
        hibernatedService = V1Service(metadata=metadata, spec=spec)
        hibernatedService = await k8s.coreV1API.create_namespaced_service(
            namespace=srvc.metadata.namespace, body=hibernatedService
        )
    return hibernatedService


async def hibernate(deployment: V1Deployment, service: V1Service, namespace: str):
    ingress = await get_ingress_by_service(service)
    if ingress is None and deployment is not None and service is not None:
        logger.info(f"Ingress not found, {namespace} is probably already hibernated")
        return ""
    try:
        hibernatedService = await get_hibernated_service(service)
    except ApiException as e:
        if e.status == 404:
            hibernatedService = await create_hibernated_service(service)
        else:
            logger.info(f"Error: {e}")
            return ""
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


async def hibernate_by_deployment(name: str, namespace: str):
    deployment = await get_deployment(name=name, namespace=namespace)
    service = await get_service_by_deployment(deployment)
    return await hibernate(deployment, service, namespace)


async def hibernate_by_service(namespace: str, service: str):
    service = await get_service(service, namespace)
    logger.info(f"SERVICE TYPE: {service.spec.type}")
    if service.spec.type == "ExternalName":
        logger.info("ExternalName service encountered")
        return ""
    deployment = await get_deployment_by_service(service)
    return await hibernate(deployment, service, namespace)
