import { describe, expect, it } from 'vitest'
import { deskInventory } from '../../test/fixtures'
import { interpretDemoCommand, resolveTarget } from './demoCommands'

const materials = new Map(deskInventory.materials.map((m) => [m.material_id, m]))
const run = (text: string, selected: string | null = null) =>
  interpretDemoCommand(text, deskInventory.objects, materials, selected).action

describe('interpretDemoCommand', () => {
  it.each([
    ['Add a sphere to the desk', 'sphere'],
    ['add a ball', 'sphere'],
    ['Create a cylinder', 'cylinder'],
    ['put a box on top', 'cube'],
  ])('"%s" adds a %s', (text, kind) => {
    expect(run(text)).toEqual({ type: 'add', kind })
  })

  it('does not treat "make the box bigger" as an add', () => {
    expect(run('make the tabletop bigger').type).toBe('edit')
  })

  it('recolours a shared material in place', () => {
    expect(run('Make the legs black metal')).toEqual({
      type: 'material', targetId: 'leg_fl', materialId: 'graphite', color: '#111111', metallic: 1, roughness: 0.35,
    })
  })

  it('changes the top to walnut', () => {
    expect(run('change the top to walnut')).toMatchObject({ type: 'material', targetId: 'desk_top', materialId: 'oak', color: '#5C3A21' })
  })

  it('widens only X by 15%', () => {
    const action = run('Make the tabletop wider')
    expect(action).toEqual({ type: 'edit', targetId: 'desk_top', patch: { dimensions: [1.84, 0.8, 0.04] } })
  })

  it('removes the named or selected object', () => {
    expect(run('delete the front right leg')).toEqual({ type: 'remove', targetId: 'leg_fr' })
    expect(run('remove it', 'leg_fl')).toEqual({ type: 'remove', targetId: 'leg_fl' })
  })

  it('falls back to a helpful reply', () => {
    expect(run('compose a sonnet')).toEqual({ type: 'none' })
    expect(interpretDemoCommand('make it red', [], materials, null).action).toEqual({ type: 'none' })
  })

  it('resolves the largest matching object when several match', () => {
    expect(resolveTarget('the legs', deskInventory.objects, null)?.component_type).toBe('leg')
    expect(resolveTarget('it', deskInventory.objects, null)?.object_id).toBe('desk_top')
  })
})
