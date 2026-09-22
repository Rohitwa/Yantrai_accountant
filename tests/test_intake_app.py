"""The form handlers with the database (and, mostly, the mailbox) faked: every
branch of store-then-mail, fallback, limits and switches, without a network.
The few tests that use the real mail or database code point it at a closed
local port, so they fail fast and never leave the machine."""
import hashlib
import io
import json
import os
import re
import ssl
import sys
import threading
import time
from email.message import EmailMessage

import pytest

import intake
import main

PASSWORD = "s3cret-pw"
DSN = f"postgresql://website_app.proj:{PASSWORD}@127.0.0.1:1/postgres?sslmode=disable"
GOOD = {"name": "Asha Rao", "email": "asha@example.com", "company": "Acme Pvt Ltd",
        "role": "CFO", "erp": "SAP", "outflow": "$50M+", "note": "hello",
        "page": "https://yantrailabs.com/"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(env, fresh_intake):
    main.app.config["TESTING"] = True
    return main.app.test_client()


@pytest.fixture
def mail(monkeypatch):
    state = {"sent": [], "ok": True}

    def fake_send(subject, body, reply_to="", attachment=None):
        state["sent"].append({"subject": subject, "body": body, "reply_to": reply_to,
                              "attachment": attachment})
        return (True, "", 200) if state["ok"] else (False, "Failed to send email", 502)

    monkeypatch.setattr(main, "_send_mail", fake_send)
    return state


@pytest.fixture
def db(monkeypatch):
    state = {"rows": [], "events": [], "outcome": "stored", "error": None}

    def fake_insert(row):
        if state["error"]:
            raise state["error"]
        state["rows"].append(dict(row))
        return state["outcome"]

    monkeypatch.setattr(intake, "insert_submission", fake_insert)
    monkeypatch.setattr(intake, "record_event", lambda sid, ev: state["events"].append((sid, ev)))
    return state


@pytest.fixture
def closed_smtp(env):
    """Real _send_mail, pointed at a port nothing listens on."""
    for key, value in (("SMTP_HOST", "127.0.0.1"), ("SMTP_PORT", "1"), ("SMTP_USER", "u@x.co"),
                       ("SMTP_PASS", "p"), ("DEMO_TO_EMAIL", "to@x.co")):
        env.setenv(key, value)


def log_lines(capsys):
    return [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]


def post(client, data=None, **headers):
    return client.post("/api/savings-check", json=GOOD if data is None else data, headers=headers)


# --- switched off: exactly as before ----------------------------------------------------
def test_without_database_the_form_only_mails_as_before(client, mail, db):
    r = post(client)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert db["rows"] == []
    assert mail["sent"][0]["subject"] == "AiFA savings check: Acme Pvt Ltd (Asha Rao)"
    assert "Lead id" not in mail["sent"][0]["body"]


def test_without_database_a_mail_failure_is_still_a_502(client, mail, db):
    mail["ok"] = False
    assert post(client).status_code == 502


def test_listing_no_forms_switches_intake_off(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "")
    assert post(client).status_code == 200
    assert db["rows"] == []


# --- stored first, then mailed ------------------------------------------------------------
def test_stored_then_mailed_with_the_lead_id(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    r = post(client)
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    row = db["rows"][0]
    assert row["form"] == "savings_check" and row["company"] == "Acme Pvt Ltd"
    assert row["locale"] == "en" and row["email"] == "asha@example.com"
    msg = mail["sent"][0]
    assert msg["subject"] == f"AiFA savings check: Acme Pvt Ltd (Asha Rao) [#{row['id'][:8]}]"
    assert f"Lead id: {row['id']}" in msg["body"]
    assert db["events"] == [(row["id"], "mail_sent")]


def test_a_stored_lead_is_ok_even_when_mail_fails(client, mail, db, env, capsys):
    """The failure that lost weeks of leads: SMTP down must no longer mean a 502."""
    env.setenv("WEBSITE_DB_URL", DSN)
    mail["ok"] = False
    assert post(client).status_code == 200
    assert db["events"] == [(db["rows"][0]["id"], "mail_failed")]
    assert "intake_mail_failed" in [e["event"] for e in log_lines(capsys)]


def test_notify_email_off_stores_silently(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("NOTIFY_EMAIL", "0")
    assert post(client).status_code == 200
    assert mail["sent"] == []
    assert db["events"] == [(db["rows"][0]["id"], "mail_disabled")]


def test_a_duplicate_counts_as_stored(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    db["outcome"] = "duplicate"
    assert post(client).status_code == 200
    assert len(mail["sent"]) == 1


# --- the real mail code, which must never raise --------------------------------------------
@pytest.mark.parametrize("company", ["Acme\x0bBcc: evil@x.co", "Acme Bcc: evil@x.co",
                                     "Acme\r\nBcc: evil@x.co"])
def test_line_breaks_in_fields_cannot_crash_the_real_mailer(client, db, env, closed_smtp, capsys, company):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, company=company)).status_code == 200
    assert db["events"] == [(db["rows"][0]["id"], "mail_failed")]
    assert "intake_mail_failed" in [e["event"] for e in log_lines(capsys)]


def test_real_mailer_normalises_the_subject(monkeypatch, closed_smtp):
    seen = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): seen["subject"] = msg["Subject"]

    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    ok, _, _ = main._send_mail("Lead: Acme\x0b Bcc: x@y.co", "body", reply_to="a@b.co")
    assert ok and seen["subject"] == "Lead: Acme Bcc: x@y.co"


@pytest.mark.parametrize("reply_to, expected", [
    ("ravi@[example.com", "u@x.co"), ("a:b@c.co <x", "u@x.co"), ("Bcc: <x@y.co>, a@b", "u@x.co"),
    ('a"b@c.co', "u@x.co"), ("\u00e9lodie@exemple.fr", "u@x.co"), ("a@b.co\u200b", "a@b.co"),
    ("user@b\u00fccher.de", "user@xn--bcher-kva.de"), ("asha@example.com", "asha@example.com"),
    ("asha@acme.com\uff0cthird.party\uff20victim.example", "u@x.co"),     # full-width , and @
    ("asha@x\uff1ay.com", "u@x.co"), ("asha@stra\u00dfe.de", "u@x.co"),     # IDNA 2003 would say strasse.de
    ("firstname.middlename.lastname@finance-department.subsidiary-company-name.example.co.in.", "u@x.co"),
    ("a\x01b@example.com", "u@x.co"), ("a\x7fb@example.com", "u@x.co"),
    ("=?utf-8?q?asha=40evil.example=2C?=@example.com", "u@x.co"),   # an encoded word, decoded on output
    ("asha@STRA\u1e9eE.DE", "u@x.co"),                                # capital sharp s: newer than IDNA 2003
    ("a@ex\u2090mple.com", "u@x.co"),                                  # subscript a (Unicode 4.1): IDNA 2008 says 'a'
    ("a@\u2c00\u2c01.com", "u@x.co"),              # a newer capital letter: IDNA 2003 would not lower-case it
    ("o'brien@example.ie", "o'brien@example.ie"), ("a@m\u00fcnchen.de", "a@xn--mnchen-3ya.de"),
    ("a@\u043f\u0440\u0438\u043c\u0435\u0440.\u0440\u0444", "a@xn--e1afmkfd.xn--p1ai"),
    # newer than Unicode 3.2 but encoded the same by IDNA 2003 and 2008: kept
    ("a@\u09ac\u09bf\u09a6\u09cd\u09af\u09c1\u09ce.\u09ac\u09be\u0982\u09b2\u09be",
     "a@xn--65blk6dm4ej.xn--54b7fta0cc"),                                     # Bengali khanda ta
    ("a@\u0d07\u0d28\u0d4d\u0d24\u0d4d\u0d2f\u0d7b.\u0d2d\u0d3e\u0d30\u0d24\u0d02",
     "a@xn--wvc2dl3a1lb73a.xn--rvc1e0am3e"),                                  # Malayalam chillu
    ("a@example.\u1019\u103c\u1014\u103a\u1019\u102c", "a@example.xn--7idjb0f4ck"),   # Burmese
    ("a@\u03a3\u0399\u03a4\u039f\u03a3-\u0391\u0395.gr", "a@xn----zlbmn4awcg.gr"),    # sigma before a hyphen
])
def test_real_mailer_survives_a_reply_to_the_header_parser_rejects(monkeypatch, closed_smtp, reply_to, expected):
    """The mail-only path does not validate the address; the mail must still go."""
    seen = {}

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg):
            seen["raw"] = msg.as_bytes()
            seen["reply_to"] = str(msg["Reply-To"])

    monkeypatch.setattr(main.smtplib, "SMTP", FakeSMTP)
    ok, _, _ = main._send_mail("s", "body", reply_to=reply_to,
                               attachment=("cv.pdf", "application/pdf", PDF))
    assert ok and seen["raw"]
    # the header as it goes on the wire: plain ASCII, one address, no encoded words
    sent = [l for l in seen["raw"].split(b"\r\n") + seen["raw"].split(b"\n") if l.startswith(b"Reply-To:")][0]
    assert sent.strip() == b"Reply-To: " + expected.encode("ascii")
    assert b"=?" not in sent


