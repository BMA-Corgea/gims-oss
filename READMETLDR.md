# GIMS — TL;DR

**GIMS (Generalized Information Management System)** is a schema-driven engine for tracking **anything humans do to anything**.

Instead of hard-coding workflows (like traditional LIMS or ERP systems), GIMS lets you **define your own nouns (things), verbs (actions), and rules** in simple JSON schemas. The system then automatically handles logging, traceability, permissions, and artifact generation.

---

## What makes GIMS different?

- **Generalized by design**  
  Not a lab system, not an HR system, not finance software — but a *grammar* that can model all of them.

- **Schema-driven**  
  Change workflows by editing configuration files, not rewriting code.

- **Grammar-based architecture**  
  Nouns (entities), verbs (actions), adjectives (attributes), adverbs (context), and phrases (intent) form a readable, intuitive model of work.

- **Automatic audit trail**  
  Every action /can/ be logged with full context: who did what, when, how, and why (but I'm trying to sell that functionality).

- **Modular & extensible**  
  Add Python scripts to parse data, run calculations, or generate documents (PDFs, reports, labels) automatically.

- **Role-aware**  
  Built-in roles and permissions control who can do what — all enforced and recorded.

---

## What GIMS is *not*

- ❌ Not a rigid, pre-built LIMS or ERP  
- ❌ Not tied to a single industry  
- ❌ Not a compliance product in this repository  

(Advanced compliance modules exist separately.)

---

## Example sentence (how GIMS thinks)

> “I **TEST** this **SAMPLE** on **HPLC** **for_COA**”

That single sentence becomes:
- a structured run log
- captured instrument context
- role-checked execution
- automatic Certificate of Analysis generation

The same grammar works for HR onboarding, manufacturing QA, finance approvals, and more.

---

## Use cases

- Laboratory sample tracking (LIMS)
- Manufacturing & QA workflows
- HR onboarding & training
- Finance approvals & audits
- Any process that needs traceability

---

## Getting started (very fast)

```bash
git clone https://github.com/BMA-Corgea/GIMS-OSS.git
cd GIMS-OSS
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn gui.gui_main:app --reload
login as bob@123.com pw Abc123!!