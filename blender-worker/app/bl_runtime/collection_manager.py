"""Collections addressed by stable collection ids."""

from __future__ import annotations

import bpy

from .context import PROP_COLLECTION_ID, OpError, RuntimeContext


def create_collection(ctx: RuntimeContext, op: dict) -> None:
    p = op["parameters"]
    if p["collection_id"] in ctx.collections:
        raise OpError("collection_exists", f"collection '{p['collection_id']}' already exists")
    col = bpy.data.collections.new(p["name"])
    col[PROP_COLLECTION_ID] = p["collection_id"]
    parent = ctx.collection(p["parent_collection_id"]) if p.get("parent_collection_id") else ctx.scene.collection
    parent.children.link(col)
    ctx.collections[p["collection_id"]] = col


def move_to_collection(ctx: RuntimeContext, op: dict) -> None:
    obj = ctx.obj(op["target_object_id"])
    target = ctx.collection(op["parameters"]["collection_id"])
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target.objects.link(obj)
    ctx.mark_modified(obj)
