"""Thin IMAP wrapper: deterministic new-message discovery by UID cursor.

The mailbox is opened read-only — the app never marks messages seen, moves, or
deletes anything. Its only state is the (UIDVALIDITY, last UID) cursor stored
in the Setting table, which makes the 5-minute poll fully deterministic: a
message is "new" iff its UID is above the cursor.
"""

from __future__ import annotations

import imaplib
import logging
import re

logger = logging.getLogger(__name__)

_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)")


class ImapError(RuntimeError):
    """An IMAP conversation failed (connect, select, search, or fetch)."""


class ImapInbox:
    """One SSL IMAP connection to the intake mailbox (context manager)."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        timeout: float = 60.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.timeout = timeout
        self._conn: imaplib.IMAP4_SSL | None = None

    def __enter__(self) -> "ImapInbox":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def connect(self) -> None:
        self._conn = imaplib.IMAP4_SSL(self.host, self.port, timeout=self.timeout)
        self._conn.login(self.username, self.password)
        status, _ = self._conn.select(self.folder, readonly=True)
        if status != "OK":
            raise ImapError(f"could not select folder {self.folder!r}")

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.close()
            self._conn.logout()
        except Exception:
            logger.debug("IMAP close failed", exc_info=True)
        self._conn = None

    def uidvalidity(self) -> int:
        """The folder's UIDVALIDITY — when it changes, UIDs were reassigned."""
        status, data = self._conn.status(self.folder, "(UIDVALIDITY)")
        if status != "OK" or not data:
            raise ImapError("UIDVALIDITY status failed")
        match = _UIDVALIDITY_RE.search(data[0] or b"")
        if not match:
            raise ImapError(f"unparseable STATUS response: {data[0]!r}")
        return int(match.group(1))

    def uids_above(self, last_uid: int) -> list[int]:
        """UIDs of messages newer than the cursor, ascending."""
        status, data = self._conn.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        if status != "OK":
            raise ImapError("UID SEARCH failed")
        raw = data[0].split() if data and data[0] else []
        # IMAP quirk: "N:*" always matches the highest-UID message, even when
        # its UID <= N, so filter explicitly.
        return sorted(int(u) for u in raw if int(u) > last_uid)

    def fetch(self, uid: int) -> bytes:
        """The full raw message. PEEK so the read leaves no flags behind."""
        status, data = self._conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
        if status != "OK":
            raise ImapError(f"UID FETCH {uid} failed")
        for item in data or []:
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
                return item[1]
        raise ImapError(f"no body returned for UID {uid}")
