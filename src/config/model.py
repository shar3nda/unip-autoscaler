from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class TargetKind(str, Enum):
    DEPLOYMENT = "deployment"
    SERVICE = "service"


class Target(BaseModel):
    kind: TargetKind
    name: str
    namespace: str


class Operator(str, Enum):
    LESS_THAN = "lt"
    GREATER_THAN = "gt"


class Condition(BaseModel):
    metric: str
    operator: Operator
    value: float


class ConditionSet(BaseModel):
    allConditions: List[Condition] = Field(default_factory=list)
    anyCondition: List[Condition] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_conditions(cls, v):
        if not v.allConditions and not v.anyCondition:
            raise ValueError("either allConditions or anyCondition must be set")
        if v.allConditions and v.anyCondition:
            raise ValueError(
                "allConditions and anyCondition cannot be set at the same time"
            )
        return v


class Transition(BaseModel):
    nextState: int
    conditions: ConditionSet


class State(BaseModel):
    replicas: int
    transitions: List[Transition] = Field(default_factory=list)


class StatesConfig(BaseModel):
    states: List[State]


class PrometheusMetric(BaseModel):
    name: str
    query: str


class ScalingOptions(BaseModel):
    cpuTimeWindow: int = 300
    memoryTimeWindow: int = 300
    cooldown: int
    hibernationEnabled: bool


class ScalingConfig(BaseModel):
    target: Target
    states: List[State]
    prometheusMetrics: List[PrometheusMetric] = Field(default_factory=list)
    scalingOptions: ScalingOptions

    def get_current_state(self, current_replicas: int) -> State:
        for state in self.states:
            if state.replicas == current_replicas:
                return state
        raise ValueError(f"state with {current_replicas=} not defined")

    def get_state_by_number(self, state_number: int) -> State:
        for state in self.states:
            if state_number == state.replicas:
                return state
        raise ValueError(f"state with {state_number=} not defined")

    def get_metric_by_name(self, name: str) -> Optional[PrometheusMetric]:
        if not self.prometheusMetrics:
            return None
        for metric in self.prometheusMetrics:
            if metric.name == name:
                return metric
        return None
