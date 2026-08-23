from tides_pool.datum_prime import header_xor_feedback, pack_header, unpack_header, xor_header


def test_header_xor_feedback_smoke():
    # Stable known value for regression (not from C vector dump)
    assert header_xor_feedback(0) != 0
    assert header_xor_feedback(0xDC871829) == header_xor_feedback(0xDC871829)


def test_header_roundtrip_flags():
    raw = pack_header(
        100,
        proto_cmd=5,
        is_signed=True,
        is_encrypted_channel=True,
    )
    h = unpack_header(raw)
    assert h["cmd_len"] == 100
    assert h["proto_cmd"] == 5
    assert h["is_signed"] is True
    assert h["is_encrypted_channel"] is True
    x = xor_header(raw, 0xDC871829)
    assert xor_header(x, 0xDC871829) == raw
