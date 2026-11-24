import { describe, expect, it } from 'vitest'
import { initialObjects } from '../../stores/workspaceStore'
import { interpretDemoCommand } from './demoCommands'

describe('interpretDemoCommand', () => {
  it.each([
    ['Add a sphere to the desk', 'sphere'],
    ['add a ball', 'sphere'],
    ['Add a cylinder', 'cylinder'],
    ['put a box on top', 'box'],
  ])('"%s" adds a %s', (text, kind) => {
    expect(interpretDemoCommand(text, initialObjects, null).action).toEqual({ type: 'add', kind })
  })

  it('recolours the tabletop', () => {
    expect(interpretDemoCommand('Change the tabletop to blue', initialObjects, null).action).toEqual({
      type: 'color',
      targetId: 'desk-top',
      color: '#6C8FD9',
    })
  })

  it('widens the tabletop by 15%', () => {
    expect(interpretDemoCommand('Make the tabletop wider', initialObjects, null).action).toMatchObject({
      type: 'widen',
      targetId: 'desk-top',
      factor: 1.15,
    })
  })

  it('removes only when something is selected', () => {
    expect(interpretDemoCommand('Remove selected object', initialObjects, 'leg-1').action).toEqual({ type: 'remove', targetId: 'leg-1' })
    expect(interpretDemoCommand('Remove selected object', initialObjects, null).action).toEqual({ type: 'none' })
  })

  it('falls back to an explanatory reply', () => {
    const result = interpretDemoCommand('Design a spaceship', initialObjects, null)
    expect(result.action).toEqual({ type: 'none' })
    expect(result.reply).toMatch(/prototype/)
  })
})
