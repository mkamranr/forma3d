import { ChevronDown, Cuboid, Eye, EyeOff, Folder, Plus, Square } from 'lucide-react'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import { CircleShape } from '../icons'

export function SceneTree() {
  const objects = useWorkspaceStore((s) => s.objects)
  const selectedId = useWorkspaceStore((s) => s.selectedId)
  const { select, toggleVisibility, addPrimitive } = useWorkspaceStore.getState()

  return (
    <div className="inspector-section">
      <div className="section-heading">
        SCENE HIERARCHY <button onClick={() => addPrimitive('box')} title="Add cube"><Plus size={14} /></button>
      </div>
      <div className="scene-tree" role="tree">
        <div className="tree-row root">
          <ChevronDown size={13} /><Folder size={14} /><span>Scene Collection</span>
          <span className="tree-number">{objects.length}</span>
        </div>
        {objects.map((obj) => (
          <div
            role="treeitem"
            aria-selected={selectedId === obj.id}
            className={`tree-row child ${selectedId === obj.id ? 'chosen' : ''}`}
            key={obj.id}
            onClick={() => select(obj.id)}
          >
            <span className="tree-indent"></span>
            <span className="tree-shape">
              {obj.kind === 'sphere' ? <CircleShape /> : obj.kind === 'cylinder' ? <Cuboid size={13} /> : <Square size={13} />}
            </span>
            <span className="tree-name">{obj.name}</span>
            <button
              className="visibility-btn"
              title={obj.visible ? 'Hide' : 'Show'}
              onClick={(e) => { e.stopPropagation(); toggleVisibility(obj.id) }}
            >
              {obj.visible ? <Eye size={13} /> : <EyeOff size={13} />}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
