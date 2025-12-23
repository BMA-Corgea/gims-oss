# GIMS Project  
## Generalized Information Management System

GIMS is a flexible, schema-driven information management system for tracking any workflow. It provides a common “grammar” of nouns and verbs to log anything humans do to anything.

In plain language, GIMS lets you define the things you track and the actions you perform, then automatically keeps detailed logs, generates documents, and maintains an audit trail for every step.

Unlike traditional **Laboratory Information Management Systems (LIMS)** or **Enterprise Resource Planning (ERP)** software, GIMS is generalized and highly adaptable. You configure your own nouns (entities), verbs (actions), and rules via simple schemas, and the system will automatically produce all logs, results, and audit trails for those workflows.

This means GIMS can fit into any domain — from lab testing to HR onboarding — without rigid pre-built modules. It is open-source, modular, and ready to use today, but also easy to extend or fork to suit specialized needs.

> For regulated industries, optional compliance modules are available separately (see below).

---

## Why GIMS?  
### Key Features & Differences

### Schema-Driven & Flexible
GIMS doesn’t hard-code a specific workflow. All entities and processes are defined in JSON (or SQL) schemas.

You can model your lab tests, approval processes, or any operations by editing configuration files — no complex coding required to change the data model. This provides extreme flexibility compared to traditional LIMS/ERP systems with fixed schemas.

### Grammar-Based Architecture
GIMS uses a grammar metaphor — think of each workflow like a sentence.

You have:
- **Nouns** (items / things)
- **Verbs** (actions on those items)
- **Adjectives**, **adverbs**, etc.

This intuitive structure makes it easy to reason about processes.  
The result is a system that can track *anything humans do to anything* in a natural way.

### Modular & Composable
GIMS is built as a collection of focused modules for configuration, execution, auditing, and more, all interoperating through the schema.

You can use only what you need and extend with new modules. Each piece is small and focused, keeping the system understandable and maintainable.

### Traceability & Auditability
Every action (every verb performed on a noun) produces a structured record of inputs, outputs, and context.

You can always reconstruct lineage:
- who did what
- when
- how

Every data change is logged with user and timestamp for full accountability.

### Automation & Extensibility
GIMS allows custom logic at key points. You can plug in Python scripts as parsers or handlers to automate calculations or generate artifacts.

Examples include:
- auto-calculating results from raw instrument data
- generating a PDF report when a task is completed

GIMS calls these scripts automatically at the right time.

### Role-Based Permissions
Built-in role management lets you control who can view or do what.

You can:
- define roles (Technician, Manager, Auditor, etc.)
- restrict access to modules or specific nouns/verbs
- enforce multi-step sign-offs

Everything is permission-aware and recorded.

---

## In Short
GIMS is designed to be as flexible as a spreadsheet, but with the rigor and traceability of an enterprise system.

You define the structure — GIMS handles data integrity, workflow execution, and record-keeping automatically.

---

## GIMS Grammar  
### Nouns, Verbs, Adjectives, Adverbs, and More

To make the system intuitive, GIMS models your domain using parts of speech:

- **Nouns** — The things you track  
  (Sample, Batch, Employee, Product, etc.)

- **Verbs** — Actions performed on nouns  
  (Test, Approve, Ship, Pay, etc.)

- **Adjectives** — Descriptors or attributes of nouns  
  (tags, statuses, lists, flags)

- **Adverbs** — Context for actions  
  (instrument, operator, temperature, environment)

- **Conjunctions** — Overrides or special run flags  
  (cancelled, needs_retest, superseded_by)

- **Prepositional Phrases** — Intents that trigger artifacts  
  (`for_COA`, `for_Label`, `for_Report`, etc.)

### Example
> “I **[TEST]** this **[SAMPLE]** on **[HPLC]** **[for_COA]**”

This performs a Test on a Sample using the HPLC instrument and generates a Certificate of Analysis automatically.

The same grammar adapts across domains:
> “I **[APPROVE]** this **[INVOICE]** **[for_Report]**”

---

## Real-World Use Cases

### Laboratory Sample Management (LIMS)
Define Sample and Batch as nouns, Test as a verb, and HPLC as an adverb.

Using a `for_COA` phrase handler, GIMS generates Certificates of Analysis automatically and maintains a complete audit trail.

### Quality Assurance & Manufacturing
Track Product Lots and perform Inspection or Assembly steps with station/operator context.

Exceptions like failed inspections can be handled via conjunctions like `needs_retest` or `cancelled`.

### HR & Onboarding
Track Candidates or Employees through Onboard or Training verbs.

Automatically generate onboarding completion documents using phrase handlers.

### Financial Workflows
Manage Invoices or Purchase Orders with Approve, Pay, or Audit verbs.

Role-based permissions enforce approvals and all actions are logged.

---

## How It Works  
### Typical Workflow

1. **Define Your Schema**  
   Configure nouns, verbs, adjectives, adverbs, conjunctions, and phrases via JSON or GUI editors.

2. **Create Instances**  
   Enter real data using the Noun Workbench or import via CSV/Excel.

3. **Perform Actions**  
   Use the Verb Workbench to execute actions. Each action creates a structured run log.

4. **Capture Context & Exceptions**  
   Context and deviations are recorded automatically.

5. **Generate Artifacts Automatically**  
   Phrase handlers generate PDFs, labels, reports, or trigger integrations.

6. **Oversight and Audit**  
   Use Runlog, Investigation, Audit, and Archive tools to inspect and manage data.

Every action is user- and timestamp-stamped.

---

## Roles & Permissions

- Define user roles (Technician, Supervisor, Auditor, Admin)
- Restrict access by module, noun, or verb
- Require sign-offs for critical steps
- Maintain full accountability and auditability

Advanced compliance features (e.g. cryptographic signatures) may exist in separate modules.

---

## Extensibility & Customization

- **Custom Parsers** — Interpret raw data files automatically
- **Phrase Handlers** — Generate documents or trigger actions
- **External Integrations** — Structured outputs enable easy system integration
- **Modular Codebase** — Add new modules or API routes cleanly

---

## Getting Started (Quickstart)

1. Install Python 3.8+
2. Clone the repository:
   ```bash
   git clone https://github.com/BMA-Corgea/GIMS-OSS.git
   cd GIMS-Project
3. Create a virtual environment:
    python3 -m venv .venv
    source .venv/bin/activate
4. Install dependencies:
    pip install fastapi uvicorn
5. Run the server:
    uvicorn gui.gui_main:app --reload
6. Open http://localhost:8000
    Demo users
        bob@123.com / Abc123!!
        alice@123.com / Abc123!!
        jeff@123.com / Abc123!!

## Project Structure
    /api       → FastAPI routers and resolvers
    /core      → Core logic (validation, lineage, search)
    /gui       → GUI backend
    /projects  → Example workflows (schemas + data)
    /docs      → Documentation
    /tools     → Minimal AWS connectivity utilities

## Roadmap
    GIMS in its current form is a nearly terminal project for me without feedback.
        The primary future enhancement is improved time awareness:

        alarms

        due dates

        search optimizations

## Contributing & Support
    Contributions are welcome:

        bug reports

        feature requests

        pull requests

        Follow PEP8, keep logic in /core, and keep GUI layers thin.

## Closing
    Closing

        GIMS is a general engine. Today it ships configured for lab workflows, but the same grammar models HR, finance, or any operational process.

        One platform. Many domains.

        Happy tracking.