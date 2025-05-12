from __future__ import annotations

from enum import Enum
from typing import List, Optional, TypedDict

from pydantic import BaseModel, Field, model_validator


class TargetKind(str, Enum):
    DEPLOYMENT = "deployment"
    SERVICE = "service"


class Target(BaseModel):
    kind: TargetKind = Field(
        ...,
        description="Тип объекта масштабирования (deployment или service)",
    )
    name: str = Field(..., description="Имя целевого объекта масштабирования")
    namespace: str = Field(
        ..., description="Пространство имен, в котором находится целевой объект"
    )


class Operator(str, Enum):
    LESS_THAN = "lt"
    GREATER_THAN = "gt"


class Condition(BaseModel):
    metric: str = Field(
        ..., description="Имя метрики Prometheus (например, cpu, memory)"
    )
    operator: Operator = Field(
        ..., description="Оператор сравнения: 'lt' (меньше) или 'gt' (больше)"
    )
    value: float = Field(
        ..., description="Целевое значение метрики для выполнения условия"
    )


class ConditionSet(BaseModel):
    allConditions: List[Condition] = Field(
        default_factory=list,
        description="Список условий, все из которых должны быть выполнены (логическое И)",
    )
    anyCondition: List[Condition] = Field(
        default_factory=list,
        description="Список условий, из которых должно быть выполнено хотя бы одно (логическое ИЛИ)",
    )

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
    nextState: int = Field(..., description="Количество реплик в следующем состоянии")
    conditions: ConditionSet = Field(
        ..., description="Набор условий, при выполнении которых произойдет переход"
    )


class State(BaseModel):
    replicas: int = Field(..., description="Количество реплик в данном состоянии")
    transitions: List[Transition] = Field(
        default_factory=list,
        description="Список переходов в другие состояния из текущего",
    )


class PrometheusMetric(BaseModel):
    name: str = Field(..., description="Уникальное имя пользовательской метрики")
    query: str = Field(..., description="Запрос PromQL для получения значения метрики")


class ScalingOptions(BaseModel):
    cpuTimeWindow: int = Field(
        300, description="Период усреднения метрики CPU в секундах"
    )
    memoryTimeWindow: int = Field(
        300, description="Период усреднения метрики RAM в секундах"
    )
    cooldown: int = Field(
        ...,
        description="Период (в секундах), в течение которого масштабирование отключено после предыдущего изменения",
    )
    hibernationEnabled: bool = Field(
        ...,
        description="Включение/отключение гибернации приложения",
    )


class ScalingConfig(BaseModel):
    target: Target = Field(..., description="Объект масштабирования")
    states: List[State] = Field(..., description="Список состояний масштабирования")
    prometheusMetrics: List[PrometheusMetric] = Field(
        default_factory=list,
        description="Список пользовательских метрик Prometheus, используемых в условиях",
    )
    scalingOptions: ScalingOptions = Field(
        ..., description="Дополнительные параметры масштабирования"
    )

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


class ScalingConfigWithMeta(TypedDict):
    """
    A helper class containing custom resource metadata for a ScalingConfig.
    """

    config: ScalingConfig
    uid: str
    resource_version: str
    name: str
    namespace: str
