from prometheus_client import Counter, Histogram

PROMETHEUS_REQUESTS_TOTAL = Counter(
    "autoscaler_prometheus_requests_total", "Total requests to Prometheus API"
)
PROMETHEUS_ERRORS_TOTAL = Counter(
    "autoscaler_prometheus_request_errors_total", "Total Prometheus API errors"
)

K8S_REQUESTS_TOTAL = Counter(
    "autoscaler_kubernetes_requests_total",
    "Total requests to Kubernetes API",
    ["method"],
)
K8S_ERRORS_TOTAL = Counter(
    "autoscaler_kubernetes_request_errors_total",
    "Total Kubernetes API errors",
    ["method"],
)

PROMETHEUS_REQUEST_DURATION = Histogram(
    "autoscaler_prometheus_request_duration_seconds",
    "Duration of Prometheus API requests",
)
K8S_REQUEST_DURATION = Histogram(
    "autoscaler_kubernetes_request_duration_seconds",
    "Duration of Kubernetes API requests",
    ["method"],
)
