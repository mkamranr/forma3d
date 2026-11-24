import { Layers3, PanelRightClose, SlidersHorizontal } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { SceneTree } from '../hierarchy/SceneTree'
import { MaterialsPanel } from '../materials/MaterialsPanel'
import { PropertiesPanel } from '../properties/PropertiesPanel'

export function InspectorPanel() {
  const activeTab = useWorkspaceStore((s) => s.activeTab)
  const { setActiveTab, setShowInspector } = useWorkspaceStore.getState()

  return (
    <aside className="inspector-panel">
      <div className="inspector-tabs" role="tablist">
        <button role="tab" aria-selected={activeTab === 'properties'} className={activeTab === 'properties' ? 'selected' : ''} onClick={() => setActiveTab('properties')}>
          <SlidersHorizontal size={14} /> Properties
        </button>
        <button role="tab" aria-selected={activeTab === 'materials'} className={activeTab === 'materials' ? 'selected' : ''} onClick={() => setActiveTab('materials')}>
          <Layers3 size={14} /> Materials
        </button>
        <button className="close-inspector" onClick={() => setShowInspector(false)} title="Hide inspector"><PanelRightClose size={16} /></button>
      </div>

      {activeTab === 'properties' ? (
        <>
          <SceneTree />
          <PropertiesPanel />
        </>
      ) : (
        <MaterialsPanel />
      )}

      <div className="inspector-bottom">
        <div className="blender-status">
          <span className="blender-mark">B</span>
          <div><strong>Blender Engine</strong><small>Connection not configured</small></div>
          <span className="offline-pill">DEMO</span>
        </div>
      </div>
    </aside>
  )
}
