"""Payment state machine.

Lifecycle: CREATED → AUTHORIZED → CAPTURED → SETTLED (plus FAILED).
Legacy processor statuses (pending → succeeded | failed) are still accepted
so in-flight charges from older processors remain processable.

Terminal states are final.
"""

from payments.models import Payment


class InvalidTransitionError(Exception):
    pass


# Allowed forward transitions. Terminal states have no outgoing edges.
_TRANSITIONS = {
    Payment.Status.CREATED: {
        Payment.Status.AUTHORIZED,
        Payment.Status.FAILED,
        Payment.Status.PENDING,  # legacy alias path
    },
    "pending": {Payment.Status.PENDING, Payment.Status.SUCCEEDED, Payment.Status.FAILED, Payment.Status.AUTHORIZED},
    Payment.Status.AUTHORIZED: {Payment.Status.CAPTURED, Payment.Status.FAILED},
    Payment.Status.CAPTURED: {Payment.Status.SETTLED, Payment.Status.FAILED},
    Payment.Status.SUCCEEDED: set(),
    Payment.Status.FAILED: set(),
    Payment.Status.SETTLED: set(),
}

_TERMINAL = {Payment.Status.SUCCEEDED, Payment.Status.FAILED, Payment.Status.SETTLED}


def can_transition(current_status: str, new_status: str) -> bool:
    """Check if a state transition is valid."""
    allowed = _TRANSITIONS.get(current_status, set())
    return new_status in allowed


def is_terminal(status: str) -> bool:
    """Check if a status is terminal."""
    return status in _TERMINAL
