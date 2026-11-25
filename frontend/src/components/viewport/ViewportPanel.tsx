import { Canvas } from '@react-three/fiber'
import {
  Axis3D, Box, Cuboid, Expand, Grid3X3, Layers3, Loader2, Maximize2, MessageSquareText, MousePointer2,
  PanelRightClose, Scan, Shrink, Square, X, ZoomIn, ZoomOut,
} from 'lucide-react'
import { Suspense, useEffect, useRef, useState } from 'react'
import { useModel, useModelingAction } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { addObject, cancelJob, createStarterScene, editObject } from '../../services/scene'
import { useWorkspaceStore, type CameraPreset, type Shading } from '../../stores/workspaceStore'
import type { ObjectPatch, PrimitiveKind } from '../../types/scene'
import { CircleShape, MoveIcon } from '../icons'
import { HistoryStrip } from '../versions/HistoryStrip'
import { Scene } from './Scene'

const SHADING_LABEL: Record<Shading, string> = { material: 'Material', solid: 'Solid', wireframe: 'Wireframe' }
const NEXT_SHADING: Record<Shading, Shading> = { material: 'solid', solid: 'wireframe', wireframe: 'material' }
const PRESETS: [CameraPreset, string][] = [['iso', 'Iso'], ['front', 'Front'], ['right', 'Right'], ['back', 'Back'], ['left', 'Left'], ['top', 'Top']]
const PRIMITIVE_LABEL: Record<PrimitiveKind, string> = {
  cube: 'Add cube', sphere: 'Add sphere', cylinder: 'Add cylinder', cone: 'Add cone', plane: 'Add plane', torus: 'Add torus',
}

