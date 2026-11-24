import { ContactShadows, Environment, Grid, Lightformer, OrbitControls } from '@react-three/drei'
import type { ModelObject } from '../../types/scene'

function MeshItem({ item, selected, onSelect }: { item: ModelObject; selected: boolean; onSelect: () => void }) {
  const geometry =
    item.kind === 'sphere' ? <sphereGeometry args={[0.65, 32, 24]} /> :
    item.kind === 'cylinder' ? <cylinderGeometry args={[0.55, 0.55, 1, 32]} /> :
    <boxGeometry args={[1, 1, 1]} />
  return (
    <mesh
      position={item.position}
      scale={item.scale}
      onClick={(e) => { e.stopPropagation(); onSelect() }}
      castShadow
      receiveShadow
      visible={item.visible}
    >
      {geometry}
      <meshStandardMaterial
        color={item.color}
        roughness={0.58}
        metalness={0.08}
        emissive={selected ? '#182b54' : '#000000'}
        emissiveIntensity={selected ? 0.18 : 0}
      />
    </mesh>
  )
}

/**
 * Local studio lighting. Uses Lightformers instead of an HDRI preset so the viewport
 * never downloads assets from a CDN (air-gapped deployments, strict CSP).
 */
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

type Props = {
  objects: ModelObject[]
  selectedId: string | null
  showGrid: boolean
  onSelect: (id: string) => void
}

export function Scene({ objects, selectedId, showGrid, onSelect }: Props) {
  return (
    <>
      <color attach="background" args={['#202630']} />
      <ambientLight intensity={0.75} />
      <directionalLight position={[5, 8, 5]} intensity={2.2} castShadow shadow-mapSize={[1024, 1024]} />
      <StudioEnvironment />
      {showGrid && (
        <Grid
          position={[0, -0.02, 0]}
          args={[18, 18]}
          cellSize={0.5}
          cellThickness={0.5}
          cellColor="#566170"
          sectionSize={2}
          sectionThickness={1}
          sectionColor="#8290a2"
          fadeDistance={22}
          infiniteGrid
        />
      )}
      {objects.map((obj) => (
        <MeshItem key={obj.id} item={obj} selected={obj.id === selectedId} onSelect={() => onSelect(obj.id)} />
      ))}
      <ContactShadows position={[0, -0.015, 0]} opacity={0.38} scale={15} blur={2.5} far={4} />
      <OrbitControls makeDefault minDistance={3} maxDistance={18} />
    </>
  )
}
