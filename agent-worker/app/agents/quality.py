"""Quality Assurance Agent (deterministic).

Compares the inventory Blender actually produced (staged, not yet committed) with the previous
version and with the Modeling Agent's expectations:

- expected objects exist; deleted objects are gone; no unexpected objects appeared
- created/modified parts have the planned size, position, rotation, material and parent
- untouched objects and materials are unchanged (no collateral edits)
- meshes are non-degenerate
- declared relationships hold (on top of / under / attached / inside) and new parts are supported

Severity: `error` blocks the commit; `fixable` triggers the bounded Revision Agent and is reported
as a warning if revisions run out; `warning` is informational.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from forma3d_shared.schemas.scene_object import ObjectSnapshot, SceneInventory

from .inspection import Box, object_box, world_space
from .modeling import Expectations

Severity = Literal["error", "fixable", "warning"]
POS_TOL = 0.002  # meters
TOUCH_TOL = 0.005  # meters: parts closer than this count as touching
ROT_TOL = 0.5  # degrees


@dataclass
class Issue:
    code: str
    severity: Severity
    message: str
    object_id: str | None = None
    hint: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class QAReport:
    passed: bool
    checks: int
    issues: list[Issue]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def fixable(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "fixable"]

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "checks": self.checks, "issues": [asdict(i) for i in self.issues]}


def _close(a: Any, b: Any, tol: float) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b, strict=False))


def _size_close(a: Any, b: Any) -> bool:
    return all(abs(float(x) - float(y)) <= max(0.002, 0.01 * abs(float(y))) for x, y in zip(a, b, strict=False))


def _angle_close(a: Any, b: Any) -> bool:
    return all(abs(((float(x) - float(y)) + 180) % 360 - 180) <= ROT_TOL for x, y in zip(a, b, strict=False))


def _fmt(v: Any) -> str:
    return "[" + ", ".join(f"{float(x):.3f}" for x in v) + "]"


def _shift(o: ObjectSnapshot, dz: float = 0.0, d: tuple[float, float, float] | None = None) -> list[float]:
    delta = d or (0.0, 0.0, dz)
    return [round(o.location[i] + delta[i], 4) for i in range(3)]


class _Checker:
    def __init__(self, before: SceneInventory, after: SceneInventory, exp: Expectations) -> None:
        self.before = {o.object_id: o for o in before.objects}
        self.after = {o.object_id: o for o in after.objects}
        self.mats_before = {m.material_id: m for m in before.materials}
        self.mats_after = {m.material_id: m for m in after.materials}
        self.exp = exp
        self.issues: list[Issue] = []
        self.checks = 0

    def check(self, ok: bool, issue: Issue) -> None:
        self.checks += 1
        if not ok:
            self.issues.append(issue)

    def box(self, oid: str) -> Box:
        return object_box(self.after[oid])

    def solid(self, oid: str) -> bool:
        """Physical geometry. Lights and cameras float by design and occupy no space."""
        o = self.after.get(oid)
        return o is not None and o.type == "MESH" and o.visible

    # ------------------------------------------------------------------ existence
    def existence(self) -> None:
        for oid in self.exp.created:
            self.check(oid in self.after, Issue("missing_object", "error", f"'{oid}' was not created", oid))
        for oid in self.exp.deleted:
            self.check(oid not in self.after, Issue("not_deleted", "error", f"'{oid}' still exists", oid))
        unexpected = set(self.after) - set(self.before) - set(self.exp.created)
        self.check(
            not unexpected, Issue("unexpected_objects", "error", f"unexpected new objects: {sorted(unexpected)}")
        )
        vanished = set(self.before) - set(self.after) - self.exp.deleted
        self.check(not vanished, Issue("unexpected_deletion", "error", f"objects disappeared: {sorted(vanished)}"))

    # ------------------------------------------------------------------ planned values
    def planned(self) -> None:
        for oid, want in {**self.exp.created, **self.exp.modified}.items():
            o = self.after.get(oid)
            if o is None:
                continue
            if want.get("size") is not None:
                self.check(
                    _size_close(o.dimensions, want["size"]),
                    Issue(
                        "wrong_size",
                        "error",
                        f"'{oid}' size is {_fmt(o.dimensions)}, planned {_fmt(want['size'])}",
                        oid,
                    ),
                )
            if want.get("position") is not None:
                self.check(
                    _close(o.location, want["position"], POS_TOL),
                    Issue(
                        "wrong_position",
                        "error",
                        f"'{oid}' is at {_fmt(o.location)}, planned {_fmt(want['position'])}",
                        oid,
                    ),
                )
            if want.get("rotation") is not None:
                self.check(
                    _angle_close(o.rotation, want["rotation"]),
                    Issue(
                        "wrong_rotation",
                        "error",
                        f"'{oid}' rotation {_fmt(o.rotation)}, planned {_fmt(want['rotation'])}",
                        oid,
                    ),
                )
            if want.get("material"):
                self.check(
                    want["material"] in o.material_ids,
                    Issue("material_not_assigned", "error", f"'{oid}' does not use material '{want['material']}'", oid),
                )
            elif oid in self.exp.created and o.type == "MESH":
                self.check(bool(o.material_ids), Issue("no_material", "warning", f"'{oid}' has no material", oid))
            if "parent" in want:
                self.check(
                    o.parent_id == want["parent"],
                    Issue(
                        "wrong_parent", "error", f"'{oid}' parent is {o.parent_id!r}, planned {want['parent']!r}", oid
                    ),
                )
            if want.get("name"):
                self.check(o.name == want["name"], Issue("wrong_name", "error", f"'{oid}' is named {o.name!r}", oid))
            if "visible" in want:
                self.check(
                    o.visible == want["visible"],
                    Issue("wrong_visibility", "error", f"'{oid}' visibility is {o.visible}", oid),
                )
            if o.type == "MESH":
                flat_ok = sum(1 for d in o.dimensions if d <= 1e-6) <= 1
                self.check(
                    o.mesh.vertices > 0 and flat_ok,
                    Issue("degenerate_mesh", "error", f"'{oid}' has a degenerate mesh", oid),
                )
        for mid, want in {**self.exp.materials_created, **self.exp.materials_updated}.items():
            m = self.mats_after.get(mid)
            self.check(m is not None, Issue("missing_material", "error", f"material '{mid}' missing"))
            if m is None:
                continue
            if want.get("base_color"):
                self.check(
                    m.base_color.upper() == str(want["base_color"]).upper(),
                    Issue(
                        "wrong_color",
                        "error",
                        f"material '{mid}' colour is {m.base_color}, planned {want['base_color']}",
                    ),
                )

    # ------------------------------------------------------------------ collateral changes
    def untouched(self) -> None:
        skip = set(self.exp.created) | set(self.exp.modified) | self.exp.deleted | self.exp.affected
        for oid, prev in self.before.items():
            if oid in skip or oid not in self.after:
                continue
            cur = self.after[oid]
            same = (
                cur.name == prev.name
                and cur.parent_id == prev.parent_id
                and cur.visible == prev.visible
                and cur.material_ids == prev.material_ids
                and _close(cur.location, prev.location, 1e-4)
                and _angle_close(cur.rotation, prev.rotation)
                and _close(cur.dimensions, prev.dimensions, 1e-4)
            )
            self.check(
                same,
                Issue("unexpected_change", "error", f"'{oid}' changed although it was not part of the request", oid),
            )
        changed_mats = set(self.exp.materials_updated) | set(self.exp.materials_created)
        for mid, prev in self.mats_before.items():
            cur = self.mats_after.get(mid)
            if mid in changed_mats or cur is None:
                continue
            same = (
                cur.base_color.upper() == prev.base_color.upper()
                and abs(cur.metallic - prev.metallic) < 1e-3
                and abs(cur.roughness - prev.roughness) < 1e-3
                and abs(cur.alpha - prev.alpha) < 1e-3
            )
            self.check(same, Issue("unexpected_material_change", "error", f"material '{mid}' changed unexpectedly"))

    # ------------------------------------------------------------------ geometry
    def relations(self) -> None:
        for r in self.exp.relations:
            a, rel, b = r["subject"], r["relation"], r["object"]
            if not (self.solid(a) and self.solid(b)):
                continue
            A, B, o = self.box(a), self.box(b), self.after[a]
            if rel == "on_top_of":
                dz = B.max[2] - A.min[2]
                self.check(
                    abs(dz) <= TOUCH_TOL and A.overlaps_xy(B, TOUCH_TOL),
                    Issue(
                        "relation",
                        "fixable",
                        f"'{a}' should sit on top of '{b}' but its bottom is {-dz:+.3f} m from "
                        f"the top of '{b}'" + ("" if A.overlaps_xy(B, TOUCH_TOL) else " and it is not above it"),
                        a,
                        hint=f"move '{a}' to position {_fmt(_shift(o, dz))}",
                        data={"suggested_position": _shift(o, dz)},
                    ),
                )
            elif rel == "under":
                dz = B.min[2] - A.max[2]
                self.check(
                    abs(dz) <= TOUCH_TOL and A.overlaps_xy(B, TOUCH_TOL),
                    Issue(
                        "relation",
                        "fixable",
                        f"'{a}' should be directly under '{b}' but there is a {dz:+.3f} m gap "
                        f"between its top and the underside of '{b}'",
                        a,
                        hint=f"move '{a}' to position {_fmt(_shift(o, dz))}",
                        data={"suggested_position": _shift(o, dz)},
                    ),
                )
            elif rel == "attached_to":
                gap = A.gap(B)
                d = self._approach(A, B)
                self.check(
                    gap <= TOUCH_TOL,
                    Issue(
                        "relation",
                        "fixable",
                        f"'{a}' should be attached to '{b}' but is {gap:.3f} m away",
                        a,
                        hint=f"move '{a}' to position {_fmt(_shift(o, d=d))}",
                        data={"suggested_position": _shift(o, d=d)},
                    ),
                )
            elif rel == "inside":
                self.check(
                    B.contains(A, TOUCH_TOL),
                    Issue("relation", "fixable", f"'{a}' should be inside '{b}' but extends outside it", a),
                )
            elif rel == "beside":
                self.check(A.gap(B) <= 0.05, Issue("relation", "warning", f"'{a}' is not next to '{b}'", a))

    @staticmethod
    def _approach(A: Box, B: Box) -> tuple[float, float, float]:
        d = [0.0, 0.0, 0.0]
        for i in range(3):
            if A.min[i] > B.max[i]:
                d[i] = B.max[i] - A.min[i]
            elif A.max[i] < B.min[i]:
                d[i] = B.min[i] - A.max[i]
        return (d[0], d[1], d[2])

    def support(self) -> None:
        """New, moved or resized parts — and everything attached to them — must touch something (or
        stand on the floor) and stay above it. Children of a resized part are included: shrinking a
        drawer can leave its handle hanging in the air."""
        related = {r["subject"] for r in self.exp.relations}
        changed = {k for k, v in self.exp.modified.items() if "position" in v or "size" in v or "rotation" in v}
        children = {k for k, o in self.after.items() if o.parent_id in changed}
        for oid in sorted(set(self.exp.created) | changed | (self.exp.affected - set(self.exp.modified)) | children):
            if not self.solid(oid):
                continue
            o, A = self.after[oid], self.box(oid)
            self.check(
                A.min[2] >= -TOUCH_TOL,
                Issue(
                    "below_floor",
                    "fixable",
                    f"'{oid}' extends {-A.min[2]:.3f} m below the floor (z=0)",
                    oid,
                    hint=f"move '{oid}' to position {_fmt(_shift(o, -A.min[2]))}",
                    data={"suggested_position": _shift(o, -A.min[2])},
                ),
            )
            others = [k for k in self.after if k != oid and self.solid(k)]
            if oid in related or not others:
                continue  # explicit relations are checked precisely above
            touching = any(A.gap(self.box(k)) <= TOUCH_TOL for k in others)
            on_floor = abs(A.min[2]) <= TOUCH_TOL
            if touching or on_floor:
                self.checks += 1
                continue
            parent = o.parent_id if o.parent_id in self.after else None
            suggested: list[float]
            if parent:
                d = self._approach(A, self.box(parent))  # shortest move that makes it touch its parent
                suggested = _shift(o, d=d)
                hint_target = f"flush against '{parent}'"
            else:
                below = [
                    self.box(k).max[2]
                    for k in others
                    if self.box(k).overlaps_xy(A) and self.box(k).max[2] <= A.min[2] + TOUCH_TOL
                ]
                dz = (max(below) if below else 0.0) - A.min[2]
                suggested = _shift(o, dz)
                hint_target = "onto the surface below it"
            self.check(
                False,
                Issue(
                    "floating",
                    "fixable",
                    f"'{oid}' floats: it touches nothing (nearest gap "
                    f"{min((A.gap(self.box(k)) for k in others), default=A.min[2]):.3f} m)",
                    oid,
                    hint=f"move '{oid}' {hint_target}: position {_fmt(suggested)}",
                    data={"suggested_position": suggested},
                ),
            )


def run_qa(before: SceneInventory, after: SceneInventory, exp: Expectations) -> QAReport:
    c = _Checker(world_space(before), world_space(after), exp)  # plans speak world rotations
    c.existence()
    c.planned()
    c.untouched()
    c.relations()
    c.support()
    blocking = [i for i in c.issues if i.severity in ("error", "fixable")]
    return QAReport(passed=not blocking, checks=c.checks, issues=c.issues)


def blender_failure_report(code: str, message: str) -> QAReport:
    """A Blender execution failure is treated as a QA error the Revision Agent may fix."""
    return QAReport(
        False,
        1,
        [Issue("blender_failed", "fixable", f"Blender could not build the plan: {message}", data={"error_code": code})],
    )
