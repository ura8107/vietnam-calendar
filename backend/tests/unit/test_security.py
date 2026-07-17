from vietnam_calendar.security import hash_password, is_valid_argon2id_hash, token_hash, verify_password


def test_argon2id_password_round_trip():
    encoded = hash_password("correct horse battery staple")
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "correct horse battery staple")
    assert not verify_password(encoded, "wrong password")
    assert is_valid_argon2id_hash(encoded)
    assert not is_valid_argon2id_hash("not-a-hash")


def test_tokens_are_one_way_digests():
    assert token_hash("secret") == "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b"
