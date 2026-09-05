from tides_pool.datum_prime import cmd_len_allowed


def test_cmd_len_allowed():
    assert cmd_len_allowed(0, 262144)
    assert cmd_len_allowed(1024, 262144)
    assert cmd_len_allowed(262144, 262144)
    assert not cmd_len_allowed(262145, 262144)
    assert not cmd_len_allowed(-1, 262144)
    assert not cmd_len_allowed(0x3FFFFF, 262144)
