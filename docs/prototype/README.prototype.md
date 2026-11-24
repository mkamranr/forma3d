# Forma3D Frontend Prototype

A React + TypeScript + Vite frontend prototype for a conversational AI 3D modeling studio.

## Included
- Responsive studio layout with AI conversation, live 3D viewport, scene hierarchy, object properties and materials.
- Interactive Three.js scene using React Three Fiber.
- Starter desk scene with selectable objects.
- Add cube, sphere and cylinder primitives.
- Edit object position, scale, visibility and material color.
- Demo natural-language commands:
  - `Add a sphere`
  - `Add a cylinder`
  - `Make the tabletop wider`
  - `Change the tabletop to blue`
  - `Remove selected object`
- Demo version snapshots, recent activity and export feedback.

## Run locally

Requires Node.js 20 or later.

```bash
npm install
npm run dev
```

Open the URL printed by Vite (normally http://localhost:5173).

## Production build

```bash
npm run build
npm run preview
```

## Docker

Build and run from the project root:

```bash
docker build -t forma3d-frontend .
docker run --rm -p 8080:80 forma3d-frontend
```

Then open http://localhost:8080.

## Important prototype boundary

This is a frontend-only prototype. AI responses and natural-language edits are simulated in the browser. The Blender status is intentionally marked `DEMO`; no Blender worker, LLM, persistence API, authentication or actual GLB export is connected yet.

Recommended next integration points:
- Replace `sendPrompt()` command parsing with `POST /api/v1/projects/{project_id}/messages`.
- Consume assistant and scene events over SSE.
- Replace the demo export handler with the backend GLB export endpoint.
- Persist project, scene and version state through the backend.
- Keep Blender execution on the isolated worker service; do not run Blender in the browser.
