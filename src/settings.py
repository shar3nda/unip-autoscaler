import os
import re

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEV_DIR = os.path.join(ROOT_DIR, "dev")

if os.path.exists(os.path.join(DEV_DIR)):
    from dotenv import load_dotenv

    load_dotenv(os.path.join(DEV_DIR, ".env"))

AUTOSCALER_APP_SELECTOR_NAME = os.environ.get(
    "AUTOSCALER_APP_SELECTOR_NAME",
    "app",
)
AUTOSCALER_HIBERNATED_SERVICE_SUFFIX = os.environ.get(
    "AUTOSCALER_HIBERNATED_SERVICE_SUFFIX",
    "-hibernated",
)
AUTOSCALER_SERVICE_EXTERNAL_NAME = os.environ.get(
    "AUTOSCALER_SERVICE_EXTERNAL_NAME",
    "unip-autoscaler.unip-system-autoscaler",
)
AUTOSCALER_HEADERS_PREFIX = os.environ.get(
    "AUTOSCALER_HEADERS_PREFIX",
    "<AUTOSCALER HEADERS",
)
AUTOSCALER_HEADERS_SUFFIX = os.environ.get(
    "AUTOSCALER_HEADERS_SUFFIX",
    "AUTOSCALER HEADERS>",
)
AUTOSCALER_READINESS_TIMEOUT = int(
    os.environ.get(
        "AUTOSCALER_READINESS_TIMEOUT",
        "1",
    )
)
AUTOSCALER_READINESS_LIMIT = int(
    os.environ.get(
        "AUTOSCALER_READINESS_LIMIT",
        "10",
    )
)
AUTOSCALER_READINESS_PROBE_INITIAL_DELAY = int(
    os.environ.get(
        "AUTOSCALER_READINESS_PROBE_INITIAL_DELAY",
        "1",
    )
)
AUTOSCALER_READINESS_PROBE_PERIOD = int(
    os.environ.get(
        "AUTOSCALER_READINESS_PROBE_PERIOD",
        "1",
    )
)
AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD = int(
    os.environ.get(
        "AUTOSCALER_READINESS_PROBE_FAILURE_THRESHOLD",
        "300",
    )
)
AUTOSCALER_SPEC_FILE = os.environ.get(
    "AUTOSCALER_SPEC_FILE",
    "/etc/unip-autoscaler/spec.yaml",
)
AUTOSCALER_CHECK_INTERVAL = int(
    os.environ.get(
        "AUTOSCALER_CHECK_INTERVAL",
        "300",
    )
)
PROMETHEUS_URL = os.environ.get(
    "PROMETHEUS_URL",
    "http://prometheus.unip-system-prometheus.svc.cluster.local:9090/prometheus/api/v1/query",
)
HIBERNATION_TIMEOUT_SECONDS = os.environ.get("HIBERNATION_TIMEOUT_SECONDS", "300")
HIBERNATION_QUERY = os.environ.get(
    "HIBERNATION_QUERY",
    'nginx_ingress_controller_requests{exported_namespace="{{ NAMESPACE }}"}[{{ HIBERNATION_TIMEOUT_SECONDS }}s]',
)
DEBUG = os.environ.get("DEBUG") == "true"
AUTOSCALER_NAMESPACE_REGEX = os.environ.get("AUTOSCALER_NAMESPACE_REGEX", "^pu-.*$")
NAMESPACE_REGEX = re.compile(AUTOSCALER_NAMESPACE_REGEX)
