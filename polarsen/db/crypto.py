from cryptography.fernet import Fernet, InvalidToken

from polarsen import env

__all__ = (
    "decrypt_api_keys",
    "decrypt_secret",
    "encrypt_api_keys",
    "encrypt_secret",
)


_FERNET: Fernet | None = None


def _get_fernet() -> Fernet:
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    key = env.POLARSEN_KMS_KEY
    if not key:
        raise RuntimeError(
            "POLARSEN_KMS_KEY is not set. Generate one with "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
    return _FERNET


def encrypt_secret(value: str) -> str:
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    # Fall back to plaintext for legacy rows written before encryption was enabled.
    # Why: lets us deploy without a forced migration; a backfill job can re-encrypt at leisure.
    try:
        return _get_fernet().decrypt(value.encode()).decode()
    except InvalidToken:
        return value


def encrypt_api_keys[K: str](api_keys: dict[K, str] | None) -> dict[K, str] | None:
    if not api_keys:
        return api_keys
    return {k: encrypt_secret(v) for k, v in api_keys.items()}


def decrypt_api_keys[K: str](api_keys: dict[K, str] | None) -> dict[K, str] | None:
    if not api_keys:
        return api_keys
    return {k: decrypt_secret(v) for k, v in api_keys.items()}
