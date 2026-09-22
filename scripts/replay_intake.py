#!/usr/bin/env python3
"""Put submissions that could not be stored back into website_intake.

When the database is unreachable, the website logs each submission in full
(event intake_unstored) and mails it as [NOT SAVED] with a REPLAY-JSON line.
This reads either and inserts the rows as website_app, keeping each original
id, so running it twice, or on a submission that did get stored, is harmless:
that row is reported as "already stored".

It accepts, one source per file (or "-" for stdin):
  * a saved [NOT SAVED] mail: the raw source (.eml, quoted-printable is fine)
    or its text. Only the LAST line that starts with REPLAY-JSON: counts: the
    website writes it after every word the visitor typed, and indents any line
    of visitor text that starts with the marker.
  * a Cloud Logging export of intake_unstored entries: a JSON array
    (gcloud logging read ... --format=json) or one JSON object per line.
  * bare row objects, one per line.

Needs WEBSITE_DB_URL (the website_app login) in the environment. A replayed
row's received_at is the replay time: website_app cannot backdate a row. The
original time is in the mail and in the log entry.

  python scripts/replay_intake.py --check          # prove the login works, change nothing
  python scripts/replay_intake.py notsaved-mail.eml
  gcloud logging read 'jsonPayload.event="intake_unstored"' --freshness=30d --format=json \\
      --project gen-lang-client-0024674990 | python scripts/replay_intake.py -

Exit status: 0 when every row found was stored or already there, 1 when any
failed or nothing replayable was found, 2 for usage errors.
"""
import email
import json
import os
import re
import sys
import uuid
from email import policy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import intake  # noqa: E402

MARKER = intake.REPLAY_MARKER
_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]*:[ \t]")


def _mail_body(text):
    """The decoded plain-text body when `text` is a raw mail; else None."""
    normalised = text.replace("\r\n", "\n").lstrip("\ufeff").lstrip()
    if not _HEADER.match(normalised) or "\n\n" not in normalised:
        return None
    msg = email.message_from_string(normalised, policy=policy.default)
    if msg.get("Subject") is None and msg.get("From") is None:
        return None                     # "Name: ..." text, not mail headers
    part = msg.get_body(preferencelist=("plain",))
    return part.get_content() if part is not None else None


def candidates(text):
    """(objects found, lines that could not be read) for one source."""
    text = text.lstrip("\ufeff")
    stripped = text.strip()
    if not stripped:
        return [], 0
    # 1. a whole JSON document: a gcloud export, or one row or entry
    try:
        whole = json.loads(stripped)
        return (whole if isinstance(whole, list) else [whole]), 0
    except json.JSONDecodeError:
        pass
    # 2. a mail: the server's own line is the last one that starts with the marker
    body = _mail_body(text)
    lines = (body if body is not None else text).replace("\r\n", "\n").split("\n")
    marked = [line for line in lines if line.startswith(MARKER)]
    if marked:
        try:
            return [json.loads(marked[-1][len(MARKER):].strip())], 0
        except json.JSONDecodeError:
            return [], 1
    if body is not None:
        # a mail without the server's line: every other line in it is visitor text
        return [], 1
    # 3. JSON lines, all or nothing: a real export has no other text, while a
    # mail's text (visitor lines included) always does
    found, unreadable = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            unreadable += 1
    return ([], unreadable) if unreadable else (found, 0)


def row_of(obj):
    """The submission row inside a mail line, a log entry or a bare object, or None."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("jsonPayload"), dict):
        obj = obj["jsonPayload"]
    if isinstance(obj.get("row"), dict):
        obj = obj["row"]
    elif obj.get("row_omitted") or "event" in obj:
        return None                     # a log entry that carries no row
    if "id" not in obj or "form" not in obj:
        return None
    return obj


def validate(row):
    unknown = set(row) - set(intake.COLUMNS)
    if unknown:
        raise ValueError("unexpected fields: " + ", ".join(sorted(unknown)))
    uuid.UUID(str(row["id"]))
    if row["form"] not in intake.FORMS:
        raise ValueError("unknown form: " + str(row["form"]))
    return {c: row.get(c) for c in intake.COLUMNS}


def _decode(data):
    # Windows PowerShell 5.1 writes UTF-16 with a BOM for `>` and Out-File;
    # Notepad and `Out-File -Encoding utf8` write UTF-8 with a BOM. Anything
    # else (Set-Content's ANSI, say) is refused: guessing would store names
    # with replacement characters, and a stored row cannot be corrected.
    try:
        if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("not UTF-8 or UTF-16; re-save it as UTF-8") from None


def _read(path):
    if path == "-":
        return _decode(sys.stdin.buffer.read())
    with open(path, "rb") as f:
        return _decode(f.read())


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    if not intake.configured():
        print("WEBSITE_DB_URL is not set: nothing to replay into.", file=sys.stderr)
        return 2
    if argv[0] == "--check":
        try:
            print("connected as " + intake.connection_check())
            return 0
        except intake.IntakeError as exc:
            print("could not connect: " + str(exc), file=sys.stderr)
            return 1
    stored = already = failed = found = 0
    for path in argv:
        try:
            text = _read(path)
        except (OSError, ValueError) as exc:
            failed += 1
            print(f"{path}: FAILED ({exc})")
            continue
        objects, unreadable = candidates(text)
        if unreadable:
            failed += unreadable
            print(f"{path}: nothing replayed, {unreadable} line(s) could not be read as JSON. "
                  "A saved mail must still contain its REPLAY-JSON line; a JSON-lines "
                  "file must hold nothing else")
        for obj in objects:
            raw = row_of(obj)
            if raw is None:
                payload = obj.get("jsonPayload", obj) if isinstance(obj, dict) else {}
                if isinstance(payload, dict) and payload.get("row_omitted"):
                    failed += 1
                    print(f"{payload.get('id')}: row not in this log entry (hourly cap); "
                          "replay it from its [NOT SAVED] mail instead")
                continue
            found += 1
            try:
                row = validate(raw)
                if intake.insert_submission(row) == "stored":
                    stored += 1
                    print(f"{row['id']}: stored")
                else:
                    already += 1
                    print(f"{row['id']}: already stored")
            except (ValueError, intake.IntakeError) as exc:
                failed += 1
                print(f"{raw.get('id')}: FAILED ({exc})")
    print(f"{found} found, {stored} stored, {already} already stored, {failed} failed")
    return 1 if failed or not found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
