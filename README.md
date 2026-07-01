# GIMS — Generalized Information Management System (Open Core)

![Python](https://img.shields.io/badge/python-3.12-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688)
![React](https://img.shields.io/badge/UI-React%20%2B%20Glide-1f6feb)
![License](https://img.shields.io/badge/license-see%20LICENSE-lightgrey)

> **Try it live:** https://gims-demo.vercel.app  ·  **Case study:** the portfolio hub (links in the demo)

GIMS is a **schema-driven, domain-agnostic information-management platform**. You describe your
world as a small grammar — **nouns** (the things you track), **verbs** (actions on them),
**adjectives** (descriptors), **adverbs** (context), **conjunctions** (run overrides), and
**prepositional phrases** (intent that triggers an artifact) — and the engine generates the
data model, the workbenches, the run logs, and the artifacts for you. No hard-coded modules per
industry: a laboratory LIMS, an HR onboarding tracker, and a financial-approval workflow are all
just different schemas over the same engine.

```
I [TEST] this [SAMPLE] on [HPLC] [for_COA]  ->  a run that produces a Certificate of Analysis
```

---

## This is the open core

This repository is the **open-core edition** of GIMS: the complete schema/grammar engine and
every workbench, editor, dashboard, guided tour, and skin — a fully runnable, **single-user**
GIMS. Three things are intentionally **not** in this build:

| Removed | What it is | Where to see it |
|---|---|---|
| **Login / authentication** | Multi-user accounts, JWT/cookie sessions | — |
| **Roles / permissions** | Per-project RBAC, feature-tag scopes, admin console | — |
| **Compliance** | 21 CFR Part 11 HMAC audit chain, e-signatures, gate sign-off, sealed exports | **[Compliance Relay demo](https://gims-compliance-relay-demo.vercel.app)** |

Those make up the proprietary layer that turns GIMS into a regulated-industry product; the
Compliance Relay demo shows them in action. In this build every request is treated as a single
fully-authorized local user, workflow gates complete without a sign-off, and no audit trail is
written. **Run it locally to explore the engine — do not deploy it as a shared service.**

---

## Core concepts

- **Nouns** — the entities you track (samples, invoices, employees, products).
- **Verbs** — actions performed on nouns (test, approve, ship, pay), each with its own run log.
- **Adjectives** — descriptors attached to nouns (tags, references, lists, live Duration tickers).
- **Adverbs** — context for a run (instrument, operator, temperature).
- **Conjunctions** — overrides on a run (cancelled, needs_retest, superseded_by).
- **Prepositional phrases** — intent that triggers an artifact (`for_COA`, `for_Report`, `for_Label`).

Everything is defined in JSON schemas per project; Python parsers/phrase-handlers automate
calculations and artifact generation.

---

## Architecture

A layered FastAPI backend with a React + Glide front end:

```
api/        HTTP/JSON routers (the API boundary) + api/app.py ASGI entrypoint
core/       the engine — words (nouns/verbs/adjectives/...), handlers, lineage, audit,
            orchestration (nodes/modules/registry), storage
nodes/      orchestration "nodes" (page nodes, infra nodes) wired into modules
modules/    module definitions that compose nodes into pages
frontend/   React sources (built into static/lib by build.mjs)
static/     built JS/CSS assets, icons, the guided-tour engine, skins
projects/   example projects (LIMS-System, DurationDemo, RunlogTest)
tools/      utilities
tests/      pytest suite
```

The dependency direction is strict — `core/` never imports from `api/`, `nodes/`, or `modules/`.

---

## Quickstart

```bash
git clone https://github.com/BMA-Corgea/gims-oss
cd gims-oss
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn api.app:app --host 127.0.0.1 --port 8100
```

Then open <http://127.0.0.1:8100/> — you land on the launcher. No login required. Pick the
**LIMS-System** demo project to explore configured nouns/verbs, run the runlog workbench, and
try the guided tours.

`./start.sh` does the same with venv bootstrapping.

---

## What you can do in this build

- Define nouns/verbs/adjectives/adverbs and run the workbenches end-to-end.
- Drive the React runlog grid, archive workbench, and data dumps.
- Use custom Python parsers and prepositional-phrase artifact generation.
- Switch skins (the modular theme system) and take the guided product tours.
- Run the pytest suite (`pytest`) — the auth/compliance-specific tests are not part of this build.

---

## License

See [LICENSE.txt](LICENSE.txt).
