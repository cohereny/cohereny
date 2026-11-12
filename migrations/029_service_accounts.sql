-- 029_service_accounts.sql — technical accounts are marked and do not count as people (CHY-163)
--
-- Four eyes on a block that lets data leave the installation (ADR-045) needs
-- two answers this table could not give: is the approver a person, and how
-- many people may approve at all? A one-person installation may approve its
-- own data-flow block; a second person ends that. Counting roles alone got
-- both wrong. On 05.11.2026 the test stage had exactly one actor with the
-- right to approve, `cohereny-ci` — the CI account would have been the
-- "single operator", and in every installation with such an account a real
-- single operator could never approve their own rule except by signing in as
-- the CI account.
--
-- The flag is a projection of the Authentik group `chy-service-accounts`,
-- refreshed on every request like the role (kernel/identity.py). FALSE by
-- default: an account nobody marked is a person, which is the side on which
-- four eyes is enforced rather than waived.

ALTER TABLE actors
    ADD COLUMN IF NOT EXISTS is_service_account BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN actors.is_service_account IS
    'A technical account (CI, automation), from the Authentik group '
    'chy-service-accounts (CHY-163). Never counted as a person who may '
    'approve, and never allowed to approve a block that lets data leave the '
    'installation (ADR-045).';
