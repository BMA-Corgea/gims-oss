# Open-core build

This repository is generated from the private GIMS source by an export tool. It is the GIMS
codebase with three subsystems removed:

1. **Login / authentication** — replaced by an open single-user session. `/login/{project}/auth/me`
   returns one fully-authorized local user; there is no login form and no accounts database.
2. **Roles / permissions** — the account/role admin console and all RBAC enforcement are removed.
   Every action is permitted.
3. **Compliance (21 CFR Part 11)** — the HMAC-chained audit/compliance trail, e-signatures,
   gate sign-off enforcement, sealed exports, and the compliance/audit log viewer are removed.
   Workflow gates still exist as checkpoints but complete without a signature. The trusted-time
   status endpoint (`/compliance/time`) is kept because the runlog clock badge and the Duration
   time-adjective use it; it involves no compliance data.

The data-integrity **audit** module (lint/consistency checks over noun instances) and **lineage**
tracing are NOT compliance features and remain fully present.

Do not deploy this build as a shared/multi-user service — it performs no authentication or
authorization.
