from app.prompts.hasher import compute_hash, normalize_content


def test_hash_is_deterministic():
    content = "Tu es un évaluateur.\nNote la réponse entre 0 et 1."
    assert compute_hash(content) == compute_hash(content)


def test_hash_is_sha256_hex_length():
    assert len(compute_hash("anything")) == 64


def test_crlf_and_lf_are_equivalent():
    a = "ligne 1\nligne 2\nligne 3"
    b = "ligne 1\r\nligne 2\r\nligne 3"
    assert compute_hash(a) == compute_hash(b)


def test_trailing_whitespace_is_ignored():
    a = "Tu es un évaluateur."
    b = "Tu es un évaluateur.   "
    c = "Tu es un évaluateur.\t\t"
    assert compute_hash(a) == compute_hash(b) == compute_hash(c)


def test_per_line_trailing_whitespace_is_ignored():
    a = "ligne 1\nligne 2"
    b = "ligne 1   \nligne 2\t"
    assert compute_hash(a) == compute_hash(b)


def test_leading_and_trailing_newlines_are_ignored():
    a = "Tu es un évaluateur."
    b = "\n\nTu es un évaluateur.\n\n"
    assert compute_hash(a) == compute_hash(b)


def test_unicode_nfc_normalization():
    # 'é' as a precomposed character vs decomposed (e + combining acute).
    composed = "Évaluateur"
    decomposed = "Évaluateur"
    assert composed != decomposed
    assert compute_hash(composed) == compute_hash(decomposed)


def test_real_content_change_produces_different_hash():
    a = "Note entre 0 et 1."
    b = "Note entre 1 et 5."
    assert compute_hash(a) != compute_hash(b)


def test_single_character_change_produces_different_hash():
    a = "Tu es un évaluateur strict."
    b = "Tu es un évaluateur strictt."
    assert compute_hash(a) != compute_hash(b)


def test_normalize_content_strips_and_lf_only():
    out = normalize_content("\r\n  hello world  \r\n\r\n")
    assert out == "hello world"
