from kubernetes_asyncio import client, config


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


k8s = KubernetesClient()
