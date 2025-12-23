# core/

This folder contains all domain-specific logic for the GIMS system.

Anything here defines what the system **does**: how nouns, verbs, adjectives, etc. behave, interact, and process data.

These files must:
- Contain no CLI or GUI logic
- Avoid any `print()` or `input()` calls
- Only raise exceptions or return values for the UI layers to handle