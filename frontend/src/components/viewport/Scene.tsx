import {
  ContactShadows,
  Environment,
  GizmoHelper,
  GizmoViewport,
  Grid,
  Lightformer,
  OrbitControls,
  OrthographicCamera,
  PerspectiveCamera,
  TransformControls,
} from '@react-three/drei'
import { useThree, type ThreeEvent } from '@react-three/fiber'
import React, { useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js'
import type { CameraPreset, Projection, Shading, Tool } from '../../stores/workspaceStore'
import type { ObjectPatch, SceneObject, Vec3 } from '../../types/scene'
import { roundVec, threeScaleToBlender, threeToBlender, vecEqual } from '../../utils/coords'

const OBJECT_ID_KEY = 'app_object_id'
const SELECT_TINT = new THREE.Color('#5b6cff')

type MeshInfo = { owner: string | null; original: THREE.Material | THREE.Material[]; solid: THREE.Material; wire: THREE.Material }

/** Nearest ancestor-or-self carrying a stable Blender object id (glTF extras -> userData). */
export function ownerId(object: THREE.Object3D | null): string | null {
  for (let o = object; o; o = o.parent) {
    const id = o.userData?.[OBJECT_ID_KEY]
    if (typeof id === 'string') return id
  }
  return null
}

function cloneMaterial(m: THREE.Material | THREE.Material[]) {
  return Array.isArray(m) ? m.map((x) => x.clone()) : m.clone()
}

function eachMaterial(m: THREE.Material | THREE.Material[], fn: (m: THREE.Material) => void) {
  if (Array.isArray(m)) m.forEach(fn)
  else fn(m)
}

/** Deep-clone the cached GLTF scene (so edits/previews never touch the cache) and index it. */
function prepare(gltf: GLTF) {
  const root = gltf.scene.clone(true)
  const nodes = new Map<string, THREE.Object3D>()
  const meshes: THREE.Mesh[] = []
  root.traverse((o) => {
    const id = o.userData?.[OBJECT_ID_KEY]
    if (typeof id === 'string' && !nodes.has(id)) nodes.set(id, o)
    if ((o as THREE.Mesh).isMesh) {
      const mesh = o as THREE.Mesh
      mesh.castShadow = true
      mesh.receiveShadow = true
      const info: MeshInfo = {
        owner: ownerId(mesh),
        original: cloneMaterial(mesh.material),
        solid: new THREE.MeshStandardMaterial({ color: '#c9ced6', roughness: 0.85, metalness: 0 }),
        wire: new THREE.MeshBasicMaterial({ color: '#aeb8c8', wireframe: true }),
      }
      mesh.userData.f3d = info
      meshes.push(mesh)
    }
  })
  return { root, nodes, meshes }
}

type ModelProps = {
  gltf: GLTF
  resetKey: number
  objects: Map<string, SceneObject>
  selectedId: string | null
  selectedIds: string[]
  shading: Shading
  tool: Tool
  onSelect: (id: string | null, additive?: boolean) => void
  onCommit: (objectId: string, patch: ObjectPatch) => void
  onDragEnd: () => void
}

function Model({ gltf, resetKey, objects, selectedId, selectedIds, shading, tool, onSelect, onCommit, onDragEnd }: ModelProps) {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const prepared = useMemo(() => prepare(gltf), [gltf, resetKey])
  const startScale = useRef(new THREE.Vector3(1, 1, 1))

  useLayoutEffect(() => {
    for (const mesh of prepared.meshes) {
      const info = mesh.userData.f3d as MeshInfo
      const obj = info.owner ? objects.get(info.owner) : undefined
      const visible = obj ? obj.visible : true
      const material = shading === 'wireframe' ? info.wire : shading === 'solid' ? info.solid : info.original
      mesh.material = material
      const selected = !!info.owner && selectedIds.includes(info.owner)
      eachMaterial(material, (m) => {
        m.visible = visible
        const std = m as THREE.MeshStandardMaterial
        if (std.emissive) {
          std.emissive.copy(selected ? SELECT_TINT : new THREE.Color(0, 0, 0))
          std.emissiveIntensity = selected ? 0.08 : 0 // subtle: keep the real material readable
        } else if ((m as THREE.MeshBasicMaterial).color) {
          ;(m as THREE.MeshBasicMaterial).color.set(selected ? '#8b9cfb' : '#aeb8c8')
        }
      })
    }
  }, [prepared, objects, selectedIds, shading])

  const selectedNode = selectedId ? prepared.nodes.get(selectedId) ?? null : null
  const box = useMemo(() => new THREE.Box3Helper(new THREE.Box3(), new THREE.Color('#8b9cfb')), [])
  useLayoutEffect(() => {
    box.visible = !!selectedNode
    if (selectedNode) box.box.setFromObject(selectedNode)
  })

  const handleClick = (e: ThreeEvent<MouseEvent>) => {
    e.stopPropagation()
    // First hit whose owning object is visible (hidden meshes stay in the GLB for stable ids).
    for (const hit of e.intersections) {
      const id = ownerId(hit.object)
      if (id && objects.get(id)?.visible !== false) {
        const n = e.nativeEvent
        onSelect(id, n.shiftKey || n.ctrlKey || n.metaKey)
        return
      }
    }
    onSelect(null)
  }

  const commitTransform = () => {
    if (!selectedNode || !selectedId) return
    const obj = objects.get(selectedId)
    if (!obj) return
    selectedNode.updateWorldMatrix(true, false)
    if (tool === 'move') {
      const p = selectedNode.getWorldPosition(new THREE.Vector3())
      const location = roundVec(threeToBlender([p.x, p.y, p.z]))
      if (!vecEqual(location, obj.location)) onCommit(selectedId, { location })
    } else if (tool === 'scale') {
      const s = selectedNode.scale
      const f = threeScaleToBlender([s.x / startScale.current.x, s.y / startScale.current.y, s.z / startScale.current.z])
      const dimensions = roundVec(obj.dimensions.map((d, i) => Math.max(0.001, d * Math.abs(f[i]))) as Vec3)
      if (!vecEqual(dimensions, obj.dimensions)) onCommit(selectedId, { dimensions })
    }
    onDragEnd()
  }

  return (
    <>
      <primitive object={prepared.root} onClick={handleClick} />
      <primitive object={box} />
      {selectedNode && (tool === 'move' || tool === 'scale') && objects.get(selectedId!)?.visible !== false && (
        <TransformControls
          object={selectedNode}
          mode={tool === 'move' ? 'translate' : 'scale'}
          space={tool === 'move' ? 'world' : 'local'}
          size={0.8}
          onMouseDown={() => startScale.current.copy(selectedNode.scale)}
          onMouseUp={commitTransform}
        />
      )}
    </>
  )
}

/** Bounds of the model's meshes only (gizmos/helpers in the group are huge and must not count). */
export function modelBounds(group: THREE.Object3D | null): THREE.Box3 {
  const box = new THREE.Box3()
  group?.updateWorldMatrix(true, true)
  group?.traverse((o) => {
    if ((o as THREE.Mesh).isMesh && o.userData.f3d) box.expandByObject(o)
  })
  return box
}

const PRESET_DIRECTIONS: Record<CameraPreset, THREE.Vector3> = {
  iso: new THREE.Vector3(1, 0.75, 1.15),
  front: new THREE.Vector3(0, 0.0001, 1),
  back: new THREE.Vector3(0, 0.0001, -1),
  right: new THREE.Vector3(1, 0.0001, 0),
  left: new THREE.Vector3(-1, 0.0001, 0),
  top: new THREE.Vector3(0, 1, 0.0001),
}

type ViewRequestKind = 'fit' | CameraPreset | 'zoom_in' | 'zoom_out'

function CameraRig({
  target,
  request,
}: {
  target: React.RefObject<THREE.Object3D>
  request: { kind: ViewRequestKind; nonce: number }
}) {
  const { camera, controls, size } = useThree() as unknown as {
    camera: THREE.PerspectiveCamera | THREE.OrthographicCamera
    controls: { target: THREE.Vector3; update: () => void } | null
    size: { width: number; height: number }
  }
  const lastDirection = useRef(PRESET_DIRECTIONS.iso.clone())

  useEffect(() => {
    if (!controls) return
    const kind = request.kind
    if (kind === 'zoom_in' || kind === 'zoom_out') {
      const factor = kind === 'zoom_in' ? 0.8 : 1.25
      if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
        camera.zoom /= factor
        camera.updateProjectionMatrix()
      } else {
        const offset = camera.position.clone().sub(controls.target).multiplyScalar(factor)
        camera.position.copy(controls.target).add(offset)
      }
      controls.update()
      return
    }
    const box = modelBounds(target.current)
    if (box.isEmpty()) box.set(new THREE.Vector3(-1, 0, -1), new THREE.Vector3(1, 1.5, 1))
    const sphere = box.getBoundingSphere(new THREE.Sphere())
    const radius = Math.max(sphere.radius, 0.25)
    if (kind !== 'fit') lastDirection.current = PRESET_DIRECTIONS[kind].clone()
    const dir = lastDirection.current.clone().normalize()
    if ((camera as THREE.OrthographicCamera).isOrthographicCamera) {
      camera.position.copy(sphere.center).addScaledVector(dir, radius * 6)
      camera.zoom = Math.min(size.width, size.height) / (radius * 2.2)
    } else {
      const fov = THREE.MathUtils.degToRad((camera as THREE.PerspectiveCamera).fov)
      const distance = (radius * 1.1) / Math.sin(fov / 2)
      camera.position.copy(sphere.center).addScaledVector(dir, distance)
    }
    camera.near = Math.max(0.01, radius / 100)
    camera.far = radius * 200 + 100
    camera.updateProjectionMatrix()
    controls.target.copy(sphere.center)
    controls.update()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.nonce, controls, camera])

  return null
}

