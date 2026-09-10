"""DATUM v3 session resume — echo prior configure token so GW keeps share queue.

Gateway (CONVOY datum_protocol.c):
  - Stores 40-byte resume token from configure (prime_id LE + 32 opaque).
  - On reconnect hello: after 0xFE + nk, sends DRS\\x01 + flag + optional token.
  - Resume accepted iff configure echoes the *same* 40-byte token (and prime_id
    matches token[0:8]). Otherwise: \"resume was declined; discarded queued shares\"
    → empty-work blast.

Prime must: parse hello resume request, remember issued tokens, echo on match.
"""
from __future__ import annotations

import struct

import pytest

from tides_pool.datum_prime import (
    DATUM_PRIME_ID,
    DATUM_RESUME_TOKEN_SIZE,
    ResumeTokenBook,
    make_resume_token,
    parse_hello_resume_token,
)


def _hello_tail(*, nk: int = 0x11223344, resume: bytes | None = None) -> bytes:
    """Bytes starting at 0xFE (as in decrypted hello before signature)."""
    buf = bytearray()
    buf.append(0xFE)
    buf.extend(struct.pack("<I", nk))
    buf.extend(b"DRS\x01")
    if resume is None:
        buf.append(0)
    else:
        assert len(resume) == DATUM_RESUME_TOKEN_SIZE
        buf.append(1)
        buf.extend(resume)
    buf.extend(b"\x00" * 8)  # pad
    return bytes(buf)


def test_make_resume_token_embeds_prime_id():
    tok = make_resume_token(DATUM_PRIME_ID)
    assert len(tok) == DATUM_RESUME_TOKEN_SIZE
    assert struct.unpack_from("<Q", tok, 0)[0] == DATUM_PRIME_ID
    tok2 = make_resume_token(DATUM_PRIME_ID, opaque=b"\x11" * 32)
    assert tok2[8:] == b"\x11" * 32


def test_parse_hello_no_drs():
    # legacy: 0xFE + nk only
    msg = bytes([0xFE]) + struct.pack("<I", 1) + b"\x00\x00"
    # fe at 0 relative — embed in fake UA prefix
    prefix = b"x" * 128
    full = prefix + msg
    fe = full.index(0xFE, 128)
    assert parse_hello_resume_token(full, fe) is None


def test_parse_hello_drs_flag_zero():
    prefix = b"x" * 128
    full = prefix + _hello_tail(resume=None)
    fe = full.index(0xFE, 128)
    assert parse_hello_resume_token(full, fe) is None


def test_parse_hello_drs_with_token():
    tok = make_resume_token(DATUM_PRIME_ID, opaque=b"\xab" * 32)
    prefix = b"x" * 128
    full = prefix + _hello_tail(resume=tok)
    fe = full.index(0xFE, 128)
    got = parse_hello_resume_token(full, fe)
    assert got == tok


def test_book_first_issue_not_resumed():
    book = ResumeTokenBook()
    tok, resumed = book.resolve("1.2.3.4", None)
    assert resumed is False
    assert len(tok) == DATUM_RESUME_TOKEN_SIZE
    assert struct.unpack_from("<Q", tok, 0)[0] == DATUM_PRIME_ID


def test_book_echoes_known_token():
    book = ResumeTokenBook()
    tok1, r1 = book.resolve("1.2.3.4", None)
    assert r1 is False
    tok2, r2 = book.resolve("1.2.3.4", tok1)
    assert r2 is True
    assert tok2 == tok1


def test_book_unknown_token_declines_issues_new():
    book = ResumeTokenBook()
    book.resolve("1.2.3.4", None)
    foreign = make_resume_token(DATUM_PRIME_ID, opaque=b"\xee" * 32)
    tok, resumed = book.resolve("1.2.3.4", foreign)
    assert resumed is False
    assert tok != foreign


def test_book_token_works_across_peer_ip():
    """GW may NAT-hop; token identity is the resume key (matches GW check)."""
    book = ResumeTokenBook()
    tok, _ = book.resolve("1.2.3.4", None)
    tok2, resumed = book.resolve("5.6.7.8", tok)
    assert resumed is True
    assert tok2 == tok


def test_book_purge_expired():
    book = ResumeTokenBook(ttl_sec=0.01)
    tok, _ = book.resolve("1.2.3.4", None)
    import time

    time.sleep(0.02)
    tok2, resumed = book.resolve("1.2.3.4", tok)
    assert resumed is False
    assert tok2 != tok


def test_configure_v3_resume_blob_layout():
    """Mirror GW parse: prime_id u64 + 40-byte token (token[0:8]==prime_id)."""
    from tides_pool.datum_prime import build_configure_v3_resume_fields

    tok = make_resume_token(DATUM_PRIME_ID, opaque=b"\x42" * 32)
    prime_id_bytes, token_bytes = build_configure_v3_resume_fields(tok)
    assert len(prime_id_bytes) == 8
    assert len(token_bytes) == 40
    assert struct.unpack_from("<Q", prime_id_bytes, 0)[0] == DATUM_PRIME_ID
    assert token_bytes == tok
    assert struct.unpack_from("<Q", token_bytes, 0)[0] == DATUM_PRIME_ID
