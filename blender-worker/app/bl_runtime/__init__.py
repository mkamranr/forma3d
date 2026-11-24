"""Code that runs *inside* Blender (`blender --background --python bl_runtime/main.py`).

Constraints: stdlib + bpy/bmesh/mathutils only (Blender bundles its own Python, without
pydantic). Input is an already-validated DSL batch written by the trusted worker service; this
package still refuses any operation type that is not in its registry. Nothing here evaluates
strings as code.
"""
