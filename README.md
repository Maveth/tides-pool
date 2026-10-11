# RIPTIDE

This repository is retired. The pool server is [Maveth/riptide-pool](https://github.com/Maveth/riptide-pool).

Site: https://riptide.maveth.ca
Connect: https://riptide.maveth.ca/connect

## DATUM

Preferred door is 0% fee. Point a gateway at the pool. Miners point at that gateway.

| Host:port | Min diff | Fee | ABW |
| --- | ---: | --- | --- |
| `riptide.maveth.ca:29120` | 2048 | 0% soft main | off |
| `riptide.maveth.ca:29121` | 32768 | 0% | off |
| `riptide.maveth.ca:29122` | 4096 | optional fee | off |
| `riptide.maveth.ca:29123` | 32768 | optional fee | off |
| `riptide.maveth.ca:29130` | 4096 | 0% | on |
| `riptide.maveth.ca:29131` | 32768 | 0% high diff | on |

```json
{
  "datum": {
    "pool_host": "riptide.maveth.ca",
    "pool_port": 29120,
    "pool_pubkey": "b95abf4a11050c5164d09e9d3c4a18a4df735c5d670e3e974fc146794efa4ed9fbc78e7cd4b6abcd3fcc0e0619543d60004fdb49e9d2974582ae11988a03d743",
    "pooled_mining_only": false,
    "pool_pass_full_users": false
  }
}
```

## SV1

Username is the payout address, or `address.worker`. Password is `x`.

| Host:port | Min diff | Fee | ABW |
| --- | ---: | --- | --- |
| `sv1.riptide.maveth.ca:29128` | 4096 | variable skim | on |
| `sv1.riptide.maveth.ca:29129` | 32768 | variable skim | on |

Older doors still open the current ones: `28916` opens `29120`, and `23337` and `23336` open `29128`.
