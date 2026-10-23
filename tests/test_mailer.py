"""A kimeno level fejlecei: Date es Message-ID (RFC 5322 - hianyuk spampontot er)."""
from __future__ import annotations

from stockwatch import mailer


def test_message_has_date_and_message_id(monkeypatch):
    env = {"SMTP_HOST": "smtp.example.com", "SMTP_PORT": "587", "MAIL_FROM": "StockWatch <hello@example.com>"}
    monkeypatch.setattr(mailer.config, "env_value", lambda k: env.get(k))
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    mailer._deliver("anna@example.com", "Targy", "Szoveg")
    msg = sent[0]
    assert msg["Date"] and msg["Message-ID"].endswith("@example.com>") and msg["To"] == "anna@example.com"
