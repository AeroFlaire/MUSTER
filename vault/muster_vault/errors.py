"""Errors the Vault raises. The Gatekeeper maps these to HTTP responses."""


class VaultError(Exception):
    """Base class for every Vault error."""


class NotFound(VaultError):
    """The record asked for does not exist."""


class PermissionDenied(VaultError):
    """The actor exists but does not have the role for this action."""


class InvalidTransition(VaultError):
    """A score was asked to move to a status it can't reach from where it is."""


class SignatureInvalid(VaultError):
    """A signature did not verify against the signer's enrolled key."""


class ChallengeError(VaultError):
    """A signing challenge is unknown, expired, already used, or for something else."""


class TimingError(VaultError):
    """IOCT gate reads can't produce a valid run time."""
