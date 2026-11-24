# Architecture decisions

Living record of decisions beyond `project-specification.md`. Update as phases land.

## Phase 1

- **Monorepo root** is `forma3d/` (spec §19 calls it `3d-studio/`). Python images build with the
  repo root as context so `shared/` can be installed into every service.
- **Auth**: argon2 password hashing (pwdlib). 15-minute HS256 JWT access tokens held in memory by the
  SPA. Opaque refresh tokens (sha256-hashed in `refresh_tokens`) in an httpOnly, SameSite=strict cookie
  scoped to `/api/v1/auth`. Each refresh rotates the token; presenting a rotated/revoked token revokes
  the entire login family (theft detection). Logout revokes the family.
- **Ownership**: every project query filters on `user_id`; cross-user access returns 404 to avoid
  disclosing ids.
- **Deletion**: projects are soft-deleted (`status='deleted'`, `deleted_at`). A retention job will purge
  files and rows (Phase 8).
- **Schema**: all §11 tables are created in migration `0001` (plus `refresh_tokens`, and
  `scene_versions.message_id` to link a version to its conversation message). The circular FK
  `projects.current_version_id → scene_versions.id` is added after both tables exist.
  Types are portable (`Uuid`, JSON→JSONB on PostgreSQL) so unit tests run on SQLite.
- **Rate limiting**: fixed-window counters in Redis (`ratelimit:*`), applied to auth endpoints now and
  job submission later.
- **Logging**: structlog JSON with a correlation id (`X-Request-ID`, validated or generated) bound per
  request and echoed in the response; secret-like keys are redacted.
- **Networking**: `edge` (published frontend), `app` (internal: nginx↔backend), `data` (internal:
  backend, workers, postgres, redis), `llm` (egress for agent-worker only). Blender worker has no egress.
- **Frontend**: the prototype's single `App.tsx` was split into `components/*`, `features/*`,
  `stores/*`; its CSS was split into `src/styles/*.css` with byte-identical rules (verified).
  Google Fonts were replaced by `@fontsource` packages; drei's CDN HDRI preset was replaced with local
  Lightformers. The workspace route is code-split so three.js is not in the auth/dashboard bundle.
  Original prototype files are kept in `docs/prototype/` for reference.
