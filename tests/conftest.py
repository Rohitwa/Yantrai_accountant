import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# everything that changes how the forms behave: cleared for every test, so a
# developer's shell (a real SMTP_PASS, say) never changes a result
FORM_ENV = ("WEBSITE_DB_URL", "WEBSITE_DB_SSLROOTCERT", "INTAKE_FORMS", "NOTIFY_EMAIL", "IP_HASH_SALT",
            "TRUSTED_XFF_HOPS", "INTAKE_IP_LIMIT", "K_REVISION", "GIT_COMMIT",
            "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "DEMO_TO_EMAIL", "DEMO_FROM_EMAIL")


@pytest.fixture
def env(monkeypatch):
    """A clean environment; tests set what they need."""
    for key in FORM_ENV:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def fresh_intake(monkeypatch):
    """Fresh per-instance counters, so one test's traffic never limits another's."""
    import intake
    monkeypatch.setattr(intake, "_per_ip", intake._Window(intake.IP_WINDOW, intake.IP_LIMIT))
    monkeypatch.setattr(intake, "_global", intake._Window(intake.GLOBAL_WINDOW, intake.GLOBAL_LIMIT))
    monkeypatch.setattr(intake, "_unstored_payloads", intake._HourlyCap(intake.UNSTORED_PAYLOAD_PER_HOUR))
    monkeypatch.setattr(intake, "_fallback_mails", intake._HourlyCap(intake.FALLBACK_MAILS_PER_HOUR))
    monkeypatch.setattr(intake, "_rate_limited_payloads",
                        intake._HourlyCap(intake.RATE_LIMITED_PAYLOAD_PER_HOUR))
    monkeypatch.setattr(intake, "_rate_limited_mails", intake._HourlyCap(intake.RATE_LIMITED_MAILS_PER_HOUR))
    return intake
