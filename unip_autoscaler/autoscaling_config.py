from typing import TypedDict

# TODO: мб использовать pydantic


class PrometheusRule(TypedDict):
    query: str
    thresholdUp: float
    thresholdDown: float
    stepUp: int
    stepDown: int


class CpuRamRule(TypedDict):
    step: int
    cpuThreshold: float | None = None
    memoryThreshold: float | None = None


class ScalingRules(TypedDict):
    prometheusMetric: PrometheusRule | None = None
    scaleUp: CpuRamRule | None = None
    scaleDown: CpuRamRule | None = None


class ScalingTarget(TypedDict):
    kind: str
    name: str
    namespace: str


class ScalingConfig(TypedDict):
    target: ScalingTarget
    scalingRules: ScalingRules
    timeWindow: int
    maxReplicas: int
    minReplicas: int
    cooldown: int


SCALING_CONFIG_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "target": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "name": {"type": "string"},
                "namespace": {"type": "string"},
            },
            "required": ["kind", "name", "namespace"],
        },
        "scalingRules": {
            "type": "object",
            "properties": {
                "prometheusMetric": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "thresholdUp": {"type": "number"},
                        "thresholdDown": {"type": "number"},
                        "stepUp": {"type": "integer"},
                        "stepDown": {"type": "integer"},
                    },
                    "required": [
                        "query",
                        "thresholdUp",
                        "thresholdDown",
                        "stepUp",
                        "stepDown",
                    ],
                },
                "scaleUp": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "integer"},
                        "cpuThreshold": {"type": ["number", "null"]},
                        "memoryThreshold": {"type": ["number", "null"]},
                    },
                    "required": ["step"],
                    "anyOf": [
                        {"required": ["cpuThreshold"]},
                        {"required": ["memoryThreshold"]},
                    ],
                },
                "scaleDown": {
                    "type": "object",
                    "properties": {
                        "step": {"type": "integer"},
                        "cpuThreshold": {"type": ["number", "null"]},
                        "memoryThreshold": {"type": ["number", "null"]},
                    },
                    "required": ["step"],
                    "anyOf": [
                        {"required": ["cpuThreshold"]},
                        {"required": ["memoryThreshold"]},
                    ],
                },
            },
            "oneOf": [
                {
                    "required": ["prometheusMetric"],
                    "not": {
                        "anyOf": [
                            {"required": ["scaleUp"]},
                            {"required": ["scaleDown"]},
                        ]
                    },
                },
                {
                    "required": ["scaleUp", "scaleDown"],
                    "not": {"required": ["prometheusMetric"]},
                },
            ],
        },
        # TODO: в прометее не нужен timeWindow, подумать, куда подвинуть этот параметр
        "timeWindow": {"type": "integer"},
        "maxReplicas": {"type": "integer"},
        "minReplicas": {"type": "integer"},
        "cooldown": {"type": "integer"},
    },
    "required": [
        "target",
        "scalingRules",
        "maxReplicas",
        "minReplicas",
        "cooldown",
    ],
}
