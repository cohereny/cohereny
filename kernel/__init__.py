"""
kernel/ — zone 3 of ADR-030.

Identity, roles, scopes, audit. Used by every context, uses none of them.

The rule is strict and scripts/architecture.py enforces it: the kernel imports
**neither a context nor `api`**. If it did it would stop being a foundation and
become another layer in the middle — which is exactly what `core/` turned into,
and what CHY-101 is unpicking.
"""
