from tides_pool.addresses import address_to_script


def test_mfh5a_ops_script():
    # Known from bitcoin-cli getaddressinfo for mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3
    script = address_to_script("mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3")
    assert script.hex() == "76a91401ea3d1be1ce4d6c25a1fb506e7aafc95d3ec7e688ac"
