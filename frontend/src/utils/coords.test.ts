import { describe, expect, it } from 'vitest'
import { buildTree } from '../components/hierarchy/SceneTree'
import { deskInventory, obj } from '../test/fixtures'
import { blenderToThree, roundVec, threeScaleToBlender, threeToBlender, vecEqual } from './coords'

describe('coordinate conversion (Blender Z-up <-> three Y-up)', () => {
  it('round-trips and maps axes like the glTF exporter', () => {
    expect(blenderToThree([1, 2, 3])).toEqual([1, 3, -2])
    expect(threeToBlender(blenderToThree([1, 2, 3]))).toEqual([1, 2, 3])
    expect(threeScaleToBlender([2, 3, 4])).toEqual([2, 4, 3])
  })

  it('rounds gizmo noise and compares with tolerance', () => {
    expect(roundVec([0.123456, -0.00001, 1])).toEqual([0.1235, 0, 1])
    expect(vecEqual([1, 2, 3], [1.00001, 2, 3])).toBe(true)
    expect(vecEqual([1, 2, 3], [1.01, 2, 3])).toBe(false)
  })
})

describe('buildTree', () => {
  it('nests by parent and sorts by name; orphans become roots', () => {
    const tree = buildTree([...deskInventory.objects, obj({ object_id: 'lamp', name: 'Lamp', parent_id: 'missing' })])
    expect(tree.map((n) => n.obj.object_id)).toEqual(['desk_top', 'lamp'])
    expect(tree[0].children.map((n) => n.obj.name)).toEqual(['Leg Front Left', 'Leg Front Right'])
  })
})
