import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from app.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    SMTP_FROM, SMTP_USE_TLS, SMTP_USE_SSL,
)

logger = logging.getLogger(__name__)


def email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def email_missing_config() -> str:
    missing = []
    if not SMTP_HOST:
        missing.append("SMTP_HOST")
    if not SMTP_FROM:
        missing.append("SMTP_FROM")
    if SMTP_HOST and not SMTP_USERNAME:
        missing.append("SMTP_USERNAME")
    if SMTP_HOST and SMTP_USERNAME and not SMTP_PASSWORD:
        missing.append("SMTP_PASSWORD")
    return "; ".join(missing) if missing else ""


def _send_sync(recipients, subject, body_html, attachments=None, body_text=None):
    if not email_configured():
        missing = email_missing_config() or "SMTP configuration"
        raise RuntimeError(
            f"SMTP is not configured. Missing: {missing}. "
            "Add them to backend/.env and restart the server."
        )

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if body_text:
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    for name, content, content_type in (attachments or []):
        part = MIMEApplication(content, _subtype=content_type.split("/")[-1])
        part.add_header("Content-Disposition", "attachment", filename=name)
        msg.attach(part)

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
        if SMTP_USE_TLS:
            server.starttls()

    try:
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
