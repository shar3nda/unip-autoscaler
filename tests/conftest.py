import pytest

from src.config.model import (
    Condition,
    ConditionSet,
    ScalingConfig,
    ScalingOptions,
    State,
    Target,
    Transition,
)


@pytest.fixture
def get_simple_scaling_config():
    def _make_config(hibernation_enabled=False):
        return ScalingConfig(
            target=Target(
                kind="deployment", name="test-deploy", namespace="pu-test-pa-test"
            ),
            scalingOptions=ScalingOptions(
                cooldown=0,
                cpuTimeWindow=60,
                memoryTimeWindow=60,
                hibernationEnabled=hibernation_enabled,
            ),
            states=[
                State(
                    replicas=1,
                    transitions=[
                        Transition(
                            nextState=2,
                            conditions=ConditionSet(
                                allConditions=[
                                    Condition(metric="cpu", operator="gt", value=75)
                                ]
                            ),
                        )
                    ],
                ),
                State(
                    replicas=2,
                    transitions=[
                        Transition(
                            nextState=1,
                            conditions=ConditionSet(
                                allConditions=[
                                    Condition(metric="cpu", operator="lt", value=50)
                                ]
                            ),
                        )
                    ],
                ),
            ],
        )

    return _make_config
