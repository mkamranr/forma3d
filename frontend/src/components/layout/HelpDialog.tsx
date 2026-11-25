import { Camera, Download, History, ImagePlus, Keyboard, MessageSquareText, Mouse, MousePointer2 } from 'lucide-react'
import type { ReactNode } from 'react'
import { Dialog } from '../ui/Dialog'

function Section({ icon, title, children }: { icon: ReactNode; title: string; children: ReactNode }) {
  return (
    <section className="help-section">
      <h3>{icon} {title}</h3>
      {children}
    </section>
  )
}

const SHORTCUTS: [string, string][] = [
  ['Enter', 'Send the message (Shift+Enter for a new line)'],
  ['Ctrl / ⌘ + Z', 'Undo (back one version)'],
  ['Ctrl / ⌘ + Shift + Z, Ctrl + Y', 'Redo'],
  ['Shift / Ctrl / ⌘ + click', 'Add or remove an object from the selection'],
  ['Ctrl / ⌘ + V in the chat box', 'Paste a photo to build from'],
]

const MOUSE: [string, string][] = [
  ['Left click', 'Select an object'],
  ['Left drag', 'Orbit around the model'],
  ['Right drag', 'Pan'],
  ['Scroll', 'Zoom'],
]

export function HelpDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="How forma3D works" description="Describe it, refine it, export it. Every change is built in Blender and saved as a version." size="lg">
      <div className="help-grid">
        <Section icon={<MessageSquareText size={14} />} title="Talk to the assistant">
          <ul>
            <li>Start with what you want: <em>“Create a modern wooden desk with two drawers”</em>.</li>
            <li>Refine in plain words: <em>“make the legs black metal”</em>, <em>“10 cm taller”</em>, <em>“walnut top”</em>.</li>
            <li>Ask about the scene: <em>“how wide is the desk?”</em> or say <em>“undo”</em> / <em>“go back to version 2”</em>.</li>
            <li>When a request is ambiguous the assistant asks which object you mean.</li>
          </ul>
        </Section>
        <Section icon={<ImagePlus size={14} />} title="Build from a photo">
          <ul>
            <li>Click <strong>+</strong> in the chat box (or paste / drop an image) and send.</li>
            <li>Add words to steer it: <em>“like this, but in oak and without armrests”</em>.</li>
            <li>Best results: one object, plain background, three-quarter view, filling the frame.</li>
            <li>Open <strong>What I saw in the photo</strong> under your message to check what was understood.</li>
          </ul>
        </Section>
        <Section icon={<MousePointer2 size={14} />} title="Edit directly">
          <ul>
            <li>Select objects in the viewport or the scene tree; the assistant knows what is selected (<em>“make it red”</em>).</li>
            <li>Move and resize with the gizmo tools, or type exact values in <strong>Properties</strong>.</li>
            <li>Change colour, roughness, glass, glow and textures in <strong>Materials</strong>.</li>
          </ul>
        </Section>
        <Section icon={<History size={14} />} title="Versions">
          <ul>
            <li>Every change is a version. <strong>Versions</strong> lets you compare two, restore one or jump to its chat.</li>
            <li>A change made after restoring starts a new branch; nothing is ever lost.</li>
          </ul>
        </Section>
        <Section icon={<Camera size={14} />} title="Render & export">
          <ul>
            <li><strong>Render</strong> makes a path-traced image (draft for quick looks, high for final images).</li>
            <li><strong>Export</strong> downloads GLB, glTF, OBJ or STL in m, cm or mm; or just ask in chat.</li>
          </ul>
        </Section>
        <Section icon={<Mouse size={14} />} title="Viewport">
          <dl className="help-keys">
            {MOUSE.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </Section>
        <Section icon={<Keyboard size={14} />} title="Keyboard shortcuts">
          <dl className="help-keys">
            {SHORTCUTS.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </Section>
        <Section icon={<Download size={14} />} title="Good to know">
          <ul>
            <li>Builds take a few seconds to a minute; photos add up to a minute for the vision model.</li>
            <li>Objects are made from simple shapes, booleans and modifiers, so organic or very detailed forms are approximated.</li>
          </ul>
        </Section>
      </div>
    </Dialog>
  )
}
