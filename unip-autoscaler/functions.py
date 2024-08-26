from kubernetes import client, config
from kubernetes.client import V1Deployment, V1DeploymentSpec, ApiException, V1Service
import settings
import re


def get_deployment(name:str, namespace:str):
    return appsV1Api.read_namespaced_deployment(
        name=name,
        namespace=namespace
    )


def get_service(name:str, namespace:str):
    return coreV1API.read_namespaced_service(
        name = name,
        namespace = namespace
    )


def get_service_by_deployment(dep: V1Deployment):
    dep_labels = dep.spec.selector.match_labels
    srvcs = coreV1API.list_namespaced_service(dep.metadata.namespace)
    srvcs = list(filter(
        lambda srvc: srvc.spec.type == 'ClusterIP'
                     and srvc.spec.selector is not None
                     and srvc.spec.selector[settings.AUTOSCALER_APP_SELECTOR_NAME] == dep_labels[settings.AUTOSCALER_APP_SELECTOR_NAME],
        srvcs.items
    ))

    return srvcs[0]

def get_ingress_by_service(srvc: V1Service):
    ingresses = networkingV1Api.list_namespaced_ingress(srvc.metadata.namespace)
    service_name = srvc.metadata.name

    for ingress in ingresses.items:
        if ingress.spec.rules:
            for rule in ingress.spec.rules:
                if rule.http and rule.http.paths:
                    for path in rule.http.paths:
                        if path.backend.service and path.backend.service.name == service_name:
                            return ingress


def get_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == 'ClusterIP':
        try:
            hibernatedService = coreV1API.read_namespaced_service(
                name = srvc.metadata.name + settings.AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
                namespace = srvc.metadata.namespace
            )
        except ApiException as e:
            if e.status == 404:
                hibernatedService = create_hibernated_service(srvc)
        except Exception as e:
            print(f"Error: {e}")
    return hibernatedService

def create_hibernated_service(srvc: V1Service):
    hibernatedService = None
    if srvc is not None and srvc.spec.type == 'ClusterIP':
        metadata = client.V1ObjectMeta(
            name = srvc.metadata.name + settings.AUTOSCALER_HIBERNATED_SERVICE_SUFFIX,
        )
        spec = client.V1ServiceSpec(
            type = "ExternalName",
            external_name = settings.AUTOSCALER_SERVICE_EXTERNAL_NAME
        )
        hibernatedService = client.V1Service(
            metadata = metadata,
            spec = spec
        )
        hibernatedService = coreV1API.create_namespaced_service(
            namespace = srvc.metadata.namespace,
            body = hibernatedService
        )
    return hibernatedService

def scale_deployment(dep:V1Deployment, replicas:int):
    try:
        dep.spec.replicas = replicas
        appsV1Api.patch_namespaced_deployment(
            name = dep.metadata.name,
            namespace = dep.metadata.namespace,
            body = dep
        )
    except ApiException as e:
        print(f"Scaling Deployment error: {e}")
    except Exception as e:
        print(f"Error: {e}")


def hibernate_deployment(name:str, namespace:str):
    deployment = get_deployment(name = name, namespace = namespace)
    service = get_service_by_deployment(deployment)
    ingress = get_ingress_by_service(service)
    hibernatedService = get_hibernated_service(service)

    for rule in ingress.spec.rules:
        for path in rule.http.paths:
            if path.backend.service.name == service.metadata.name:
                path.backend.service.name = hibernatedService.metadata.name

    rewriteTarget = ingress.metadata.annotations.get("nginx.ingress.kubernetes.io/rewrite-target")

    additionalHeaders = """#<AUTOSCALER HEADERS
proxy_set_header AUTOSCALER_APP_NAMESPACE %s;
proxy_set_header AUTOSCALER_APP_DEPLOYMENT %s;
proxy_set_header AUTOSCALER_APP_SERVICE %s;
proxy_set_header AUTOSCALER_APP_INGRESS %s;
proxy_set_header AUTOSCALER_APP_INGRESS_REWRITE %s;
#AUTOSCALER HEADERS>""" % (
                        namespace,
                        deployment.metadata.name,
                        service.metadata.name,
                        ingress.metadata.name,
                        rewriteTarget)

    nginxConfig = ingress.metadata.annotations.get("nginx.ingress.kubernetes.io/configuration-snippet") + additionalHeaders
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = nginxConfig
    del ingress.metadata.annotations["nginx.ingress.kubernetes.io/rewrite-target"]

    ingress = networkingV1Api.replace_namespaced_ingress(
        name = ingress.metadata.name,
        namespace = ingress.metadata.namespace,
        body = ingress
    )

    return scale_deployment(dep = deployment, replicas = 0)

def wakeup_ingress(namespace:str, serviceName:str, ingName:str, rewriteRule:str):
    ingress = networkingV1Api.read_namespaced_ingress(namespace = namespace, name = ingName)
    for rule in ingress.spec.rules:
        for path in rule.http.paths:
            if path.backend.service == serviceName + settings.AUTOSCALER_HIBERNATED_SERVICE_SUFFIX:
                path.backend.service.name = serviceName

    nginxConfig = ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"]
    nginxConfig = re.sub(r'{}.*?{}'.format(re.escape("#<AUTOSCALER HEADERS"), re.escape("#AUTOSCALER HEADERS>")), '', nginxConfig)
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/configuration-snippet"] = nginxConfig
    ingress.metadata.annotations["nginx.ingress.kubernetes.io/rewrite-target"] = rewriteRule

    return networkingV1Api.replace_namespaced_ingress(
        name = ingress.metadata.name,
        namespace = ingress.metadata.namespace,
        body = ingress
    )

def is_service_ready(namespace:str, name:str):
    try:
        service = coreV1API.read_namespaced_service(namespace = namespace, name = name)
        return is_any_pod_ready(service)
    except ApiException as e:
        print(f"ApiException: {e}")
        return False

def is_any_pod_ready(srvc: V1Service):
    try:
        selectors = srvc.spec.selector
        label_selector = ",".join([f"{key}={value}" for key, value in selectors.items()])
        for pod in coreV1API.list_namespaced_pod(
                namespace = srvc.metadata.namespace,
                label_selector  = label_selector
        ).items:
            pod_ready = all(container.ready for container in pod.status.container_statuses)
            if pod_ready:
                return True
        return False
    except ApiException as e:
        return False


config.load_kube_config()
coreV1API = client.CoreV1Api()
appsV1Api = client.AppsV1Api()
networkingV1Api = client.NetworkingV1Api()


if __name__ == "__main__":
    hibernate_deployment(
        name='module-example-mlcmp-deployment',
        namespace = 'pu-test-pa-module-example'
    )
