from __future__ import annotations

import imaplib
import smtplib
import socket
import ssl
from copy import deepcopy
from email.utils import parseaddr
from typing import Any
from xml.etree import ElementTree

import httpx

from backend.agents.sentero.mail.mail_provider_fallback import MAIL_PROVIDER_FALLBACKS
from backend.agents.sentero.mail.models import MailConfig, MailEncryption

ISPDB_URL = "https://autoconfig.thunderbird.net/v1.1/{domain}"
HTTP_TIMEOUT_SECONDS = 3.0
MAIL_LOGIN_TIMEOUT_SECONDS = 10.0


async def discover_mail_settings(email: str) -> MailConfig | None:
    """Discover IMAP/SMTP settings from Mozilla ISPDB for an email address."""

    domain = email_domain(email)
    if not domain:
        return None

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(ISPDB_URL.format(domain=domain))
            response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        return None

    try:
        return parse_ispdb_config(response.content)
    except (ElementTree.ParseError, ValueError, TypeError):
        return None


async def get_mail_settings(email: str) -> MailConfig | None:
    """Resolve mail settings via ISPDB first and static provider fallbacks second."""

    discovered = await discover_mail_settings(email)
    if discovered:
        return discovered

    domain = email_domain(email)
    if not domain:
        return None
    config = MAIL_PROVIDER_FALLBACKS.get(domain)
    return deepcopy(config) if config else None


def parse_ispdb_config(xml_payload: bytes | str) -> MailConfig | None:
    """Parse Mozilla clientConfig XML and return the first IMAP/SMTP pair."""

    root = ElementTree.fromstring(xml_payload)
    incoming = _first_server(root, "incomingServer", "imap")
    outgoing = _first_server(root, "outgoingServer", "smtp")
    if incoming is None or outgoing is None:
        return None

    return MailConfig(
        imap_host=_required_text(incoming, "hostname"),
        imap_port=_required_port(incoming, "port"),
        imap_encryption=_encryption_from_socket_type(_text(incoming, "socketType")),
        smtp_host=_required_text(outgoing, "hostname"),
        smtp_port=_required_port(outgoing, "port"),
        smtp_encryption=_encryption_from_socket_type(_text(outgoing, "socketType")),
        auth_method=_text(outgoing, "authentication") or _text(incoming, "authentication"),
        source="ispdb",
    )


def email_domain(email: str) -> str | None:
    """Extract and normalize the domain part from an email address."""

    _, address = parseaddr(str(email or "").strip())
    if not address or address.count("@") != 1:
        return None
    _, domain = address.rsplit("@", 1)
    domain = domain.strip().rstrip(".").lower()
    if not domain or "." not in domain or any(char.isspace() for char in domain):
        return None
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def verify_mail_credentials(
    config: MailConfig,
    email: str,
    password: str,
    imap_username: str | None = None,
    smtp_username: str | None = None,
) -> tuple[bool, str | None]:
    """Verify IMAP and SMTP login credentials for a discovered mail configuration."""

    username = parseaddr(str(email or "").strip())[1] or str(email or "").strip()
    imap_login = str(imap_username or username).strip()
    smtp_login = str(smtp_username or username).strip()
    try:
        _verify_imap_login(config, imap_login, password)
        _verify_smtp_login(config, smtp_login, password)
    except UnicodeEncodeError:
        return False, "Das gespeicherte Passwort konnte nicht verwendet werden. Bitte geben Sie das Passwort oder App-Passwort erneut ein."
    except imaplib.IMAP4.error:
        return False, "Die Anmeldung am Posteingangsserver ist fehlgeschlagen. Bitte prüfen Sie E-Mail-Adresse und Passwort."
    except smtplib.SMTPAuthenticationError:
        return False, "Die Anmeldung am Postausgangsserver ist fehlgeschlagen. Bitte prüfen Sie E-Mail-Adresse und Passwort."
    except (socket.timeout, TimeoutError):
        return False, "Der Mailserver antwortet nicht rechtzeitig. Bitte prüfen Sie die Serverdaten oder versuchen Sie es später erneut."
    except ssl.SSLError:
        return False, "Die verschlüsselte Verbindung zum Mailserver konnte nicht aufgebaut werden. Bitte prüfen Sie die Verschlüsselungseinstellungen."
    except (ConnectionError, OSError):
        return False, "Die Verbindung zum Mailserver konnte nicht hergestellt werden. Bitte prüfen Sie Server, Port und Verschlüsselung."
    except smtplib.SMTPException:
        return False, "Der Postausgangsserver hat die Anmeldung abgelehnt. Bitte prüfen Sie die SMTP-Einstellungen."
    return True, None


def _verify_imap_login(config: MailConfig, username: str, password: str) -> None:
    client: imaplib.IMAP4
    if config.imap_encryption == MailEncryption.SSL:
        client = imaplib.IMAP4_SSL(config.imap_host, config.imap_port, timeout=MAIL_LOGIN_TIMEOUT_SECONDS)
    else:
        client = imaplib.IMAP4(config.imap_host, config.imap_port, timeout=MAIL_LOGIN_TIMEOUT_SECONDS)
    try:
        if config.imap_encryption == MailEncryption.STARTTLS:
            client.starttls()
        client.login(username, password)
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _verify_smtp_login(config: MailConfig, username: str, password: str) -> None:
    client: smtplib.SMTP
    if config.smtp_encryption == MailEncryption.SSL:
        client = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port, timeout=MAIL_LOGIN_TIMEOUT_SECONDS)
    else:
        client = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=MAIL_LOGIN_TIMEOUT_SECONDS)
    try:
        client.ehlo()
        if config.smtp_encryption == MailEncryption.STARTTLS:
            client.starttls()
            client.ehlo()
        client.login(username, password)
    finally:
        try:
            client.quit()
        except Exception:
            pass


def _first_server(root: ElementTree.Element, tag_name: str, server_type: str) -> ElementTree.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == tag_name and str(element.attrib.get("type", "")).lower() == server_type:
            return element
    return None


def _required_text(element: ElementTree.Element, child_name: str) -> str:
    value = _text(element, child_name)
    if not value:
        raise ValueError(f"missing {child_name}")
    return value


def _required_port(element: ElementTree.Element, child_name: str) -> int:
    return int(_required_text(element, child_name))


def _text(element: ElementTree.Element, child_name: str) -> str | None:
    child = _find_child(element, child_name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _find_child(element: ElementTree.Element, child_name: str) -> ElementTree.Element | None:
    for child in list(element):
        if _local_name(child.tag) == child_name:
            return child
    return None


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _encryption_from_socket_type(value: str | None) -> MailEncryption:
    normalized = str(value or "").strip().lower()
    if normalized in {"ssl", "ssl/tls", "tls"}:
        return MailEncryption.SSL
    if normalized in {"starttls", "starttls/tls"}:
        return MailEncryption.STARTTLS
    if normalized in {"plain", "none", "cleartext"}:
        return MailEncryption.NONE
    return MailEncryption.NONE
