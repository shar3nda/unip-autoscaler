import time
from functools import wraps

from kubernetes_asyncio import client, config

from src.self_metrics.metrics import (
    K8S_ERRORS_TOTAL,
    K8S_REQUEST_DURATION,
    K8S_REQUESTS_TOTAL,
)


def _k8s_method_wrapper(method_name):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            K8S_REQUESTS_TOTAL.labels(method=method_name).inc()
            start = time.time()
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                K8S_ERRORS_TOTAL.labels(method=method_name).inc()
                raise
            finally:
                duration = time.time() - start
                K8S_REQUEST_DURATION.labels(method=method_name).observe(duration)

        return wrapper

    return decorator


def _wrap_api(api, api_name: str):
    for attr_name in dir(api):
        if attr_name.startswith("_"):
            continue
        attr = getattr(api, attr_name)
        if callable(attr):
            wrapped = _k8s_method_wrapper(f"{api_name}.{attr_name}")(attr)
            setattr(api, attr_name, wrapped)


class KubernetesClient:
    def __init__(self):
        self.coreV1Api = None
        self.customObjectsApi = None
        self.appsV1Api = None
        self.networkingV1Api = None

    async def init_client(self):
        try:
            config.load_incluster_config()
        except config.ConfigException:
            await config.load_kube_config()

        self.coreV1Api = client.CoreV1Api()
        self.customObjectsApi = client.CustomObjectsApi()
        self.appsV1Api = client.AppsV1Api()
        self.networkingV1Api = client.NetworkingV1Api()

        _wrap_api(self.coreV1Api, "coreV1Api")
        _wrap_api(self.customObjectsApi, "customObjectsApi")
        _wrap_api(self.appsV1Api, "appsV1Api")
        _wrap_api(self.networkingV1Api, "networkingV1Api")


k8s = KubernetesClient()
