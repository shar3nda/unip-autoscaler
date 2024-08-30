from kubernetes import client, config
from kubernetes.config import ConfigException


try:
    config.load_incluster_config()
except ConfigException as e:
    config.load_kube_config()
coreV1API = client.CoreV1Api()
appsV1Api = client.AppsV1Api()
networkingV1Api = client.NetworkingV1Api()