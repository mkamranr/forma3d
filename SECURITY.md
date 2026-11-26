# Security policy

forma3D runs AI-generated plans in Blender, stores user projects and talks to an LLM server, so security
reports are taken seriously.

## Supported versions

Only the latest commit on `main` receives fixes. Please check that a problem still exists there before you
report it.

## Reporting a vulnerability

**Do not open a public issue for security problems.**

Report them privately through GitHub's
[private vulnerability reporting](https://github.com/mkamranr/forma3d/security/advisories/new)
(repository → **Security** → **Report a vulnerability**). Only the maintainer can see the report.

Please include:
- what is affected (component, endpoint, file) and the version or commit;
- steps to reproduce, or a proof of concept;
- the impact as you see it, such as data exposure, code execution, privilege escalation or denial of service.

You can expect an acknowledgement within 7 days and a status update at least every 14 days until the issue is
resolved. Once a fix is released, the advisory is published, and you are credited unless you prefer not to be.

## Scope

Especially interesting:
- anything that lets LLM output or user input run code outside the allowlisted modeling DSL, for example in
  Blender, the workers or the backend;
- reading or changing another user's projects, versions, assets, conversations or agent traces;
- bypassing admin-only areas (Configuration, Agent activity, LLM prompts);
- reaching internal endpoints (`/internal/*`, `/metrics`) or internal networks from outside;
- leaking secrets (JWT secret, internal token, LLM API key) through the API, logs or exports.

Out of scope:
- vulnerabilities in a third-party LLM server, or in your own reverse proxy or host configuration;
- denial of service that needs valid admin credentials;
- reports from automated scanners with no demonstrated impact.

## Deployment hardening

See [docs/deployment.md](docs/deployment.md#8-hardening-built-in) for what the containers already enforce, and
[docs/operations.md](docs/operations.md) for rotating secrets.
