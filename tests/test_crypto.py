import pytest

from polarsen.db.crypto import (
    decrypt_api_keys,
    decrypt_secret,
    encrypt_api_keys,
    encrypt_secret,
)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("sk-test-1234", id="openai-style"),
        pytest.param("AIzaSyA-fakegeminikey-0123456789", id="gemini-style"),
        pytest.param("", id="empty"),
        pytest.param("a" * 4096, id="long"),
        pytest.param("héllo-wörld 🔐", id="non-ascii"),
    ],
)
def test_round_trip(value):
    ciphertext = encrypt_secret(value)
    assert ciphertext != value
    assert decrypt_secret(ciphertext) == value


def test_ciphertext_changes_each_call():
    # Fernet embeds a random IV/nonce, so the same input must produce different ciphertexts.
    a = encrypt_secret("sk-same")
    b = encrypt_secret("sk-same")
    assert a != b
    assert decrypt_secret(a) == decrypt_secret(b) == "sk-same"


def test_decrypt_legacy_plaintext_fallback():
    # Existing rows written before encryption was enabled must still be readable.
    legacy = "sk-legacy-plaintext"
    assert decrypt_secret(legacy) == legacy


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(None, id="none"),
        pytest.param({}, id="empty-dict"),
    ],
)
def test_dict_passthrough_for_empty(value):
    assert encrypt_api_keys(value) == value
    assert decrypt_api_keys(value) == value


def test_dict_round_trip():
    keys = {"openai": "sk-openai", "mistral": "sk-mistral", "gemini": "AIza-gemini"}
    encrypted = encrypt_api_keys(keys)
    assert encrypted is not None
    assert set(encrypted.keys()) == set(keys.keys())
    for source, value in keys.items():
        assert encrypted[source] != value
    assert decrypt_api_keys(encrypted) == keys


def test_missing_kms_key_raises(monkeypatch):
    # Reset the cached Fernet so the env-var check runs again.
    import polarsen.db.crypto as crypto_module
    from polarsen import env

    monkeypatch.setattr(env, "POLARSEN_KMS_KEY", None)
    monkeypatch.setattr(crypto_module, "_FERNET", None)

    with pytest.raises(RuntimeError, match="POLARSEN_KMS_KEY"):
        encrypt_secret("anything")