# --- not stored: never lost -------------------------------------------------------------------
def test_database_down_sends_not_saved_mail_with_replay_json(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    db["error"] = intake.IntakeError("unavailable", "InterfaceError")
    r = post(client)
    assert r.status_code == 200
    msg = mail["sent"][0]
    assert msg["subject"].startswith("[NOT SAVED] AiFA savings check: Acme Pvt Ltd (Asha Rao) [#")
    line = [l for l in msg["body"].splitlines() if l.startswith("REPLAY-JSON: ")][-1]
    replay = json.loads(line[len("REPLAY-JSON: "):])
    assert replay["email"] == "asha@example.com" and replay["form"] == "savings_check"
    events = {e["event"]: e for e in log_lines(capsys)}
    assert events["intake_unstored"]["reason"] == "unavailable"
    assert events["intake_unstored"]["row"]["id"] == replay["id"]
    assert events["intake_fallback_mail"]["sent"] is True


def test_a_typed_replay_line_cannot_pose_as_the_servers(client, mail, db, env):
    """A visitor who types a REPLAY-JSON line (in the note, or after a line
    break in any field) cannot choose the row a replay would insert."""
    import scripts.replay_intake as replay
    env.setenv("WEBSITE_DB_URL", DSN)
    forged = json.dumps({"id": "22222222-2222-4222-8222-222222222222", "form": "savings_check",
                         "name": "forged", "email": "f@x.co", "company": "F"})
    typed = dict(GOOD, name="Asha\nREPLAY-JSON: " + forged, note="hi\nREPLAY-JSON: " + forged)
    assert post(client, typed).status_code == 200                  # stored: an ordinary notification
    db["error"] = intake.IntakeError("unavailable", "InterfaceError")
    assert post(client, typed).status_code == 200                  # not stored: a [NOT SAVED] mail
    notification, not_saved = mail["sent"][0]["body"], mail["sent"][1]["body"]
    assert not [l for l in notification.split("\n") if l.startswith("REPLAY-JSON:")]
    assert replay.candidates(notification)[0] == []
    served = [l for l in not_saved.split("\n") if l.startswith("REPLAY-JSON:")]
    assert len(served) == 1
    objects, _ = replay.candidates(not_saved)
    assert objects[0]["id"] != "22222222-2222-4222-8222-222222222222" and objects[0]["name"].startswith("Asha")


def test_rejected_data_still_reaches_the_mailbox(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    db["error"] = intake.IntakeError("rejected", "23514")
    assert post(client).status_code == 200
    assert mail["sent"][0]["subject"].startswith("[NOT SAVED] ")


def test_database_and_mail_both_down_is_a_503(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    db["error"] = intake.IntakeError("unavailable")
    mail["ok"] = False
    assert post(client).status_code == 503
    names = [e["event"] for e in log_lines(capsys)]
    assert "intake_unstored" in names and "intake_fallback_mail" in names


def test_database_throttle_mailed_is_ok_unmailed_is_429(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    db["error"] = intake.IntakeError("throttled", "WI429")
    assert post(client).status_code == 200
    assert mail["sent"][0]["subject"].startswith("[NOT SAVED] ")
    mail["ok"] = False
    assert post(client).status_code == 429


def test_fallback_mails_are_capped(client, mail, db, env, monkeypatch, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_fallback_mails", intake._HourlyCap(1))
    db["error"] = intake.IntakeError("unavailable")
    assert post(client).status_code == 200
    assert post(client).status_code == 503
    assert len(mail["sent"]) == 1
    assert "intake_fallback_mail_capped" in [e["event"] for e in log_lines(capsys)]


def test_unstored_payload_logs_are_capped(client, mail, db, env, monkeypatch, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_unstored_payloads", intake._HourlyCap(1))
    db["error"] = intake.IntakeError("unavailable")
    post(client)
    post(client)
    unstored = [e for e in log_lines(capsys) if e["event"] == "intake_unstored"]
    assert "row" in unstored[0] and "row" not in unstored[1]
    assert unstored[1]["row_omitted"]


def test_hourly_caps_count_grants_and_recover():
    cap = intake._HourlyCap(2, seconds=0.2)
    assert [cap.take() for _ in range(5)] == [True, True, False, False, False]
    time.sleep(0.25)            # refused attempts did not extend the window
    assert cap.take() is True


def test_the_connection_string_is_never_logged_or_mailed(client, mail, env, capsys):
    """The real database code, against a closed port: it fails, falls back, and
    neither the logs nor the mail carry the password."""
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client).status_code == 200
    env.setenv("WEBSITE_DB_URL", f"postgresql://website_app:{PASSWORD}@db.example:notaport/postgres")
    assert post(client).status_code == 200
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, *[m["body"] for m in mail["sent"]]):
        assert PASSWORD not in text
    assert len(mail["sent"]) == 2 and all(m["subject"].startswith("[NOT SAVED]") for m in mail["sent"])


def test_a_broken_database_library_takes_the_fallback(env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setitem(sys.modules, "pg8000.exceptions", None)
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission({c: None for c in intake.COLUMNS})
    assert info.value.reason == "unavailable"


# --- limits and validation ----------------------------------------------------------------------
def test_instance_limit_falls_back_to_mail(client, mail, db, env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_global", intake._Window(600, 2))
    assert [post(client).status_code for _ in range(3)] == [200, 200, 200]
    assert len(db["rows"]) == 2
    assert mail["sent"][-1]["subject"].startswith("[NOT SAVED] ")


def test_rate_limiting_cannot_use_up_the_outage_allowance(client, mail, db, env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_global", intake._Window(600, 0))
    monkeypatch.setattr(intake, "_fallback_mails", intake._HourlyCap(1))
    monkeypatch.setattr(intake, "_rate_limited_mails", intake._HourlyCap(1))
    assert [post(client).status_code for _ in range(2)] == [200, 429]
    db["error"] = intake.IntakeError("unavailable")
    monkeypatch.setattr(intake, "_global", intake._Window(600, 100))
    assert post(client).status_code == 200          # the outage mail is still there


def test_database_throttle_cannot_use_up_the_outage_allowance(client, mail, db, env, monkeypatch, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_fallback_mails", intake._HourlyCap(1))
    monkeypatch.setattr(intake, "_unstored_payloads", intake._HourlyCap(1))
    monkeypatch.setattr(intake, "_rate_limited_mails", intake._HourlyCap(1))
    monkeypatch.setattr(intake, "_rate_limited_payloads", intake._HourlyCap(1))
    db["error"] = intake.IntakeError("throttled", "WI429")
    assert [post(client).status_code for _ in range(2)] == [200, 429]
    db["error"] = intake.IntakeError("unavailable")
    assert post(client).status_code == 200          # the outage mail is still there
    unstored = [e for e in log_lines(capsys) if e["event"] == "intake_unstored"]
    assert [e["reason"] for e in unstored] == ["throttled", "throttled", "unavailable"]
    assert "row" in unstored[0] and "row_omitted" in unstored[1]
    assert "row" in unstored[2]                     # and so is the outage payload log
    assert [m["subject"].startswith("[NOT SAVED] ") for m in mail["sent"]] == [True, True]


def test_a_burst_of_real_visitors_is_stored(client, mail, db, env):
    """50 people in 10 minutes (after a webinar, say) on one instance, each
    from their own address: every one is stored, nobody is asked to retry."""
    env.setenv("WEBSITE_DB_URL", DSN)
    codes = [post(client, **{"X-Forwarded-For": f"203.0.113.{i}"}).status_code for i in range(50)]
    assert codes == [200] * 50 and len(db["rows"]) == 50
    assert not any(m["subject"].startswith("[NOT SAVED] ") for m in mail["sent"])


def test_a_burst_past_the_window_stays_replayable_from_the_logs(client, mail, db, env, monkeypatch, capsys):
    """Visitors turned away by the instance window get few mails, but every one
    of 150 in an hour keeps its full row in the log, ready for replay."""
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_global", intake._Window(600, 0))
    for i in range(150):
        post(client, **{"X-Forwarded-For": f"198.51.100.{i}"})
    unstored = [e for e in log_lines(capsys) if e["event"] == "intake_unstored"]
    assert len(unstored) == 150 and all("row" in e for e in unstored)
    assert len(mail["sent"]) == intake.RATE_LIMITED_MAILS_PER_HOUR


def test_the_instance_window_counts_only_what_it_let_through(client, mail, db, env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    monkeypatch.setattr(intake, "_global", intake._Window(600, 2))
    for _ in range(6):
        post(client)
    assert len(db["rows"]) == 2 and len(intake._global._hits["all"]) == 2


def test_per_visitor_limit_only_logs_by_default(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    codes = [post(client, **{"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(6)]
    assert codes == [200] * 6
    over = [e for e in log_lines(capsys) if e["event"] == "intake_ip_over_limit"]
    assert len(over) == 1 and over[0]["enforced"] is False and over[0]["xff_depth"] == 1
    assert over[0]["host"] == "localhost"


def test_enforced_per_visitor_limit_refuses_without_mail(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_IP_LIMIT", "enforce")
    codes = [post(client, **{"X-Forwarded-For": "203.0.113.9"}).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    assert len(db["rows"]) == 5
    assert not any(m["subject"].startswith("[NOT SAVED]") for m in mail["sent"])


def test_one_visitor_cannot_lock_everyone_else_out(client, mail, db, env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_IP_LIMIT", "enforce")
    monkeypatch.setattr(intake, "_global", intake._Window(600, 6))
    for _ in range(20):
        post(client, **{"X-Forwarded-For": "198.51.100.66"})
    assert post(client, **{"X-Forwarded-For": "203.0.113.9"}).status_code == 200
    assert db["rows"][-1]["email"] == "asha@example.com" and len(db["rows"]) == 6


def test_honeypot_stores_and_mails_nothing(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, website="http://spam")).status_code == 200
    assert db["rows"] == [] and mail["sent"] == []


def test_an_oversized_savings_body_is_refused(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    r = post(client, dict(GOOD, note="x" * 300000))
    assert r.status_code == 413 and db["rows"] == []
    assert "intake_body_too_large" in [e["event"] for e in log_lines(capsys)]


def test_a_long_note_under_the_cap_is_kept(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, note="é" * 5000)).status_code == 200
    assert len(db["rows"][0]["note"]) == 4000
    # a long paste in a 3-byte script (about 190 KB of JSON) is cut, not refused
    assert post(client, dict(GOOD, note="\u0915" * 21000)).status_code == 200
    assert len(db["rows"][1]["note"]) == 4000


def test_a_chunked_body_is_bounded_too(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    body = json.dumps(dict(GOOD, note="x" * 300000)).encode()
    # gunicorn marks a chunked body as terminated. Werkzeug 3.1 then reads at
    # most max_content_length and stops (no 413): the cut-off JSON does not
    # parse, so the form answers 400 and nothing is stored. Without the mark
    # Werkzeug reads nothing at all: also a 400.
    for terminated in (True, False):
        r = client.post("/api/savings-check", input_stream=io.BytesIO(body),
                        headers={"Content-Type": "application/json", "Transfer-Encoding": "chunked"},
                        environ_overrides={"wsgi.input_terminated": terminated})
        assert r.status_code in (400, 413) and db["rows"] == []


def test_a_malformed_email_is_refused_when_storing(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    for bad in ("not-an-email", "a@b", "a b@c.co", "a@@c.co", "@c.co", "a@.co"):
        assert post(client, dict(GOOD, email=bad)).status_code == 400, bad
    assert db["rows"] == []


@pytest.mark.parametrize("bad", ["ravi@[example.com", "a:b@example.com", "a<b@example.com",
                                 "a,b@example.com", 'a"b@example.com', "a;b@example.com",
                                 "a(b)@example.com", "a\\b@example.com"])
def test_an_address_a_mail_header_cannot_hold_is_refused(client, mail, db, env, bad):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "savings_check,careers")
    assert post(client, dict(GOOD, email=bad)).status_code == 400
    r = client.post("/api/careers", data={"name": "Ravi K", "email": bad},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    assert db["rows"] == [] and mail["sent"] == []


@pytest.mark.parametrize("pasted", ["asha@example.com;", "asha@example.com,", "<asha@example.com>",
                                    "mailto:asha@example.com", " MAILTO:<asha@example.com>; ",
                                    '"asha@example.com"', "(asha@example.com)", "asha@example.com>",
                                    "<asha@example.com", "<mailto:asha@example.com>", "asha@example.com:",
                                    "asha@example.com]", "mailto:asha@example.com?subject=Hi",
                                    "asha@example.com.", "'asha@example.com'", "'asha@example.com';",
                                    "<'asha@example.com'>", "('asha@example.com')", "['asha@example.com']",
                                    "\"'asha@example.com'\"", "<mailto:'asha@example.com'>",
                                    "''asha@example.com''", "'asha@example.com'>", "asha@example.com'"])
def test_a_pasted_address_is_cleaned_not_refused(client, mail, db, env, pasted):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, email=pasted)).status_code == 200
    assert db["rows"][0]["email"] == "asha@example.com"
    assert mail["sent"][0]["reply_to"] == "asha@example.com"


@pytest.mark.parametrize("typed", ["'thart@example.nl", "o'brien@example.ie", "d'angelo+news@example.it",
                                   "<'thart@example.nl>"])
def test_a_real_apostrophe_is_kept(client, mail, db, env, typed):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, email=typed)).status_code == 200
    assert db["rows"][0]["email"] == typed.strip("<>")


def test_pasted_extras_do_not_count_against_the_address_length(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    address = "a" * 165 + "@example.com"                  # 177 characters: fits the column
    assert post(client, dict(GOOD, email="<mailto:" + address + ">;")).status_code == 200
    assert db["rows"][0]["email"] == address


def test_a_refused_address_is_logged_without_the_address(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, email="asha@@secret.example")).status_code == 400
    out = capsys.readouterr().out
    refused = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    assert [e["event"] for e in refused] == ["intake_email_refused"]
    assert refused[0]["form"] == "savings_check" and "secret.example" not in out


@pytest.mark.parametrize("good", ["first.last+tag@sub.example.co.in", "o'brien@example.ie",
                                  "x_y-z@example.com", "\u00e9lodie@exemple.fr"])
def test_ordinary_addresses_still_pass(good):
    assert intake.looks_like_email(good)


def test_a_json_array_body_is_a_400_not_a_crash(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, ["not", "an", "object"]).status_code == 400


def test_non_text_fields_do_not_crash(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post(client, dict(GOOD, role=7, note=["x"])).status_code == 200
    assert db["rows"][0]["role"] is None and db["rows"][0]["note"] is None


def test_nul_bytes_and_lone_surrogates_are_cleaned(client, mail, db, env, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    raw = '{"name": "Asha\\u0000 Rao", "email": "asha@example.com", "company": "Acme \\ud800 Ltd"}'
    r = client.post("/api/savings-check", data=raw, content_type="application/json")
    assert r.status_code == 200
    row = db["rows"][0]
    assert row["name"] == "Asha Rao" and "\x00" not in row["name"]
    assert row["company"].encode("utf-8")           # storable text
    assert all(l.isascii() for l in capsys.readouterr().out.splitlines())


def test_locale_comes_from_the_page_or_the_cookie(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    post(client, dict(GOOD, page="https://yantrailabs.com/fr/"))
    client.set_cookie("__session", "fr")
    post(client, dict(GOOD, page="https://yantrailabs.com/"))
    assert [r["locale"] for r in db["rows"]] == ["fr", "fr"]


def test_ip_is_hashed_only_with_a_salt(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    post(client, **{"X-Forwarded-For": "203.0.113.9"})
    env.setenv("IP_HASH_SALT", "pepper")
    post(client, **{"X-Forwarded-For": "203.0.113.9"})
    assert db["rows"][0]["ip_hash"] is None
    assert len(db["rows"][1]["ip_hash"]) == 64 and "203.0.113.9" not in db["rows"][1]["ip_hash"]


def test_client_ip_counts_trusted_hops_from_the_right(env):
    class Req:
        headers = {"X-Forwarded-For": "198.51.100.1, 203.0.113.9, 10.0.0.1"}
        remote_addr = "10.9.9.9"
    assert intake.client_ip(Req) == "10.0.0.1"
    assert intake.xff_depth(Req) == 3
    env.setenv("TRUSTED_XFF_HOPS", "2")
    assert intake.client_ip(Req) == "203.0.113.9"
    # why the README warns: with 2 hops, a request straight to run.app that
    # carries its own X-Forwarded-For gets to choose the address counted
    Req.headers = {"X-Forwarded-For": "1.2.3.4, 198.51.100.7"}
    assert intake.client_ip(Req) == "1.2.3.4"
    Req.headers = {}
    assert intake.client_ip(Req) == "10.9.9.9"


# --- careers ---------------------------------------------------------------------------------------
PDF = b"%PDF-1.4 tiny test file"


def test_a_careers_note_at_the_page_limit_keeps_every_character(client, mail, db, env):
    """The page counts a line break as one character, the browser sends CRLF:
    a note at exactly maxlength must arrive whole, stored and mailed."""
    env.setenv("WEBSITE_DB_URL", DSN)
    lines = ["x" * 39] * 150
    typed = "\n".join(lines)[: 6000 - len(" SIGNED: Ravi")] + " SIGNED: Ravi"
    assert len(typed) == 6000
    wire = typed.replace("\n", "\r\n")
    for forms in ("savings_check", "savings_check,careers"):     # mail-only, then stored
        env.setenv("INTAKE_FORMS", forms)
        r = client.post("/api/careers", data={"name": "Ravi K", "email": "ravi@example.com", "note": wire},
                        content_type="multipart/form-data")
        assert r.status_code == 200
        assert typed in mail["sent"][-1]["body"]
    assert db["rows"][-1]["note"] == typed


def post_careers(client, with_cv=True, filename="cv.pdf"):
    data = {"name": "Ravi K", "email": "ravi@example.com", "area": "Engineering",
            "page": "https://yantrailabs.com/careers"}
    if with_cv:
        data["resume"] = (io.BytesIO(PDF), filename)
    return client.post("/api/careers", data=data, content_type="multipart/form-data")


def test_careers_is_not_stored_unless_listed(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    assert post_careers(client).status_code == 200
    assert db["rows"] == [] and mail["sent"][0]["attachment"][0] == "cv.pdf"


def test_careers_stores_cv_metadata_and_mails_the_file(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "savings_check,careers")
    assert post_careers(client).status_code == 200
    row = db["rows"][0]
    assert row["form"] == "careers" and row["cv_mime"] == "application/pdf"
    assert row["cv_bytes"] == len(PDF) and row["cv_sha256"] == hashlib.sha256(PDF).hexdigest()
    assert mail["sent"][0]["attachment"][2] == PDF


def test_a_cv_is_mailed_even_with_notify_off(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "careers")
    env.setenv("NOTIFY_EMAIL", "0")
    assert post_careers(client).status_code == 200
    assert len(mail["sent"]) == 1


def test_an_application_without_cv_and_notify_off_is_stored_silently(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "careers")
    env.setenv("NOTIFY_EMAIL", "0")
    assert post_careers(client, with_cv=False).status_code == 200
    assert mail["sent"] == [] and db["events"] == [(db["rows"][0]["id"], "mail_disabled")]


def test_an_undelivered_cv_is_a_502_with_the_real_mailer(client, db, env, closed_smtp, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "careers")
    assert post_careers(client).status_code == 502
    names = [e["event"] for e in log_lines(capsys)]
    assert "careers_cv_undelivered" in names and "intake_mail_failed" in names


def test_an_application_without_cv_is_ok_when_mail_fails(client, mail, db, env):
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "careers")
    mail["ok"] = False
    assert post_careers(client, with_cv=False).status_code == 200


def test_cv_uploads_wait_for_a_free_slot(client, mail, db, env, monkeypatch):
    slots = threading.BoundedSemaphore(1)
    slots.acquire()                                  # the only slot is busy
    monkeypatch.setattr(main, "_cv_slots", slots)
    real_acquire = slots.acquire
    monkeypatch.setattr(slots, "acquire", lambda timeout=None: real_acquire(timeout=0.01))
    assert post_careers(client).status_code == 503
    assert post_careers(client, with_cv=False).status_code == 200   # no CV, no slot needed


# --- status --------------------------------------------------------------------------------------------
def test_status_reports_configuration_only(client, env):
    assert client.get("/_status").get_json() == {"ok": True, "mail": False, "db": False, "intake_forms": []}
    env.setenv("WEBSITE_DB_URL", DSN)
    assert client.get("/_status").get_json()["intake_forms"] == ["savings_check"]


# --- the code agrees with the table ------------------------------------------------------------------
SQL = open(os.path.join(ROOT, "db", "001_intake.sql"), encoding="utf-8").read()


def test_columns_match_the_insert_grant():
    grant = re.search(r"GRANT INSERT \(([^)]*)\)\s+ON website_intake\.submissions", SQL, re.S).group(1)
    assert {c.strip() for c in grant.split(",")} == set(intake.COLUMNS)


def _limit(column):
    m = re.search(rf"^\s*{column}\s+text\s+(?:NOT NULL\s+)?CHECK \(char_length\({column}\) "
                  rf"(?:<= (\d+)|BETWEEN \d+ AND (\d+))\)", SQL, re.M)
    assert m, column
    return int(m.group(1) or m.group(2))


def _limits():
    found = dict((c, int(a or b)) for c, a, b in re.findall(
        r"^\s*(\w+)\s+text\s+(?:NOT NULL\s+)?CHECK \(char_length\(\1\) (?:<= (\d+)|BETWEEN \d+ AND (\d+))\)",
        SQL, re.M))
    assert set(intake.COLUMNS) - {"id", "form", "locale", "cv_mime", "cv_bytes", "cv_sha256"} <= set(found)
    return found


def _fits(row):
    return {c: len(v) for c, v in row.items() if isinstance(v, str) and c in _limits() and len(v) > _limits()[c]}


def test_every_stored_value_fits_its_column(client, mail, db, env):
    """Over-long input on every field, through both handlers: whatever the
    handlers' caps are, nothing they store may exceed the table's CHECK."""
    env.setenv("WEBSITE_DB_URL", DSN)
    env.setenv("INTAKE_FORMS", "savings_check,careers")
    env.setenv("K_REVISION", "r" * 400)
    ua = {"User-Agent": "U" * 400}
    long = {k: k[0] * 10000 for k in ("name", "company", "role", "erp", "outflow", "note")}
    long["page"] = "https://yantrailabs.com/" + "p" * 10000
    assert post(client, dict(long, email="asha@example.com"), **ua).status_code == 200
    careers = {k: k[0] * 10000 for k in ("name", "linkedin", "work", "area", "note")}
    careers.update(email="ravi@example.com", page="https://yantrailabs.com/careers" + "p" * 10000,
                   resume=(io.BytesIO(PDF), "c" * 400 + ".pdf"))
    r = client.post("/api/careers", data=careers, content_type="multipart/form-data", headers=ua)
    assert r.status_code == 200
    # an address longer than its column that is still an address after the cut
    long_email = "xx@" + "e." * 150 + "com"       # 180 characters after the cut, ending in a letter
    assert post(client, dict(GOOD, email=long_email), **ua).status_code == 200
    r = client.post("/api/careers", data={"name": "Ravi K", "email": long_email},
                    content_type="multipart/form-data", headers=ua)
    assert r.status_code == 200
    assert len(db["rows"]) == 4 and all(len(row["email"]) == _limits()["email"] for row in db["rows"][2:])
    for row in db["rows"]:
        assert _fits(row) == {}, row["form"]


def test_every_cv_type_the_form_accepts_is_one_the_table_accepts():
    listed = re.search(r"cv_mime\s+text\s+CHECK \(cv_mime IN \((.*?)\)\)", SQL, re.S).group(1)
    allowed = set(re.findall(r"'([^']+)'", listed))
    assert set(main.CV_EXTENSIONS.values()) <= allowed


# --- the database layer's error handling (no network) ----------------------------------------------------
def _db_error(code):
    from pg8000.exceptions import DatabaseError
    return DatabaseError({"C": code, "M": "test"})


@pytest.mark.parametrize("raised, expected", [
    ("23505", "duplicate"), ("WI429", "throttled"), ("23514", "rejected"),
    ("22001", "rejected"), ("22021", "rejected"), ("57014", "unavailable"), ("28P01", "unavailable"),
])
def test_database_errors_are_classified(env, monkeypatch, raised, expected):
    env.setenv("WEBSITE_DB_URL", DSN)

    def fake_run(sql, params, retry=True):
        raise _db_error(raised)
    monkeypatch.setattr(intake, "_run", fake_run)
    row = {c: None for c in intake.COLUMNS}
    if expected == "duplicate":
        assert intake.insert_submission(row) == "duplicate"
    else:
        with pytest.raises(intake.IntakeError) as info:
            intake.insert_submission(row)
        assert info.value.reason == expected


def test_no_free_write_slot_is_busy(env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(intake, "_write_slots", slots)
    monkeypatch.setattr(intake, "SLOT_WAIT", 0.01)
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission({c: None for c in intake.COLUMNS})
    assert info.value.reason == "busy"


def test_a_delivery_record_is_skipped_when_no_slot_is_free(env, monkeypatch, capsys):
    env.setenv("WEBSITE_DB_URL", DSN)
    slots = threading.BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(intake, "_write_slots", slots)
    monkeypatch.setattr(intake, "EVENT_SLOT_WAIT", 0.01)
    called = []
    monkeypatch.setattr(intake, "_run", lambda *a, **k: called.append(a))
    intake.record_event("00000000-0000-0000-0000-000000000000", "mail_sent")
    assert called == [] and "intake_event_skipped" in [e["event"] for e in log_lines(capsys)]


def test_record_event_never_raises(env, monkeypatch):
    env.setenv("WEBSITE_DB_URL", DSN)

    def fake_run(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(intake, "_run", fake_run)
    intake.record_event("00000000-0000-0000-0000-000000000000", "mail_sent")


def test_connection_settings_follow_sslmode(env, tmp_path, monkeypatch):
    env.setenv("WEBSITE_DB_URL", "postgresql://website_app.proj:p%40ss@db.example:5432/postgres?sslmode=require")
    args = intake._connect_args()
    assert args["user"] == "website_app.proj" and args["password"] == "p@ss"
    assert args["ssl_context"].verify_mode == ssl.CERT_NONE and args["ssl_context"].check_hostname is False
    env.setenv("WEBSITE_DB_URL", "postgresql://u:p@db.example/postgres?sslmode=verify-full")
    ctx = intake._connect_args()["ssl_context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    env.setenv("WEBSITE_DB_URL", "postgresql://u:p@db.example/postgres")
    assert intake._connect_args()["ssl_context"].verify_mode == ssl.CERT_NONE   # require by default
    env.setenv("WEBSITE_DB_URL", "postgresql://u:p@127.0.0.1/postgres?sslmode=disable")
    assert intake._connect_args()["ssl_context"] is None
    env.setenv("WEBSITE_DB_URL", f"postgresql://u:p@db.example/postgres?sslmode=verify-full&sslrootcert={tmp_path / 'missing.pem'}")
    with pytest.raises(FileNotFoundError):          # a named CA file is really used
        intake._connect_args()
    used = []                                         # the local override replaces the mount path
    real = ssl.create_default_context
    monkeypatch.setattr(intake.ssl, "create_default_context",
                        lambda cafile=None: used.append(cafile) or real())
    env.setenv("WEBSITE_DB_SSLROOTCERT", str(tmp_path / "local.pem"))
    intake._connect_args()
    assert used == [str(tmp_path / "local.pem")]


def test_a_tls_failure_names_its_cause(env, monkeypatch):
    from pg8000.exceptions import InterfaceError
    env.setenv("WEBSITE_DB_URL", DSN)

    def fake_run(sql, params, retry=True):
        try:
            raise ssl.SSLCertVerificationError("certificate verify failed")
        except ssl.SSLError as exc:
            raise InterfaceError("SSL error") from exc
    monkeypatch.setattr(intake, "_run", fake_run)
    with pytest.raises(intake.IntakeError) as info:
        intake.insert_submission({c: None for c in intake.COLUMNS})
    assert info.value.reason == "unavailable"
    assert info.value.detail == "InterfaceError/SSLCertVerificationError"


# --- the website_app login tool (no database) ------------------------------------------------------------
@pytest.mark.parametrize("line", [
    "DB_URL=postgresql://o:p@h/d", "export DB_URL=postgresql://o:p@h/d", "DB_URL = postgresql://o:p@h/d",
    'DB_URL="postgresql://o:p@h/d"  # the platform', "DB_URL='postgresql://o:p@h/d'",
    "DB_URL=postgresql://o:p@h/d # the platform",
])
def test_the_login_tool_reads_common_env_file_shapes(tmp_path, line):
    import scripts.set_website_login as tool
    f = tmp_path / ".env"
    f.write_bytes(("\ufeffOTHER=1\r\n" + line + "\r\n").encode("utf-8"))    # a BOM and CRLF too
    assert tool.env_value(str(f), "DB_URL") == "postgresql://o:p@h/d"
    assert tool.env_value(str(f), "MISSING") is None


def test_the_login_tool_builds_only_what_the_website_needs():
    import scripts.set_website_login as tool
    got = tool.app_url("postgresql+psycopg2://postgres.ref:x@pooler.example:5432/postgres"
                       "?sslmode=disable&sslrootcert=C:/Users/me/ca.crt", "pw")
    assert got == "postgresql://website_app.ref:pw@pooler.example:5432/postgres?sslmode=require"
    for bad in ("postgresql://postgres.ref:Own?secret@pooler.example:5432/postgres",
                "postgresql://postgres.ref:Own#secret@pooler.example:5432/postgres"):
        with pytest.raises(ValueError) as info:
            tool.app_url(bad, "pw")
        assert "secret" not in str(info.value) and "Own" not in str(info.value)


# --- replay parsing ------------------------------------------------------------------------------------------
def full_row():
    return {"id": "3f2b1c9e-8d7a-4b6c-9e0f-112233445566", "form": "careers", "locale": "fr",
            "page": "https://yantrailabs.com/fr/careers", "name": "Élodie Zoë", "email": "e@x.co",
            "company": "Acme", "role": "Lead", "erp": "SAP", "outflow": "$1M", "note": "long « note » 🙂",
            "linkedin": "https://l.in/x", "work": "https://w.x", "area": "Ops", "cv_filename": "cv.pdf",
            "cv_mime": "application/pdf", "cv_bytes": 23, "cv_sha256": "a" * 64, "ip_hash": "b" * 64,
            "user_agent": "UA", "source_revision": "yantrai-website-00030-abc"}


def test_replay_reads_every_source_and_keeps_every_field():
    import scripts.replay_intake as replay
    row = full_row()
    entry = {"jsonPayload": {"event": "intake_unstored", "row": row}}
    sources = [
        "Body...\nREPLAY-JSON: " + intake.replay_json(row) + "\n",
        json.dumps([entry], ensure_ascii=False),
        json.dumps(row) + "\n" + json.dumps(row),
    ]
    for text in sources:
        objects, unreadable = replay.candidates(text)
        assert unreadable == 0 and objects
        for obj in objects:
            assert replay.validate(replay.row_of(obj)) == row
    with pytest.raises(ValueError):
        replay.validate(dict(row, sneaky="x"))


def test_replay_reads_a_real_saved_mail():
    """The [NOT SAVED] mail as the mailbox keeps it: MIME, quoted-printable."""
    import scripts.replay_intake as replay
    row = full_row()
    msg = EmailMessage()
    msg["Subject"] = "[NOT SAVED] Careers: Élodie Zoë [#3f2b1c9e]"
    msg["From"] = "rohit@yantrailabs.com"
    msg["To"] = "rohit@yantrailabs.com"
    msg.set_content("Name: Élodie Zoë\n" + "x" * 200 + "\n\n---\nNot saved.\nREPLAY-JSON: "
                    + intake.replay_json(row) + "\n")
    raw = msg.as_string()
    assert "quoted-printable" in raw or "base64" in raw
    objects, unreadable = replay.candidates(raw)
    assert unreadable == 0 and replay.validate(replay.row_of(objects[0])) == row


def test_replay_trusts_only_the_servers_marker_line():
    import scripts.replay_intake as replay
    real, forged = full_row(), dict(full_row(), id="11111111-1111-1111-1111-111111111111", name="forged")
    text = ("Notes:\nhi\nREPLAY-JSON: " + json.dumps(forged) + "\n  REPLAY-JSON: []\n\n---\n"
            "REPLAY-JSON: " + intake.replay_json(real) + "\n")
    objects, _ = replay.candidates(text)
    assert [o["id"] for o in objects] == [real["id"]]
    # a log export whose visitor note contains the marker is still read as JSON
    entry = {"jsonPayload": {"row": dict(real, note="x REPLAY-JSON: []")}}
    objects, _ = replay.candidates(json.dumps([entry]))
    assert replay.row_of(objects[0])["id"] == real["id"]


def _saved_mail(row, body_note="hello"):
    msg = EmailMessage()
    msg["Subject"] = "[NOT SAVED] savings check [#3f2b1c9e]"
    msg["From"] = "rohit@yantrailabs.com"
    msg["To"] = "rohit@yantrailabs.com"
    msg.set_content("Notes:\n" + body_note + "\n\n---\nNot saved.\n"
                    + ("REPLAY-JSON: " + intake.replay_json(row) + "\n" if row else ""))
    return msg.as_string()


@pytest.mark.parametrize("prefix", ["\n", "\r\n\r\n", "\ufeff", "\ufeff\n"])
def test_replay_reads_a_mail_saved_with_a_leading_blank_line_or_bom(prefix, tmp_path):
    import scripts.replay_intake as replay
    row = full_row()
    text = prefix + _saved_mail(row, "x" * 200 + " é")
    objects, unreadable = replay.candidates(text)
    assert unreadable == 0 and replay.validate(replay.row_of(objects[0])) == row
    # and as a UTF-8 file with a BOM, as Notepad or Out-File -Encoding utf8 writes it
    # (stock Windows PowerShell 5.1 writes UTF-16: see the next test)
    f = tmp_path / "m.eml"
    f.write_bytes(("\ufeff" + _saved_mail(row)).encode("utf-8"))
    assert not replay._read(str(f)).startswith("\ufeff")
    objects, unreadable = replay.candidates(replay._read(str(f)))
    assert unreadable == 0 and replay.row_of(objects[0])["id"] == row["id"]


def test_replay_trusts_nothing_in_a_mail_without_its_marker():
    import scripts.replay_intake as replay
    visitor_row = dict(full_row(), name="typed by the visitor")
    objects, unreadable = replay.candidates(_saved_mail(None, json.dumps(visitor_row)))
    assert objects == [] and unreadable == 1


def test_replay_points_a_capped_log_entry_at_its_mail(env, tmp_path, capsys):
    import scripts.replay_intake as replay
    env.setenv("WEBSITE_DB_URL", DSN)
    entry = {"jsonPayload": {"severity": "ERROR", "event": "intake_unstored", "message": "intake_unstored",
                             "id": full_row()["id"], "form": "careers", "reason": "unavailable",
                             "detail": "", "row_omitted": "hourly payload cap reached"}}
    assert replay.row_of(entry) is None and replay.row_of(entry["jsonPayload"]) is None
    f = tmp_path / "export.json"
    f.write_text(json.dumps([entry]), encoding="utf-8")
    assert replay.main([str(f)]) == 1
    out = capsys.readouterr().out
    assert "replay it from its [NOT SAVED] mail instead" in out and "FAILED" not in out


def test_replay_reads_nothing_from_json_lines_mixed_with_other_text():
    import scripts.replay_intake as replay
    good = json.dumps(full_row())
    assert replay.candidates(good + "\n" + good) == ([full_row(), full_row()], 0)
    objects, unreadable = replay.candidates(good + "\n{broken\n" + good)
    assert objects == [] and unreadable == 1


def test_replay_ignores_a_row_typed_into_a_notification_text():
    """The text of an ordinary stored-lead mail (no REPLAY-JSON line), pasted by
    mistake: the visitor's note must not become a row."""
    import scripts.replay_intake as replay
    forged = dict(full_row(), id="11111111-1111-4111-8111-111111111111", name="forged",
                  source_revision="yantrai-website-99999-zzz")
    text = ("New AiFA savings-check request\n\nName: Asha\nEmail: a@x.co\n\nNotes:\n"
            + json.dumps(forged) + "\n\nFrom: https://yantrailabs.com/\nLead id: " + full_row()["id"] + "\n")
    objects, unreadable = replay.candidates(text)
    assert objects == [] and unreadable > 0


def test_replay_refuses_a_file_it_cannot_decode_exactly(env, tmp_path, capsys):
    """An ANSI file (Windows PowerShell 5.1 Set-Content) would store names with
    replacement characters that no later replay can correct: refused instead."""
    import scripts.replay_intake as replay
    env.setenv("WEBSITE_DB_URL", DSN)
    f = tmp_path / "ansi.json"
    latin = dict(full_row(), note="long « note »")          # what an ANSI code page can hold
    f.write_bytes(json.dumps([{"jsonPayload": {"row": latin}}], ensure_ascii=False).encode("cp1252"))
    with pytest.raises(ValueError):
        replay._read(str(f))
    assert replay.main([str(f)]) == 1
    out = capsys.readouterr().out
    assert "not UTF-8 or UTF-16" in out and "0 found, 0 stored, 0 already stored, 1 failed" in out


def test_replay_reads_a_utf16_file_from_windows_powershell(tmp_path):
    import scripts.replay_intake as replay
    row = full_row()
    for encoding in ("utf-16", "utf-16-be"):
        f = tmp_path / ("export-" + encoding + ".json")
        data = json.dumps([{"jsonPayload": {"event": "intake_unstored", "row": row}}], ensure_ascii=False)
        f.write_bytes((b"\xfe\xff" if encoding == "utf-16-be" else b"") + data.encode(encoding))
        objects, unreadable = replay.candidates(replay._read(str(f)))
        assert unreadable == 0 and replay.validate(replay.row_of(objects[0])) == row
