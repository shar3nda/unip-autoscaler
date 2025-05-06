#!/usr/bin/env python3

import json
import os
from typing import Any

import jsonref
import yaml

from src.config.model import ScalingConfig

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CRDS_TEMPLATE = """apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: scalingconfigs.autoscaler.unified-platform.cs.hse.ru
spec:
  group: autoscaler.unified-platform.cs.hse.ru
  names:
    kind: ScalingConfig
    plural: scalingconfigs
    shortNames:
    - scalecfg
    singular: scalingconfig
  scope: Namespaced
  versions:
  - name: v1alpha1
    schema:
      openAPIV3Schema:
        properties:
          spec:
        type: object
    served: true
    storage: true
"""


pydantic_schema = ScalingConfig.model_json_schema()
schema_clean: dict[str, Any] = json.loads(
    json.dumps(jsonref.replace_refs(pydantic_schema), indent=4)
)
schema_clean.pop("$defs", None)

crd = yaml.safe_load(CRDS_TEMPLATE)
crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"] = (
    schema_clean
)

with open(os.path.join(ROOT_DIR, "k8s", "crds.yaml"), "w") as f:
    f.write(yaml.dump(crd))

print(f"CRD created at {ROOT_DIR}k8s/crds.yaml")
