"""Payment state machine.

Lifecycle: CREATED → AUTHORIZED → CAPTURED → SETTLED (plus FAILED).
Legacy processor statuses (pending → succeeded | failed) are still accepted
so in-flight charges from older processors remain processable.

Terminal states are final.
"""

import logging

from payments.models import Payment

logger = logging.getLogger(__name__)


class InvalidTransitionError(Exception):
    pass


# Allowed forward transitions. Terminal states have no outgoing edges.
# ISSUE 4 FIX: Removed Payment.Status.PENDING from its own allowed transitions
# to prevent ledger pollution from duplicate pending webhooks with different
# event_ids. PENDING now only transitions forward to AUTHORIZED, SUCCEEDED,
# or FAILED.
_TRANSITIONS = {
    Payment.Status.CREATED: {
        Payment.Status.AUTHORIZED,
        Payment.Status.FAILED,
        Payment.Status.PENDING,  # legacy alias path
    },
    Payment.Status.PENDING: {
        Payment.Status.AUTHORIZED,
        Payment.Status.SUCCEEDED,
        Payment.Status.FAILED,
    },
    Payment.Status.AUTHORIZED: {Payment.Status.CAPTURED, Payment.Status.FAILED},
    Payment.Status.CAPTURED: {Payment.Status.SETTLED, Payment.Status.FAILED},
    Payment.Status.SUCCEEDED: set(),
    Payment.Status.FAILED: set(),
    Payment.Status.SETTLED: set(),
}

_TERMINAL = {Payment.Status.SUCCEEDED, Payment.Status.FAILED, Payment.Status.SETTLED}


def can_transition(current_status: str, new_status: str) -> bool:
    """Check if a state transition is valid."""
    # ISSUE 4 FIX: Log a warning when current_status is unknown (e.g., corrupted
    # data) instead of silently returning False, which makes debugging difficult.
    allowed = _TRANSITIONS.get(current_status)
    if allowed is None:
        logger.warning(
            "Unknown payment status '%s' encountered in can_transition(); "
            "rejecting transition to '%s'.",
            current_status,
            new_status,
        )
        return False
    return new_status in allowed


def is_terminal(status: str) -> bool:
    """Check if a status is terminal."""
    return status in _TERMINAL
