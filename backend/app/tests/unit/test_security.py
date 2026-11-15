from app.services.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    password_needs_rehash,
    verify_password,
    verify_session_token,
)


def test_passwords_are_salted_argon2id_hashes():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first.startswith("$argon2id$")
    assert second.startswith("$argon2id$")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert not verify_password("wrong password", first)
    assert not password_needs_rehash(first)


def test_invalid_password_hash_is_rejected_without_leaking_an_exception():
    assert not verify_password("password", "not-an-argon2-hash")
    assert password_needs_rehash("not-an-argon2-hash")


def test_session_token_is_random_and_verified_only_through_its_hash():
    first = generate_session_token()
    second = generate_session_token()

    assert first.value != second.value
    assert first.token_hash == hash_session_token(first.value)
    assert len(first.token_hash) == 64
    assert first.value not in first.token_hash
    assert verify_session_token(first.value, first.token_hash)
    assert not verify_session_token(second.value, first.token_hash)
