from __future__ import annotations

import email
import imaplib
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime

from backend.agents.sentero.mail.models import InboundMail, MailAssistantConfig
from backend.services.device_mapping_service import now


class ImapMailClient:
    def __init__(self, config: MailAssistantConfig) -> None:
        self.config = config

    def fetch_unseen(self, limit: int = 20) -> list[InboundMail]:
        with imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port) as client:
            client.login(self.config.imap_username, self.config.imap_password)
            client.select("INBOX")
            status, data = client.search(None, "UNSEEN")
            if status != "OK":
                return []
            uids = (data[0] or b"").split()[:limit]
            messages: list[InboundMail] = []
            for uid in uids:
                status, payload = client.fetch(uid, "(RFC822)")
                if status != "OK" or not payload:
                    continue
                raw = next((item[1] for item in payload if isinstance(item, tuple)), None)
                if not raw:
                    continue
                messages.append(parse_message(uid.decode("ascii", "ignore"), raw))
            return messages

    def mark_processed(self, uid: str) -> None:
        with imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port) as client:
            client.login(self.config.imap_username, self.config.imap_password)
            client.select("INBOX")
            client.store(uid, "+FLAGS", "\\Seen")


def parse_message(uid: str, raw: bytes) -> InboundMail:
    msg = email.message_from_bytes(raw)
    sender = first_address(msg.get("From", ""))
    recipients = [addr for _, addr in getaddresses([msg.get("To", ""), msg.get("Cc", ""), msg.get("Delivered-To", "")]) if addr]
    received_at = now()
    if msg.get("Date"):
        try:
            received_at = parsedate_to_datetime(msg.get("Date", "")).astimezone().isoformat(timespec="seconds")
        except Exception:
            pass
    return InboundMail(
        uid=uid,
        message_id=str(msg.get("Message-ID") or f"imap:{uid}").strip(),
        sender_email=sender,
        recipient_addresses=recipients,
        subject=str(msg.get("Subject") or ""),
        body=plain_body(msg),
        received_at=received_at,
        in_reply_to=str(msg.get("In-Reply-To") or "").strip() or None,
        references=str(msg.get("References") or "").strip() or None,
        x_sentero_generated=str(msg.get("X-Sentero-Generated") or "").strip() or None,
        auto_submitted=str(msg.get("Auto-Submitted") or "").strip() or None,
        has_attachments=has_attachments(msg),
    )


def first_address(value: str) -> str:
    parsed = getaddresses([value])
    return parsed[0][1].lower() if parsed else ""


def plain_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            if part.get_content_type() == "text/plain":
                return decode_part(part)
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return strip_html(decode_part(part))
        return ""
    if msg.get_content_type() == "text/html":
        return strip_html(decode_part(msg))
    return decode_part(msg)


def decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return str(part.get_payload() or "")
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace").strip()


def strip_html(value: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def has_attachments(msg: Message) -> bool:
    return any("attachment" in str(part.get("Content-Disposition") or "").lower() for part in msg.walk())
