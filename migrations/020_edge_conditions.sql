-- Migration 020: edge conditions as data, not as an expression (CHY-130)
--
-- `workflow_connections.condition` decides which arm of an XOR branch is
-- taken. Nothing evaluated it, and nothing said how one is written. Model and
-- schema had also disagreed since day one:
--
--     arc42 §5.6      condition: JSONB | null   -- a structured condition
--     001_initial     condition TEXT            -- "optionaler Guard-Ausdruck"
--
-- The disagreement went unnoticed for the reason such things do: with no
-- evaluator, both readings behaved identically.
--
-- The decision is JSONB, which is what the model said. A structured condition
-- is checkable *before* it runs, needs no parser and no sandbox, and the
-- validator can reject a malformed one at activation instead of failing a run
-- half way through. An expression language is the kind of decision that cannot
-- be taken back: the moment one workflow uses a feature of it, the language is
-- a contract.
--
-- The shape, deliberately small:
--
--     {"field": "risk_level", "op": "eq", "value": "high"}
--     {"all": [ … ]}   {"any": [ … ]}   {"not": { … }}
--
-- Fields address the source node's output. Nesting is allowed because a real
-- branch condition is rarely one comparison; arithmetic is not, because the
-- moment a condition can compute, a reader can no longer tell what a workflow
-- does by reading it.
--
-- Safe as a type change: workflow_connections holds zero rows in dev, test and
-- prod — checked at the host on 2026-09-04 for CHY-82 and unchanged since,
-- because the endpoints that write it were only merged today. The USING clause
-- still handles data, so the migration stays correct if that ever changes.

ALTER TABLE workflow_connections
    ALTER COLUMN condition TYPE JSONB
    USING CASE
        WHEN condition IS NULL OR condition = '' THEN NULL
        -- A legacy string becomes an object that says what it was, rather than
        -- being dropped or parsed. Parsing would guess; dropping would lose a
        -- branch silently, and a branch that quietly disappears is the failure
        -- arc42 §5.6 names by hand.
        ELSE jsonb_build_object('unparsed', condition)
    END;

ALTER TABLE workflow_connections DROP CONSTRAINT IF EXISTS workflow_connections_condition_object;
ALTER TABLE workflow_connections ADD CONSTRAINT workflow_connections_condition_object
    CHECK (condition IS NULL OR jsonb_typeof(condition) = 'object');

COMMENT ON COLUMN workflow_connections.condition IS
    'Structured guard, or NULL for an unconditional edge (CHY-130). '
    '{"field": …, "op": …, "value": …} with all/any/not for nesting. JSONB and '
    'not an expression: checkable before it runs, no parser, no sandbox. The '
    'validator refuses a malformed one at activation rather than failing a run '
    'half way through.';
