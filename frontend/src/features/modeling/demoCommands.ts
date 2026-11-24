import type { ModelObject, PrimitiveKind } from '../../types/scene'

/**
 * Prototype-only command interpreter carried over from the original design.
 * Replaced by the real conversation endpoint + agent pipeline in Phase 4.
 */
export type DemoAction =
  | { type: 'add'; kind: PrimitiveKind }
  | { type: 'color'; targetId: string | null; color: string }
  | { type: 'widen'; targetId: string | null; factor: number }
  | { type: 'remove'; targetId: string }
  | { type: 'none' }

export type DemoResult = { action: DemoAction; reply: string }

const COLORS: [string, string][] = [
  ['red', '#C96E68'],
  ['blue', '#6C8FD9'],
  ['green', '#6E9C82'],
  ['black', '#30343B'],
  ['white', '#E8E9E6'],
  ['oak', '#C99563'],
]

const findTabletop = (objects: ModelObject[]) =>
  objects.find((o) => o.name.toLowerCase().includes('tabletop'))?.id ?? null

export function interpretDemoCommand(text: string, objects: ModelObject[], selectedId: string | null): DemoResult {
  const lower = text.toLowerCase()

  if (lower.includes('sphere') || lower.includes('ball')) {
    return {
      action: { type: 'add', kind: 'sphere' },
      reply: 'Added a sphere to the scene. You can select it in the scene tree to adjust its size, position and material.',
    }
  }
  if (lower.includes('cylinder')) {
    return {
      action: { type: 'add', kind: 'cylinder' },
      reply: 'Added a cylinder to the scene. Select it to fine-tune its dimensions.',
    }
  }
  if (lower.includes('cube') || lower.includes('box')) {
    return { action: { type: 'add', kind: 'box' }, reply: 'Added a cube to the scene. It is ready for further editing.' }
  }
  const color = COLORS.find(([word]) => lower.includes(word))
  if (color) {
    return {
      action: { type: 'color', targetId: findTabletop(objects), color: color[1] },
      reply: 'Updated the tabletop finish. You can fine-tune the exact colour in the Materials panel.',
    }
  }
  if (lower.includes('wider') || lower.includes('larger') || lower.includes('bigger')) {
    return {
      action: { type: 'widen', targetId: findTabletop(objects), factor: 1.15 },
      reply: 'Made the tabletop wider by 15%. The existing legs remain in place in this prototype.',
    }
  }
  if (lower.includes('remove') || lower.includes('delete')) {
    if (selectedId) return { action: { type: 'remove', targetId: selectedId }, reply: 'Removed the selected object from the scene.' }
    return { action: { type: 'none' }, reply: 'Select an object in the scene tree first, then ask me to remove it.' }
  }
  return {
    action: { type: 'none' },
    reply:
      'I’ve interpreted your instruction. This prototype demonstrates the conversation flow; connect the modeling API to execute more advanced edits.',
  }
}
