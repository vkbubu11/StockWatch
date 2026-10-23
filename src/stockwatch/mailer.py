"""Tranzakcios e-mail (e-mail-ellenorzes, jelszo-visszaallitas) SMTP-n, a standard konyvtarral - barmely
szolgaltato SMTP-kapuja jo (Brevo, Mailjet, Resend, sajat domain). A kuldes hatterszalon fut: a hibaja
naplozodik, de a feluletet nem akasztja meg.

Titokfajl (.env / STOCKWATCH_ENV_FILE):
    SMTP_HOST, SMTP_PORT (587: STARTTLS, 465: SSL), SMTP_USER, SMTP_PASSWORD, MAIL_FROM
    MAIL_TO_LOG=1   csak fejleszteshez: SMTP helyett a level (a linkkel) a naploba kerul
"""
from __future__ import annotations

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

from . import config

log = logging.getLogger(__name__)


def configured() -> bool:
    return bool(config.env_value("SMTP_HOST") and config.env_value("MAIL_FROM"))


def send(to: str, subject: str, body: str) -> None:
    threading.Thread(target=_deliver, args=(to, subject, body), daemon=True, name="mailer").start()


def _deliver(to: str, subject: str, body: str) -> None:
    if not configured():
        if config.env_value("MAIL_TO_LOG") == "1":
            log.warning("MAIL_TO_LOG - level %s cimre: %s\n%s", to, subject, body)
        else:
            log.error("Nincs beallitva SMTP (SMTP_HOST, MAIL_FROM) - a level nem ment el: %s", subject)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.env_value("MAIL_FROM"), to, subject
    # a Date es a Message-ID kotelezo fejlec (RFC 5322); hianyuk a spamszuroknel rontja a levelet
    msg["Date"] = formatdate(usegmt=True)
    msg["Message-ID"] = make_msgid(domain=parseaddr(msg["From"])[1].rpartition("@")[2] or None)
    msg.set_content(body)
    host, port = config.env_value("SMTP_HOST"), int(config.env_value("SMTP_PORT") or 587)
    user, password = config.env_value("SMTP_USER"), config.env_value("SMTP_PASSWORD")
    try:
        context = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=context) if port == 465 else smtplib.SMTP(host, port, timeout=20)
        with smtp:
            if port != 465:
                smtp.starttls(context=context)
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("A level nem ment el (%s): %s", subject, type(exc).__name__)
