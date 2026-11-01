"""
api/surfaces.py

The per-router surface gate (ADR-031, CHY-109).

The vocabulary — which surfaces exist, what their audiences are called — lives
in kernel/surfaces.py. This module holds only the part that needs HTTP: turning
a mismatch between the token's audience and the router's surface into a 403.

Re-exports Surface and AUDIENCES so a caller in the transport layer has one
import rather than two.

Called by:
  - api.v1.router (dependencies=[Depends(require_surface(...))] per router)
"""
from fastapi import Depends, HTTPException, status

from api.deps import CurrentUser, require_auth
from kernel.surfaces import AUDIENCE_PREFIX, AUDIENCES, Surface, accepted_audiences

__all__ = ["AUDIENCES", "AUDIENCE_PREFIX", "Surface", "require_surface"]


def require_surface(surface: Surface):
    """Router-level dependency: the token must have been issued for this surface.

    Attached in api/v1/router.py rather than to each endpoint. A gate that has
    to be repeated on every route is a gate that will be missing from one of
    them, and the missing one will be the interesting one.

    Runs *after* require_auth and *before* any role check, which is the point:
    a token for the wrong surface never reaches the question of what its holder
    may do.
    """
    expected = AUDIENCES[surface.value]
    # The UI's client and the machine client of this surface (INFRA-122).
    # Two audiences, one surface: a machine token opens exactly the surface its
    # UI counterpart opens, and no other.
    accepted = accepted_audiences(surface.value)

    async def _check(actor: CurrentUser = Depends(require_auth)) -> CurrentUser:
        # Dev mode has no token at all (api/deps.py returns a static admin), so
        # there is no audience to check. Guarded against production by CHY-91.
        if actor.audience is None:
            return actor
        if actor.audience not in accepted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This token was issued for '{actor.audience}' and this is "
                    f"the '{expected}' surface."
                ),
            )
        return actor

    return _check
