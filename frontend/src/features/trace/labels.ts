/** Friendly names and one-line descriptions of the LangGraph nodes. */
export const NODE_INFO: Record<string, { label: string; about: string }> = {
  load_context: { label: 'Load context', about: 'Reads your message, the selection, photos and the current scene.' },
  inspect_scene: { label: 'Inspection agent', about: 'Summarises the scene so the other agents know what exists.' },
  vision: { label: 'Vision agent', about: 'Turns an attached photo into a description of the object (image-to-3D).' },
  conversation: { label: 'Conversation agent', about: 'Works out what you want: build, change, answer, export, undo or ask back.' },
  answer: { label: 'Answer', about: 'Replies directly (questions, export and render requests).' },
  version_manager: { label: 'Version manager', about: 'Undo, redo or restore a version, without Blender.' },
  planning: { label: 'Planning agent', about: 'Designs the parts, sizes, positions and materials.' },
  ask: { label: 'Ask a question', about: 'The request was ambiguous, so the assistant asks you.' },
  wait_answer: { label: 'Wait for answer', about: 'Paused until your next message answers the question.' },
  modeling: { label: 'Modeling', about: 'Compiles the plan into modeling operations and validates every one.' },
  execute: { label: 'Blender build', about: 'Blender applies the operations in an isolated job (not saved yet).' },
  quality: { label: 'Quality agent', about: 'Checks sizes, contacts, materials and that nothing else changed.' },
  revise: { label: 'Revision agent', about: 'Fixes the problems the quality check found, then rebuilds.' },
  commit: { label: 'Save version', about: 'Saves the checked result as a new version.' },
  fail: { label: 'Roll back', about: 'Discards the result; the scene stays unchanged.' },
  respond: { label: 'Reply', about: 'Sends the answer to the chat.' },
}

/** Nodes that call the language model. */
export const LLM_NODES = new Set(['vision', 'conversation', 'planning', 'revise'])

export const nodeLabel = (id: string) => NODE_INFO[id]?.label ?? id.replace(/_/g, ' ')
