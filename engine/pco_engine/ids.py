"""ULID generation — sortable, collision-free, no dependencies.

Rust port: `ulid` crate. Same encoding, same properties.
"""
from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_CROCKFORD[value & 31])
        value >>= 5
    return "".join(reversed(chars))


class Ulid:
    """Monotonic ULID factory (same-millisecond IDs still sort in creation order)."""

    def __init__(self) -> None:
        self._last_ms = 0
        self._last_rand = 0

    def new(self) -> str:
        ms = int(time.time() * 1000)
        if ms > self._last_ms:
            self._last_ms = ms
            self._last_rand = int.from_bytes(os.urandom(10), "big")
        else:
            # monotonic increment within the same millisecond
            self._last_rand += 1
            if self._last_rand >= (1 << 80):
                self._last_ms += 1
                self._last_rand = 0
        ms = self._last_ms
        time_part = _encode(ms, 10)
        rand_part = _encode(self._last_rand, 16)
        return time_part + rand_part


_DEFAULT = Ulid()


def new_id() -> str:
    return _DEFAULT.new()
