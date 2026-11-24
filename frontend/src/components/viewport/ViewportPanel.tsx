import { Canvas } from '@react-three/fiber'
import {
  Cuboid, Layers3, Maximize2, MessageSquareText, MousePointer2, PanelRightClose, RotateCcw,
  SlidersHorizontal, Square, ZoomIn, ZoomOut,
} from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { CircleShape, MoveIcon } from '../icons'
import { HistoryStrip } from '../versions/HistoryStrip'
import { Scene } from './Scene'

export function ViewportPanel() {
  const objects = useWorkspaceStore((s) => s.objects)
  const selectedId = useWorkspaceStore((s) => s.selectedId)
  const activeTool = useWorkspaceStore((s) => s.activeTool)
  const showChat = useWorkspaceStore((s) => s.showChat)
  const showInspector = useWorkspaceStore((s) => s.showInspector)
  const showGrid = useWorkspaceStore((s) => s.showGrid)
  const { select, setActiveTool, setShowChat, setShowInspector, addPrimitive, toggleGrid, resetScene } =
    useWorkspaceStore.getState()

  const selected = objects.find((o) => o.id === selectedId) ?? null
  const visibleCount = objects.filter((o) => o.visible).length

  return (
    <section className="viewport-column">
      <div className="viewport-toolbar">
        <div className="tool-group">
          {!showChat && <button className="tool-btn" onClick={() => setShowChat(true)} title="Show AI assistant"><MessageSquareText size={16} /></button>}
          <button className={`tool-btn ${activeTool === 'select' ? 'active' : ''}`} onClick={() => setActiveTool('select')} title="Select"><MousePointer2 size={16} /></button>
          <button className={`tool-btn ${activeTool === 'move' ? 'active' : ''}`} onClick={() => setActiveTool('move')} title="Move"><MoveIcon /></button>
          <button className={`tool-btn ${activeTool === 'scale' ? 'active' : ''}`} onClick={() => setActiveTool('scale')} title="Scale"><Maximize2 size={16} /></button>
          <span className="tool-divider"></span>
          <button className="tool-btn" onClick={() => addPrimitive('box')} title="Add cube"><Square size={15} /></button>
          <button className="tool-btn" onClick={() => addPrimitive('sphere')} title="Add sphere"><CircleShape /></button>
          <button className="tool-btn" onClick={() => addPrimitive('cylinder')} title="Add cylinder"><Cuboid size={16} /></button>
        </div>
        <div className="tool-group right-tools">
          <button className="tool-btn" onClick={toggleGrid} title="Toggle grid"><SlidersHorizontal size={15} /></button>
          <button className="tool-btn" onClick={resetScene} title="Reset scene"><RotateCcw size={15} /></button>
          {!showInspector && <button className="tool-btn" onClick={() => setShowInspector(true)} title="Show inspector"><PanelRightClose size={15} /></button>}
        </div>
      </div>

      <div className="viewport">
        <Canvas shadows camera={{ position: [5.5, 4.2, 6.5], fov: 42 }} onPointerMissed={() => select(null)}>
          <Scene objects={objects} selectedId={selectedId} showGrid={showGrid} onSelect={select} />
        </Canvas>
        <div className="viewport-label"><span className="live-indicator"></span> LIVE 3D PREVIEW <span className="viewport-label-sep">/</span> PERSPECTIVE</div>
        <div className="viewport-object-count"><Layers3 size={13} /> {visibleCount} visible</div>
        <div className="viewport-bottom-left"><span className="axis axis-x">X</span><span className="axis axis-y">Y</span><span className="axis axis-z">Z</span></div>
        <div className="viewport-bottom-right">
          <button title="Zoom in"><ZoomIn size={15} /></button>
          <button title="Zoom out"><ZoomOut size={15} /></button>
          <button title="Frame object"><Maximize2 size={15} /></button>
        </div>
        {selected && <div className="selection-chip"><span className="selection-dot"></span>{selected.name}</div>}
      </div>

      <div className="viewport-footer">
        <div className="footer-left">
          <span className="footer-item"><span className="keycap">LMB</span> Select</span>
          <span className="footer-item"><span className="keycap">MMB</span> Orbit</span>
          <span className="footer-item"><span className="keycap">Scroll</span> Zoom</span>
        </div>
        <div className="footer-right"><span>Blender scene · Demo mode</span><span className="footer-sep">|</span><span>Metric · Meters</span></div>
      </div>
      <HistoryStrip />
    </section>
  )
}
