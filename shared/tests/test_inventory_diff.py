from forma3d_shared.inventory_diff import diff_inventories
from forma3d_shared.schemas.scene_object import SceneInventory


def inv(objects, materials=()):
    return SceneInventory.model_validate({"objects": list(objects), "materials": list(materials)})


def obj(oid, **kw):
    base = {"object_id": oid, "name": oid.title(), "location": [0, 0, 0], "dimensions": [1, 1, 1],
            "material_ids": ["oak"]}
    return {**base, **kw}


OAK = {"material_id": "oak", "name": "Oak", "base_color": "#c99563"}


def test_diff_added_removed_changed_and_unchanged():
    a = inv([obj("top"), obj("leg"), obj("lamp")], [OAK])
    b = inv([obj("top", location=[0, 0, 0.00001]), obj("leg", dimensions=[1, 1, 2], name="Tall Leg"),
             obj("drawer")], [{**OAK, "base_color": "#C99563"}, {"material_id": "steel", "name": "Steel"}])
    d = diff_inventories(a, b)
    assert d["objects"]["added"] == [{"object_id": "drawer", "name": "Drawer"}]
    assert d["objects"]["removed"] == [{"object_id": "lamp", "name": "Lamp"}]
    assert d["objects"]["unchanged"] == 1  # float noise and colour case are not changes
    (leg,) = d["objects"]["changed"]
    assert leg["object_id"] == "leg" and set(leg["fields"]) == {"name", "dimensions"}
    assert leg["fields"]["dimensions"] == {"before": [1.0, 1.0, 1.0], "after": [1.0, 1.0, 2.0]}
    assert d["materials"]["added"] == [{"material_id": "steel", "name": "Steel"}]
    assert d["materials"]["changed"] == []


def test_identical_inventories_have_no_changes():
    a = inv([obj("top")], [OAK])
    d = diff_inventories(a, a)
    assert d["objects"] == {"added": [], "removed": [], "changed": [], "unchanged": 1}
