from datetime import datetime
from math import isclose
from typing import Optional

from src.autoscaler.cooldown import has_cooldown, set_scaling_timestamp
from src.autoscaler.metrics import (
    fetch_prometheus_metric,
    get_cpu_query,
    get_memory_query,
    render_query_template,
)
from src.config.model import Condition, Operator, ScalingConfig, State
from src.k8s.actions import (
    hibernate_by_deployment,
    scale_deployment,
)
from src.k8s.resolver import get_deployment_from_config
from src.settings import HIBERNATION_QUERY, NAMESPACE_REGEX
from src.utils.logger import logger


async def check_condition(config: ScalingConfig, condition: Condition) -> bool:
    if condition.metric == "cpu":
        query = get_cpu_query(config.target.name, config.scalingOptions.cpuTimeWindow)
    elif condition.metric == "memory":
        query = get_memory_query(
            config.target.name, config.scalingOptions.memoryTimeWindow
        )
    else:
        query = config.get_metric_by_name(condition.metric).query
        query = await render_query_template(config, query)
        if query is None:
            raise ValueError(f"Unknown metric: {condition.metric}")

    value = await fetch_prometheus_metric(query)
    if value is None:
        return False

    if condition.operator == Operator.LESS_THAN:
        return value < condition.value

    if condition.operator == Operator.GREATER_THAN:
        return value > condition.value

    raise ValueError(f"Unknown operator: {condition.operator}")


async def get_new_state(config: ScalingConfig, current_state: State) -> State:
    for transition in current_state.transitions:
        all_conditions = transition.conditions.allConditions
        any_condition = transition.conditions.anyCondition
        if all_conditions:
            for condition in all_conditions:
                if not await check_condition(config, condition):
                    return None
            return config.get_state_by_number(transition.nextState)
        else:
            for condition in any_condition:
                if await check_condition(config, condition):
                    return config.get_state_by_number(transition.nextState)
    return None


async def get_new_replica_count(
    config: ScalingConfig, current_replica_count: int
) -> Optional[int]:
    """
    Возвращает новое количество реплик в соответствии с правилами масштабирования.
    """
    # hibernation is disabled only on api query
    if current_replica_count == 0:
        return 0

    try:
        current_state = config.get_current_state(current_replica_count)
    except ValueError as e:
        logger.error(f"error getting current state: {e}")
        return None

    try:
        new_state = await get_new_state(config, current_state)
    except ValueError as e:
        logger.error(f"error getting new state: {e}")
        return None

    if new_state is None:
        logger.debug("no transition rules matched")
        return None

    return new_state.replicas


async def is_hibernation_needed(config: ScalingConfig) -> bool:
    if not config.scalingOptions.hibernationEnabled:
        logger.debug(f"hibernation is disabled for {config.target=}")
        return False

    if not NAMESPACE_REGEX.match(config.target.namespace):
        logger.info(
            f"{config.target.namespace=} does not match regex, skipping scaling"
        )
        return False

    query = await render_query_template(config, HIBERNATION_QUERY)
    metric_value = await fetch_prometheus_metric(query)

    if not isclose(metric_value, 0):
        return False

    return True


async def autoscale_target(
    config: ScalingConfig,
    **kwargs,  # needed to pass extra arguments with APScheduler
) -> None:
    """Масштабирует объект в соответствии с конфигурацией."""

    if not NAMESPACE_REGEX.match(config.target.namespace):
        logger.info(
            f"{config.target.namespace=} does not match regex, skipping scaling"
        )
        return

    if await has_cooldown(config.target, config.scalingOptions.cooldown):
        logger.info("cooldown is active, skip scaling")
        return

    target = config.target

    deployment = await get_deployment_from_config(config)
    if deployment is None:
        logger.error(f"deployment to autoscale not found for {target}")
        return

    if await is_hibernation_needed(config):
        logger.info(f"hibernating target {config.target}")
        await hibernate_by_deployment(
            config.target.name,
            config.target.namespace,
        )
        await set_scaling_timestamp(target, datetime.now())
        return

    current_replica_count = deployment.spec.replicas
    logger.debug(f"{current_replica_count=}")

    new_replica_count = await get_new_replica_count(config, current_replica_count)
    logger.debug(f"{new_replica_count=}")
    if new_replica_count is None:
        return

    if new_replica_count == current_replica_count:
        logger.info(f"{new_replica_count=} == {current_replica_count=}, skip scaling")
        return

    logger.info(f"scaling {target} from {current_replica_count} to {new_replica_count}")
    await scale_deployment(deployment, new_replica_count)
    await set_scaling_timestamp(target, datetime.now())
    return
