import { ChevronDown, ChevronRight, Circle, Cuboid, Eye, EyeOff, Folder, Plus, Square, Trash2 } from 'lucide-react'
import { useMemo, useState, type KeyboardEvent } from 'react'
import { useModelingAction } from '../../features/modeling/hooks'
import { useWorkspaceScene } from '../../features/modeling/WorkspaceContext'
import { addObject, deleteObject, editObject } from '../../services/scene'
import { useWorkspaceStore } from '../../stores/workspaceStore'
import type { SceneObject } from '../../types/scene'
import { Button } from '../ui/Button'
import { Dialog } from '../ui/Dialog'

type Node = { obj: SceneObject; children: Node[] }

/** Nest objects by parent_id; orphans (parent not in scene) become roots. Sorted by name. */
export function buildTree(objects: SceneObject[]): Node[] {
  const nodes = new Map(objects.map((o) => [o.object_id, { obj: o, children: [] as Node[] }]))
  const roots: Node[] = []
  for (const node of nodes.values()) {
    const parent = node.obj.parent_id ? nodes.get(node.obj.parent_id) : undefined
    if (parent) parent.children.push(node)
    else roots.push(node)
  }
  const sort = (list: Node[]) => {
    list.sort((a, b) => a.obj.name.localeCompare(b.obj.name))
    list.forEach((n) => sort(n.children))
  }
  sort(roots)
  return roots
}

const countDescendants = (n: Node): number => n.children.reduce((sum, c) => sum + 1 + countDescendants(c), 0)

function ShapeIcon({ obj }: { obj: SceneObject }) {
  const c = (obj.component_type ?? obj.name).toLowerCase()
  if (c.includes('sphere') || c.includes('ball')) return <Circle size={13} />
  if (c.includes('cylinder') || c.includes('leg') || c.includes('cone')) return <Cuboid size={13} />
  return <Square size={13} />
}

export function SceneTree() {
  const { projectId, inventory, objectMap } = useWorkspaceScene()
  const selectedId = useWorkspaceStore((s) => s.selectedId)
  const busy = useWorkspaceStore((s) => !!s.activeJob)
  const select = useWorkspaceStore((s) => s.select)
  const run = useModelingAction()
  const tree = useMemo(() => buildTree(inventory.objects), [inventory.objects])
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Node | null>(null)

  const toggleCollapsed = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const commitRename = () => {
    if (!renaming) return
    const obj = objectMap.get(renaming.id)
    const name = renaming.value.trim()
    setRenaming(null)
    if (obj && name && name !== obj.name) {
      void run(`Renamed to ${name}`, () => editObject(projectId, obj.object_id, { name }))
    }
  }

  const onRenameKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') commitRename()
    if (e.key === 'Escape') setRenaming(null)
  }

  const toggleVisible = (obj: SceneObject) =>
    run(obj.visible ? `Hid ${obj.name}` : `Showed ${obj.name}`, () =>
      editObject(projectId, obj.object_id, { visible: !obj.visible }),
    )

  const doDelete = () => {
    if (!confirmDelete) return
    const { obj } = confirmDelete
    const recursive = confirmDelete.children.length > 0
    setConfirmDelete(null)
    void run(`Deleted ${obj.name}`, () => deleteObject(projectId, obj.object_id, recursive)).then(
      (ok) => ok && select(null),
    )
  }

  const renderNode = (node: Node, depth: number) => {
    const { obj } = node
    const chosen = selectedId === obj.object_id
    const isCollapsed = collapsed.has(obj.object_id)
    return (
      <div key={obj.object_id} role="none">
        <div
          role="treeitem"
          aria-selected={chosen}
          aria-expanded={node.children.length ? !isCollapsed : undefined}
          className={`tree-row child ${chosen ? 'chosen' : ''} ${obj.visible ? '' : 'is-hidden'}`}
          style={{ paddingLeft: 10 + depth * 14 }}
          onClick={() => select(obj.object_id)}
          onDoubleClick={() => !busy && setRenaming({ id: obj.object_id, value: obj.name })}
        >
          {node.children.length ? (
            <button
              className="tree-toggle"
              aria-label={isCollapsed ? 'Expand' : 'Collapse'}
              onClick={(e) => { e.stopPropagation(); toggleCollapsed(obj.object_id) }}
            >
              {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
            </button>
          ) : (
            <span className="tree-indent"></span>
          )}
          <span className="tree-shape"><ShapeIcon obj={obj} /></span>
          {renaming?.id === obj.object_id ? (
            <input
              className="tree-rename"
              aria-label="Object name"
              autoFocus
              maxLength={120}
              value={renaming.value}
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setRenaming({ id: obj.object_id, value: e.target.value })}
              onKeyDown={onRenameKey}
              onBlur={commitRename}
            />
          ) : (
            <span className="tree-name" title={`${obj.name} (${obj.object_id})`}>{obj.name}</span>
          )}
          <button
            className="visibility-btn"
            disabled={busy}
            title={obj.visible ? 'Hide' : 'Show'}
            onClick={(e) => { e.stopPropagation(); void toggleVisible(obj) }}
          >
            {obj.visible ? <Eye size={13} /> : <EyeOff size={13} />}
          </button>
          <button
            className="visibility-btn"
            disabled={busy}
            title="Delete"
            onClick={(e) => { e.stopPropagation(); setConfirmDelete(node) }}
          >
            <Trash2 size={13} />
          </button>
        </div>
        {!isCollapsed && node.children.map((c) => renderNode(c, depth + 1))}
      </div>
    )
  }

  const descendants = confirmDelete ? countDescendants(confirmDelete) : 0
  return (
    <div className="inspector-section">
      <div className="section-heading">
        SCENE HIERARCHY{' '}
        <button disabled={busy} onClick={() => void run('Added cube', () => addObject(projectId, 'cube'))} title="Add cube">
          <Plus size={14} />
        </button>
      </div>
      <div className="scene-tree" role="tree" aria-label="Scene hierarchy">
        <div className="tree-row root">
          <ChevronDown size={13} /><Folder size={14} /><span>Scene Collection</span>
          <span className="tree-number">{inventory.objects.length}</span>
        </div>
        {tree.map((n) => renderNode(n, 0))}
        {inventory.objects.length === 0 && <div className="tree-empty">No objects yet</div>}
      </div>
      <Dialog
        open={!!confirmDelete}
        title={`Delete “${confirmDelete?.obj.name ?? ''}”?`}
        description={
          descendants
            ? `This also deletes ${descendants} child object${descendants > 1 ? 's' : ''}. Earlier versions keep them.`
            : 'Earlier versions keep the object, so you can restore it from history.'
        }
        onClose={() => setConfirmDelete(null)}
      >
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmDelete(null)}>Cancel</Button>
          <Button variant="danger" data-autofocus onClick={doDelete}>Delete</Button>
        </div>
      </Dialog>
    </div>
  )
}
