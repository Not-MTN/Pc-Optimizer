"""One exception for "that object does not exist".

Both a missing JSON key and a failed lookup used to raise KeyError, so the
server could not tell them apart and reported every one as "missing field" —
including `missing field 'unknown workspace nope'`, which tells the user to add
a field whose name is a sentence. KeyError is a legitimate thing for a caller to
raise for a missing argument, so this is a separate class: anything raised as
Absent is definitely a lookup that found nothing, and maps to 404.
"""

from __future__ import annotations


class Absent(KeyError):
    """The named object does not exist. Not a malformed request — a 404.

    Subclasses KeyError so existing `except KeyError` callers keep working.
    """
