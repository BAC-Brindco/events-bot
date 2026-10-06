"""SMTP email channel. Credentials only from env (Settings)."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr

import truststore

from ..core.models import Message
from ..core.settings import Settings


class ConfigError(RuntimeError):
    pass


class EmailChannel:
    name = "email"

    def __init__(self, settings: Settings):
        self.s = settings

    def send(self, msg: Message, recipients: list[str]) -> None:
        s = self.s
        if not (s.smtp_host and s.smtp_from):
            raise ConfigError("SMTP_HOST / SMTP_FROM not set (FLAGS F-10)")
        m = EmailMessage()
        m["Subject"] = msg.subject
        m["From"] = s.smtp_from
        m["To"] = ", ".join(recipients)
        m["Date"] = formatdate(localtime=False)
        m["Message-ID"] = make_msgid(domain=parseaddr(s.smtp_from)[1].split("@")[-1] or None)
        m["X-Events-Bot-Ref"] = f"{msg.ref} {msg.stage} {msg.kind}"
        m.set_content(msg.body_text)
        m.add_alternative(msg.body_html, subtype="html")
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=ctx, timeout=30) as c:
                if s.smtp_user:
                    c.login(s.smtp_user, s.smtp_password or "")
                c.send_message(m)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as c:
                if s.smtp_starttls:
                    c.starttls(context=ctx)
                if s.smtp_user:
                    c.login(s.smtp_user, s.smtp_password or "")
                c.send_message(m)
