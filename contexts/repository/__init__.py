"""
contexts/repository/ — the product core: blocks, their versions, their approval
(ADR-030, CHY-105 ✅).

Owns: context_blocks
Also holds: composition and routing, which have no table of their own and read
only blocks. Separating the read side from the write side across a context
boundary would cost a translation layer and buy nothing (ADR-030 point 3).

Its own language: block, version, approval state, composition

    schema.py    transport shapes — domain/blocks.py + domain/compose.py merged
    block.py     the rules, above all the transition table of the review workflow
    store.py     the only place with SQL
    compose.py   the composition read path and its Redis layer (ADR-010)
    service.py   use cases — own the transaction, call the above

No events.py: this context has no wire vocabulary of its own. What happened to
a block is recorded in the kernel's audit log, and the action names live there.
A context fills the modules it needs.

Published interface: service, schema, errors. `compose` is reached through
service.compose_context — the guard rule context_encapsulation caught two
endpoints importing compose.py directly.

**What the split bought:** the approval lifecycle is callable without HTTP.
Before CHY-105 the only way to submit, approve or reject a block was an HTTP
request, so neither the CLI nor a background job nor the runner of ADR-028
could establish the central fact of this product — what is approved. ADR-030
point 4 depends on this interface existing: orchestration uses it instead of
building a second approval mechanism, because two mechanisms would be two
truths about what is approved.

api/v1/blocks.py went from 621 lines to 202, `context_blocks` from four files
with SQL to one, and the transition table from four sets of function arguments
to one table — which is what made it checkable against migration 013 rather
than merely consistent with it by habit.
"""