export function ViewportPanel() {
  const { projectId, query, version, objectMap, selected } = useWorkspaceScene()
  const model = useModel(version)
  const s = useWorkspaceStore()
  const run = useModelingAction()
  const viewportRef = useRef<HTMLDivElement>(null)
  const [resetKey, setResetKey] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const suppressMiss = useRef(0)
  const fittedFor = useRef<string | null>(null)

  // Frame the model the first time a project's scene appears (not on every edit).
  useEffect(() => {
    if (model.data && fittedFor.current !== projectId) {
      fittedFor.current = projectId
      s.requestView('fit')
    }
  }, [model.data, projectId, s])

  // A failed/cancelled job leaves gizmo previews on screen; rebuild from the cached GLB.
  const activeJobId = s.activeJob?.id
  useEffect(() => {
    if (!activeJobId) setResetKey((k) => k + 1)
  }, [activeJobId])

  // Drop selection if the selected object disappeared (deleted / restored version).
  useEffect(() => {
    if (s.selectedId && query.data && !objectMap.has(s.selectedId)) s.select(null)
  }, [objectMap, query.data, s])

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === viewportRef.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen()
    else void viewportRef.current?.requestFullscreen?.()
  }

  const add = (kind: PrimitiveKind) => run(PRIMITIVE_LABEL[kind].replace('Add', 'Added'), () => addObject(projectId, kind))
  const commit = (objectId: string, patch: ObjectPatch) => {
    const name = objectMap.get(objectId)?.name ?? objectId
    void run(patch.location ? `Moved ${name}` : `Resized ${name}`, () => editObject(projectId, objectId, patch))
  }

  const visibleCount = [...objectMap.values()].filter((o) => o.visible).length
  const busy = !!s.activeJob
  const hasScene = !!version
  const loadingModel = hasScene && model.isLoading
  const modelError = hasScene && model.isError

  return (
    <section className="viewport-column">
      <div className="viewport-toolbar">
        <div className="tool-group">
          {!s.showChat && <button className="tool-btn" onClick={() => s.setShowChat(true)} title="Show AI assistant"><MessageSquareText size={16} /></button>}
          <button className={`tool-btn ${s.activeTool === 'select' ? 'active' : ''}`} onClick={() => s.setActiveTool('select')} title="Select"><MousePointer2 size={16} /></button>
          <button className={`tool-btn ${s.activeTool === 'move' ? 'active' : ''}`} onClick={() => s.setActiveTool('move')} title="Move (drag gizmo, release to save)"><MoveIcon /></button>
          <button className={`tool-btn ${s.activeTool === 'scale' ? 'active' : ''}`} onClick={() => s.setActiveTool('scale')} title="Resize (drag gizmo, release to save)"><Maximize2 size={16} /></button>
          <span className="tool-divider"></span>
          <button className="tool-btn" disabled={busy} onClick={() => add('cube')} title="Add cube"><Square size={15} /></button>
          <button className="tool-btn" disabled={busy} onClick={() => add('sphere')} title="Add sphere"><CircleShape /></button>
          <button className="tool-btn" disabled={busy} onClick={() => add('cylinder')} title="Add cylinder"><Cuboid size={16} /></button>
        </div>
        <div className="tool-group right-tools">
          <button className="tool-btn tool-text" onClick={() => s.setShading(NEXT_SHADING[s.shading])} title="Shading mode">
            <Box size={14} /> {SHADING_LABEL[s.shading]}
          </button>
          <button className="tool-btn tool-text" onClick={s.toggleProjection} title="Toggle perspective / orthographic">
            {s.projection === 'perspective' ? 'Persp' : 'Ortho'}
          </button>
          <button className={`tool-btn ${s.showGrid ? 'active' : ''}`} onClick={s.toggleGrid} title="Toggle grid"><Grid3X3 size={15} /></button>
          <button className={`tool-btn ${s.showAxes ? 'active' : ''}`} onClick={s.toggleAxes} title="Toggle axes gizmo"><Axis3D size={15} /></button>
          {!s.showInspector && <button className="tool-btn" onClick={() => s.setShowInspector(true)} title="Show inspector"><PanelRightClose size={15} /></button>}
        </div>
      </div>

      <div className="viewport" ref={viewportRef}>
        <Canvas
          shadows
          onPointerMissed={() => {
            if (Date.now() - suppressMiss.current > 300) s.select(null)
          }}
        >
          <Suspense fallback={null}>
            <Scene
              gltf={model.data ?? null}
              resetKey={resetKey}
              objects={objectMap}
              selectedId={s.selectedId}
              showGrid={s.showGrid}
              showAxes={s.showAxes}
              shading={s.shading}
              projection={s.projection}
              tool={busy ? 'select' : s.activeTool}
              viewRequest={s.viewRequest}
              onSelect={s.select}
              onCommit={commit}
              onDragEnd={() => { suppressMiss.current = Date.now() }}
            />
          </Suspense>
        </Canvas>

        <div className="viewport-label">
          <span className="live-indicator"></span> LIVE 3D PREVIEW <span className="viewport-label-sep">/</span>{' '}
          {s.projection === 'perspective' ? 'PERSPECTIVE' : 'ORTHOGRAPHIC'}
        </div>
        <div className="viewport-object-count"><Layers3 size={13} /> {visibleCount} visible</div>
        <div className="view-presets" role="group" aria-label="Camera views">
          {PRESETS.map(([kind, label]) => (
            <button key={kind} onClick={() => s.requestView(kind)}>{label}</button>
          ))}
        </div>
        <div className="viewport-bottom-right">
          <button title="Zoom in" onClick={() => s.requestView('zoom_in')}><ZoomIn size={15} /></button>
          <button title="Zoom out" onClick={() => s.requestView('zoom_out')}><ZoomOut size={15} /></button>
          <button title="Fit to view" onClick={() => s.requestView('fit')}><Scan size={15} /></button>
          <button title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'} onClick={toggleFullscreen}>
            {fullscreen ? <Shrink size={15} /> : <Expand size={15} />}
          </button>
        </div>
        {selected && <div className="selection-chip"><span className="selection-dot"></span>{selected.name}</div>}

        {s.activeJob && (
          <div className="viewport-job" role="status">
            <Loader2 size={14} className="spin" />
            <span><strong>{s.activeJob.label}</strong> · {s.activeJob.message}</span>
            <button title="Cancel" onClick={() => void cancelJob(s.activeJob!.id).catch(() => undefined)}><X size={13} /></button>
          </div>
        )}

        {query.isLoading && <div className="viewport-overlay"><Loader2 className="spin" size={22} /><span>Loading scene…</span></div>}
        {query.isError && <div className="viewport-overlay"><strong>Could not load the scene</strong><span>{query.error.message}</span></div>}
        {loadingModel && <div className="viewport-overlay subtle"><Loader2 className="spin" size={20} /><span>Loading model…</span></div>}
        {modelError && (
          <div className="viewport-overlay">
            <strong>The 3D model could not be displayed</strong>
            <span>{model.error?.message}</span>
            <button className="overlay-btn" onClick={() => void model.refetch()}>Retry</button>
          </div>
        )}
        {query.isSuccess && !hasScene && !busy && (
          <div className="viewport-overlay empty">
            <strong>Start creating your first 3D model.</strong>
            <span>Begin from a sample desk, or add a primitive and shape it from the inspector.</span>
            <div className="overlay-actions">
              <button className="overlay-btn primary" onClick={() => void run('Sample desk', () => createStarterScene(projectId))}>
                Start with a sample desk
              </button>
              <button className="overlay-btn" onClick={() => void add('cube')}>Add a cube</button>
            </div>
          </div>
        )}
      </div>

      <div className="viewport-footer">
        <div className="footer-left">
          <span className="footer-item"><span className="keycap">LMB</span> Select</span>
          <span className="footer-item"><span className="keycap">RMB</span> Pan</span>
          <span className="footer-item"><span className="keycap">Drag</span> Orbit</span>
          <span className="footer-item"><span className="keycap">Scroll</span> Zoom</span>
        </div>
        <div className="footer-right">
          <span>{version ? `Blender scene · v${version.version_number}` : 'Blender scene · empty'}</span>
          <span className="footer-sep">|</span><span>Metric · Meters · Z up</span>
        </div>
      </div>
      <HistoryStrip />
    </section>
  )
}
