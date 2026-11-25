import type { ObjectPatch, PrimitiveKind, SceneMaterial, SceneObject, Vec3 } from '../../types/scene'

/**
 * Rule-based command interpreter used until the LLM agent lands (Phase 4). Unlike the prototype it
 * does not touch a local scene: it maps text to a *direct edit* that runs through the real
 * DSL -> validator -> Blender -> version pipeline.
 */
export type DemoAction =
  | { type: 'add'; kind: PrimitiveKind }
  | { type: 'material'; targetId: string; materialId: string | null; color: string; metallic?: number; roughness?: number }
  | { type: 'edit'; targetId: string; patch: ObjectPatch }
  | { type: 'remove'; targetId: string }
  | { type: 'none' }

export type DemoResult = { action: DemoAction; reply: string }

const COLORS: [string, string, Partial<{ metallic: number; roughness: number }>][] = [
  ['walnut', '#5C3A21', { roughness: 0.55, metallic: 0 }],
  ['oak', '#C99563', { roughness: 0.6, metallic: 0 }],
  ['red', '#C96E68', {}],
  ['blue', '#6C8FD9', {}],
  ['green', '#6E9C82', {}],
  ['black', '#111111', {}],
  ['white', '#E8E9E6', {}],
]

const WORDS = (s: string) => s.toLowerCase().replace(/[_-]/g, ' ')

/** Pick the object the text refers to: by name/component words, else the selection, else the largest. */
export function resolveTarget(text: string, objects: SceneObject[], selectedId: string | null): SceneObject | null {
  const lower = WORDS(text)
  // Score = number of distinct name/component words mentioned; ties go to the larger object.
  const score = (o: SceneObject) => {
    const words = new Set([...WORDS(o.name).split(/\s+/), ...(o.component_type ? WORDS(o.component_type).split(/\s+/) : [])])
    return [...words].filter((w) => w.length > 2 && lower.includes(w)).length
  }
  const named = objects
    .map((o) => ({ o, s: score(o) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || volume(b.o) - volume(a.o))
  if (named.length) return named[0].o
  if (selectedId) return objects.find((o) => o.object_id === selectedId) ?? null
  return [...objects].sort((a, b) => volume(b) - volume(a))[0] ?? null
}

const volume = (o: SceneObject) => o.dimensions[0] * o.dimensions[1] * Math.max(o.dimensions[2], 0.001)

export function interpretDemoCommand(
  text: string,
  objects: SceneObject[],
  materials: Map<string, SceneMaterial>,
  selectedId: string | null,
): DemoResult {
  const lower = text.toLowerCase()

  const add = (['sphere', 'ball', 'cylinder', 'cube', 'box', 'cone', 'torus', 'plane'] as const).find((w) =>
    new RegExp(`\\b(add|create|put|insert)\\b.*\\b${w}`).test(lower),
  )
  if (add) {
    const kind: PrimitiveKind = add === 'ball' ? 'sphere' : add === 'box' ? 'cube' : add
    return { action: { type: 'add', kind }, reply: `Adding a ${kind}. Select it afterwards to position and size it.` }
  }

  const target = resolveTarget(text, objects, selectedId)
  if (!target) {
    return { action: { type: 'none' }, reply: 'The scene is empty — start with the sample desk or ask me to add a cube.' }
  }

  if (/\b(remove|delete)\b/.test(lower)) {
    return { action: { type: 'remove', targetId: target.object_id }, reply: `Removing ${target.name}.` }
  }

  const color = COLORS.find(([word]) => lower.includes(word))
  if (color) {
    const [word, hex, extra] = color
    const metal = /\bmetal(lic)?\b/.test(lower)
    return {
      action: {
        type: 'material',
        targetId: target.object_id,
        materialId: target.material_ids[0] && materials.has(target.material_ids[0]) ? target.material_ids[0] : null,
        color: hex,
        ...(metal ? { metallic: 1, roughness: 0.35 } : extra),
      },
      reply: `Changing ${target.name} to ${word}${metal ? ' metal' : ''}. Objects sharing its material change too.`,
    }
  }

  const grow = /\b(wider|larger|bigger|longer)\b/.test(lower)
  const shrink = /\b(narrower|smaller|shorter)\b/.test(lower)
  if (grow || shrink) {
    const f = grow ? 1.15 : 1 / 1.15
    const d = target.dimensions
    const dimensions: Vec3 = /\b(wider|narrower|longer|shorter)\b/.test(lower)
      ? [d[0] * f, d[1], d[2]]
      : [d[0] * f, d[1] * f, d[2] * f]
    return {
      action: { type: 'edit', targetId: target.object_id, patch: { dimensions: dimensions.map((n) => Number(n.toFixed(4))) as Vec3 } },
      reply: `Making ${target.name} ${grow ? '15% larger' : '15% smaller'}.`,
    }
  }

  if (/\bhide\b/.test(lower)) {
    return { action: { type: 'edit', targetId: target.object_id, patch: { visible: false } }, reply: `Hiding ${target.name}.` }
  }

  return {
    action: { type: 'none' },
    reply:
      'I can only follow simple commands until the AI planner is connected: add a shape, recolour, make something wider or smaller, hide, or delete it.',
  }
}
