import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher, profiles
from argon2.exceptions import InvalidHashError, VerificationError


_password_hasher = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)


def hash_password(password: str) -> str:
    """Create an Argon2id password hash with the library's RFC 9106 profile."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _password_hasher.check_needs_rehash(password_hash)
    except (InvalidHashError, VerificationError):
        return True


def hash_session_token(token: str) -> str:
    """Digest a high-entropy token before it crosses the persistence boundary."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_session_token(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_session_token(token), token_hash)


@dataclass(frozen=True)
class NewSessionToken:
    value: str
    token_hash: str


def generate_session_token() -> NewSessionToken:
    value = secrets.token_urlsafe(32)
    return NewSessionToken(value=value, token_hash=hash_session_token(value))
