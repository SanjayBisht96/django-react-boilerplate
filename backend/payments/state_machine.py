"""Payment state machine: pending → succeeded | failed. Terminal states are final."""

from payments.models import Payment


class InvalidTransitionError(Exception):
    pass


def can_transition(current_status: str, new_status: str) -> bool:
    """Check if a state transition is valid."""
    if current_status == Payment.Status.PENDING:
        return new_status in (
            Payment.Status.PENDING,
            Payment.Status.SUCCEEDED,
            Payment.Status.FAILED,
        )
    # Terminal states: no further transitions allowed
    return False


def is_terminal(status: str) -> bool:
    """Check if a status is terminal."""
    return status in (Payment.Status.SUCCEEDED, Payment.Status.FAILED)
