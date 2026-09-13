import smtplib
import socket
import logging
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from app.config import (
    SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD,
    SMTP_FROM, SMTP_USE_TLS, SMTP_USE_SSL,
)

logger = logging.getLogger(__name__)


class _IPv4SMTP(smtplib.SMTP):
    """SMTP that connects over IPv4 only.

    Gmail advertises both A and AAAA records, and ``smtplib`` picks whatever
    ``getaddrinfo`` returns first. On hosts without a working IPv6 route
    (e.g. some serverless/container providers) that raises
    ``[Errno 101] Network is unreachable``. Resolving to IPv4 explicitly
    avoids it and keeps a proper domain in EHLO.
    """

    _ssl = False

    def __init__(self, host, port, local_hostname=None, timeout=30):
        ip = socket.gethostbyname(host)
        server_cls = smtplib.SMTP_SSL if self._ssl else smtplib.SMTP
        server_cls.__init__(self, ip, port, local_hostname=local_hostname or host, timeout=timeout)


class _IPv4SMTP_SSL(_IPv4SMTP):
    _ssl = True


def _connect_smtp(host, port, use_ssl, timeout=30, use_tls=True):
    """Open an SMTP connection, trying the configured mode plus a fallback port.

    Returns a connected ``SMTP`` (or ``SMTP_SSL``) object, or raises
    ``ConnectionError`` with the last underlying error.
    """
    candidates = [(port, use_ssl, bool(use_tls) if not use_ssl else False)]
    if not (port == 465 and use_ssl):
        candidates.append((465, True, False))
    if not (port == 587 and not use_ssl):
        candidates.append((587, False, True))

    last_error = None
    for cand_port, cand_ssl, cand_tls in candidates:
        try:
            server = _IPv4SMTP_SSL(host, cand_port, timeout=timeout) if cand_ssl else _IPv4SMTP(host, cand_port, timeout=timeout)
            if not cand_ssl and cand_tls:
                server.starttls()
            logger.info("SMTP connected via %s:%s (%s)", host, cand_port, "SSL" if cand_ssl else "STARTTLS")
            return server
        except (OSError, smtplib.SMTPException) as e:
            last_error = e
            logger.warning("SMTP connection to %s:%s (%s) failed: %s", host, cand_port, "SSL" if cand_ssl else "STARTTLS", e)

    tried = ", ".join("{} / {}".format(p, "SSL" if s else "STARTTLS") for p, s, _ in candidates)
    raise ConnectionError(
        f"Unable to connect to SMTP server {host} (ports tried: {tried}): {last_error}"
    )


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

    server = _connect_smtp(SMTP_HOST, SMTP_PORT, SMTP_USE_SSL, use_tls=SMTP_USE_TLS)

    try:
        if SMTP_USERNAME:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, recipients, msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
