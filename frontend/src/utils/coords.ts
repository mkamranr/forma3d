import type { Vec3 } from '../types/scene'

/**
 * Blender is Z-up; glTF/three.js is Y-up. The glTF exporter maps Blender (x, y, z) to
 * three (x, z, -y). All API values stay in Blender space; convert only at the viewport edge.
 */
export const blenderToThree = ([x, y, z]: Vec3): Vec3 => [x, z, -y]
export const threeToBlender = ([x, y, z]: Vec3): Vec3 => [x, -z, y]

/** Per-axis local scale factors measured in three space -> Blender local axes. */
export const threeScaleToBlender = ([x, y, z]: Vec3): Vec3 => [x, z, y]

/** Round to 0.1 mm so tiny float noise from gizmos doesn't create spurious edits. */
export const roundVec = (v: Vec3, digits = 4): Vec3 => v.map((n) => Number(n.toFixed(digits)) + 0) as Vec3

export const vecEqual = (a: Vec3, b: Vec3, eps = 1e-4) => a.every((n, i) => Math.abs(n - b[i]) <= eps)
