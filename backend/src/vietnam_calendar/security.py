import hashlib
import secrets

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerifyMismatchError

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def is_valid_argon2id_hash(encoded: str) -> bool:
    try:
        return encoded.startswith("$argon2id$") and extract_parameters(encoded).type.name == "ID"
    except (InvalidHashError, ValueError):
        return False


def random_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
