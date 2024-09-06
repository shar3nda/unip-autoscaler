from kubernetes_asyncio import client, config  # меняем на асинхронную версию
from kubernetes.config import ConfigException

try:
    await config.load_incluster_config()
except ConfigException as e:
    await config.load_kube_config()

coreV1API = client.CoreV1Api()
appsV1Api = client.AppsV1Api()
networkingV1Api = client.NetworkingV1Api()
