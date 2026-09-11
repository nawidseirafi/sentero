# Microsoft Mail Authentication

Sentero keeps IMAP/SMTP and replaces Microsoft password authentication with delegated OAuth2. This preserves the existing inbox search/fetch/Seen behavior, message threading, SMTP provider and notification outbox. Graph is not required for this fix; a future Graph adapter remains possible.

Microsoft documents OAuth for both Outlook.com and Microsoft 365 IMAP/SMTP:
https://learn.microsoft.com/en-us/exchange/client-developer/legacy-protocols/how-to-authenticate-an-imap-pop-smtp-application-by-using-oauth

## Product Configuration

Before shipping, supply the Sentero public application ID centrally through `SENTERO_MICROSOFT_CLIENT_ID` or `mail.microsoft.client_id` in Sentero configuration. No client ID is fabricated or entered by a family user. No client secret is used. This repository does not currently supply a production registration ID.

Register an application supporting organizational directories and personal Microsoft accounts, enable public client/device code flow, and configure delegated Exchange Online permissions `IMAP.AccessAsUser.All` and `SMTP.Send`. The authority defaults to `https://login.microsoftonline.com/common`. `SENTERO_MICROSOFT_AUTHORITY` or `mail.microsoft.authority` may specify consumers, organizations, or a tenant UUID on the same official host. National cloud endpoints are not implemented.

MSAL requests `https://outlook.office.com/IMAP.AccessAsUser.All` and `https://outlook.office.com/SMTP.Send`. MSAL adds `offline_access` automatically; explicitly passing that reserved scope would be incorrect. Organizational policies may require administrator consent or disallow device flow. IMAP and authenticated SMTP must be enabled for the mailbox; OAuth does not override tenant protocol restrictions.

## Setup and Storage

Authenticated Sentero sessions can POST `/api/mail/microsoft/connect/start` with the mailbox email, GET `/api/mail/microsoft/connect/status`, and POST `/api/mail/microsoft/disconnect`. These routes are deliberately absent from the anonymous endpoint allowlist. Existing anonymous password-provider verification remains unchanged; OAuth verification additionally requires a session.

The user selects "Mit Microsoft verbinden", opens Microsoft's device login page and enters the short code. MSAL polls in a separate thread. The signed-in MSAL account username must match the requested mailbox; shared mailboxes and aliases differing from the sign-in username are not supported in this patch. After connection, the existing connection verification, test-mail and Save actions remain available.

Only pending flows expose the user code, official verification URL and remaining lifetime. Device codes, tokens, raw Microsoft responses and token caches never enter API responses. Wrong-account sign-in fails without persisting the new cache. Disconnect removes the local cache and cancels the flow; a late response cannot restore it. It does not revoke consent at Microsoft, modify the outbox, or delete other data.

The MSAL serialized cache and mailbox/home-account binding live in `data/secrets/microsoft-mail.json`, outside SQLite and existing application exports. The existing persistent data volume retains this across container updates. The directory is 0700 and cache/temp files are 0600; writes use a same-directory temporary file and atomic replacement. This is local filesystem protection, not encryption against administrators or someone with access to the disk. Restrict any independent whole-volume backups accordingly. The service is serialized for the existing single-process appliance runtime; multiple application replicas sharing this file are not supported.

The existing network secret store is not reused: its namespace belongs to network provisioning, and its write routine creates the temporary file before tightening permissions. The OAuth cache has its own private directory and creates temporary files with restrictive permissions from the outset.

## Runtime and Migration

Normal operation only uses silent token acquisition. SMTP/IMAP authentication rejection triggers one forced silent refresh and one authentication retry. Consent errors persist `reconnect_required`; subsequent calls do not repeatedly contact Microsoft. The assistant becomes inactive until reconnected, and outbox reconnect errors use the existing one-hour channel backoff. Temporary failures retain the existing backoff. Startup, health, updater and network recovery are unchanged.

Microsoft is recognized through its known mail hosts, personal mailbox domains and authentication metadata. This is also evaluated for legacy settings on every use, so old stored Microsoft passwords cannot fall back to Basic Auth. Hosts and mailbox fields are retained. Saving an OAuth configuration stores only public metadata, ignores incoming Microsoft passwords and retains previously stored passwords without using them. No schema migration or automatic password deletion is performed. Non-Microsoft password providers retain their existing behavior.

Both transports use the shared token/authentication helper. IMAP callbacks return raw SASL bytes; imaplib performs base64 encoding. SMTP uses smtplib `auth("XOAUTH2", ...)`, which encodes its initial response, after EHLO/STARTTLS. OAuth transports enforce TLS certificate verification and only send bearer tokens to the known Microsoft mail endpoints. Notification sends, outbox, test sends, assistant replies and password-reset emails share the email provider.

## Validation and Field Acceptance

Mocked Microsoft/IMAP/SMTP tests cover authentication selection, refresh, revoked consent, persistence/permissions, expiry, disconnect races, verification, assistant threading/Seen, outbox preservation and sanitizer behavior. Existing startup tests exercise a real local Uvicorn server with blocked providers. No production mailbox, device flow or real-box deployment is performed by these tests.

Before release, test with the actual Sentero registration and both intended account types: connect once, verify IMAP/SMTP, send a test notification, receive and answer a threaded mail, restart the box, revoke consent and verify reconnect UI. Confirm tenant protocol permissions separately from token acquisition.
