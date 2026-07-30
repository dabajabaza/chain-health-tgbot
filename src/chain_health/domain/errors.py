class DomainError(Exception):
    """Expected, user-facing failure. Safe to show a friendly message for."""


class NotFoundError(DomainError):
    """A requested entity does not exist, or does not belong to the caller.

    Deliberately does not distinguish "missing" from "someone else's" — a
    stale button and an ownership probe must be indistinguishable to the
    caller, by design (see docs/ARCHITECTURE.md D15).
    """

    def __init__(self, entity: str, entity_id: object) -> None:
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id


class InvalidOperationError(DomainError):
    """The request is well-formed but not allowed in the current state."""


class StaleMessageError(DomainError):
    """The message behind an inline button can no longer be edited."""


class NoActiveChainError(DomainError):
    """The user has no current group, or that group has no active chain."""