/** Local studio lighting (no CDN HDRI: air-gapped deployments, strict CSP). */
function StudioEnvironment() {
  return (
    <Environment resolution={256}>
      <Lightformer form="rect" intensity={2.2} position={[0, 5, -9]} scale={[10, 5, 1]} />
      <Lightformer form="rect" intensity={1.4} position={[-6, 2, 2]} rotation-y={Math.PI / 2} scale={[8, 3, 1]} />
      <Lightformer form="rect" intensity={1.0} position={[6, 2, 2]} rotation-y={-Math.PI / 2} scale={[8, 3, 1]} />
      <Lightformer form="ring" intensity={0.8} position={[0, 8, 0]} rotation-x={Math.PI / 2} scale={4} />
    </Environment>
  )
}

export type SceneProps = {
  gltf: GLTF | null
  resetKey: number
  objects: Map<string, SceneObject>
  selectedId: string | null
  selectedIds: string[]
  showGrid: boolean
  showAxes: boolean
  shading: Shading
  projection: Projection
  tool: Tool
  viewRequest: { kind: ViewRequestKind; nonce: number }
  onSelect: (id: string | null, additive?: boolean) => void
  onCommit: (objectId: string, patch: ObjectPatch) => void
  onDragEnd: () => void
}

export function Scene(props: SceneProps) {
  const { gltf, showGrid, showAxes, projection, viewRequest } = props
  const modelGroup = useRef<THREE.Group>(null)
  return (
    <>
      <color attach="background" args={['#202630']} />
      {projection === 'perspective' ? (
        <PerspectiveCamera makeDefault position={[3.2, 2.4, 3.8]} fov={42} near={0.01} far={500} />
      ) : (
        <OrthographicCamera makeDefault position={[3.2, 2.4, 3.8]} zoom={160} near={-500} far={500} />
      )}
      <ambientLight intensity={0.75} />
      <directionalLight position={[5, 8, 5]} intensity={2.2} castShadow shadow-mapSize={[1024, 1024]} />
      <StudioEnvironment />
      {showGrid && (
        <Grid
          position={[0, -0.001, 0]}
          args={[18, 18]}
          cellSize={0.1}
          cellThickness={0.5}
          cellColor="#566170"
          sectionSize={1}
          sectionThickness={1}
          sectionColor="#8290a2"
          fadeDistance={22}
          infiniteGrid
        />
      )}
      <group ref={modelGroup}>{gltf && <Model {...props} gltf={gltf} />}</group>
      <ContactShadows position={[0, -0.0005, 0]} opacity={0.38} scale={10} blur={2.5} far={4} />
      <OrbitControls makeDefault minDistance={0.2} maxDistance={80} />
      <CameraRig target={modelGroup} request={viewRequest} />
      {showAxes && (
        <GizmoHelper alignment="bottom-left" margin={[64, 64]}>
          {/* three is Y-up: its Y axis is Blender Z and its Z axis is Blender -Y */}
          <GizmoViewport axisColors={['#e5636b', '#6c8fd9', '#6fc28b']} labels={['X', 'Z', '-Y']} labelColor="#fff" />
        </GizmoHelper>
      )}
    </>
  )
}
