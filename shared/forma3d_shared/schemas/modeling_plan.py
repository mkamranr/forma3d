"""Modeling plan schema (spec §8.4). Produced by the planning agent in Phase 4/5; its
`ordered_operations` must still pass the DSL validator before anything executes."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from forma3d_shared import DSL_SCHEMA_VERSION
from forma3d_shared.schemas.common import ObjectId, SafeName, SafeText, StrictModel, Token
from forma3d_shared.schemas.modeling_operation import Operation

TaskType = Literal["CREATE", "MODIFY", "DELETE"]


class PlanComponent(StrictModel):
    object_id: ObjectId
    name: SafeName
    component_type: Token
    description: SafeText = ""
    parent_id: Optional[ObjectId] = None


class PlanRelationship(StrictModel):
    subject_id: ObjectId
    relation: Literal["attached_to", "on_top_of", "inside", "aligned_with", "child_of", "symmetric_with"]
    object_id: ObjectId


class ModelingPlan(StrictModel):
    plan_id: Token
    schema_version: Literal["1.0"] = DSL_SCHEMA_VERSION
    task_type: TaskType
    scene_id: str = Field(max_length=64)
    units: Literal["meters"] = "meters"
    components: list[PlanComponent] = Field(default_factory=list, max_length=500)
    relationships: list[PlanRelationship] = Field(default_factory=list, max_length=1000)
    ordered_operations: list[Operation] = Field(min_length=1, max_length=2000)
    expected_result: SafeText = ""
    validation_criteria: list[SafeText] = Field(default_factory=list, max_length=50)
