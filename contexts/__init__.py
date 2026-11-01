"""
contexts/ — zone 2 of ADR-030.

Four vertical slices: repository, orchestration, learning, pilots. A fifth,
governance, was removed with ADR-042. No context imports another; the only
shared ground is `kernel/`.

The directories start empty on purpose. The layout is settled (below), and five
tickets each inventing a layout would be five chances to get five different
answers. They were filled one at a time — CHY-104 pilots, CHY-105 repository, CHY-106
governance, CHY-107 orchestration, CHY-108 learning — with a green suite in
between. All five existed until ADR-042 removed governance.

When the layout was settled, two of them carried real code and three were
skeletons — the history below. Orchestration has since received the workflow
runner of ADR-028. `pilots` and `repository` were extractions: 846 and 621 lines of rules,
SQL and lifecycle came out of `api/`. `governance`, `orchestration` and
`learning` had nothing to extract — their tables existed, their routes all
answered 501, and no code touched them. Doing them anyway settles the layout **before**
CHY-65 and the ADR-028 runner write into it, because the first two cuts showed
that the layout is the expensive part.

Layout of a context:

    contexts/<name>/
    ├── schema.py     Pydantic — transport shapes
    ├── <aggregate>.py state machine and rules — no IO, no HTTP
    ├── events.py     what happened
    ├── errors.py     domain errors — never HTTPException
    ├── store.py      data access, the only place with SQL
    └── service.py    use cases: own the transaction, call all of the above

"The aggregate decides, the use case executes" is the point of the split. It
makes the rules testable without a database — which is where coverage has been
failing. pilots.py was the proof both ways: at 54 % every test needed the full
stack, because rule and execution were the same function; afterwards 57 tests
of its rules run in 0.06 s against nothing at all.

Published interface: service, schema, errors. Zone 1 may import those and
nothing else; the guard rule is `context_encapsulation`.
"""
