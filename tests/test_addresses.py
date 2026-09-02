from tides_pool.addresses import address_to_script, is_valid_payout_address


def test_mfh5a_ops_script():
    # Known from bitcoin-cli getaddressinfo for mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3
    script = address_to_script("mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3")
    assert script.hex() == "76a91401ea3d1be1ce4d6c25a1fb506e7aafc95d3ec7e688ac"


def test_is_valid_payout_address():
    assert is_valid_payout_address("mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3")
    assert is_valid_payout_address("n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5")
    assert is_valid_payout_address(
        "tb1pr58xqc6j2dx83n3qhmggy92gel6de8ez7gs9uw584x598p9qrkrsvnl64j"
    )
    assert not is_valid_payout_address("box2")
    assert not is_valid_payout_address("")
    assert not is_valid_payout_address("not-an-address")
    assert not is_valid_payout_address("1Bogus")
