"""Forma3D shared contracts.

The modeling DSL defined here is the reliability and security boundary between LLM output and
Blender: anything that reaches Blender must parse as an `OperationBatch` and pass
`validate_batch()`. Never execute LLM-produced code."""

DSL_SCHEMA_VERSION = "1.0"
INVENTORY_SCHEMA_VERSION = "1.0"
