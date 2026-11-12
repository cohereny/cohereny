-- 025_drop_merge_requests.sql — one review path, not two (CHY-65, ADR-042)
--
-- `merge_requests` and `merge_request_comments` came with 001_initial, as the
-- storage ADR-005 decided on (2026-06-30): diffs, versions and review state in
-- tables of their own. Nothing ever wrote to them — the seven routes under
-- /v1/merge-requests answered 501, and on 2026-09-29 both tables held zero
-- rows in dev, test and prod.
--
-- What ADR-005 needed storage for has since been built on context_blocks
-- itself: a version per change (CHY-30), draft → pending_review → active with
-- approval and a reasoned rejection (013, CHY-32), the privacy ratchet (016,
-- CHY-62). A merge request carrying its own diff, applied on merge, would be a
-- second way to change a block next to the first — two sources for one truth.
--
-- Decided on 2026-09-29 (Thomas): ADR-011 is built on the block lifecycle
-- (ADR-042). Strict scopes use the existing review; direct scopes activate at
-- once. Both tables go. Dropping them loses nothing: they are empty, and
-- only merge_request_comments referenced merge_requests.
--
-- Not reversible, like every migration here (CHY-113).

DROP TABLE merge_request_comments;
DROP TABLE merge_requests;
