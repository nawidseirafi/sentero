from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from backend.logging_config import get_logger
from .device_mapping_service import DeviceMappingService, now

ROOMS = ['living_room', 'kitchen', 'bathroom', 'bedroom', 'hallway', 'entrance']
logger = get_logger(__name__)

class SenteroSetupService:
    def __init__(self, mapping: DeviceMappingService) -> None:
        self.mapping = mapping

    def status(self) -> dict[str, Any]:
        logger.debug("Wizard status requested", extra={"component": "wizard"})
        with self.mapping.connect() as con:
            row = con.execute('select * from setup_state where id = 1').fetchone()
            profile = con.execute('select * from sentero_profile where id = 1').fetchone()
            contacts = con.execute('select * from trusted_contacts where active = 1 order by id').fetchall()
            notifications = con.execute('select * from notification_preferences where id = 1').fetchone()
        profile_data = dict(profile) if profile else None
        contact_data = [dict(contact) for contact in contacts]
        notification_data = dict(notifications) if notifications else None
        status = {
            'current_step': row['current_step'],
            'completed_steps': json.loads(row['completed_steps'] or '[]'),
            'selected_rooms': json.loads(row['selected_rooms_json'] or '[]'),
            'is_complete': bool(row['is_complete']),
            'home': self.mapping.home_status(),
            'has_profile': bool(profile),
            'profile': profile_data,
            'trusted_contacts_count': len(contact_data),
            'trusted_contacts': contact_data,
            'notifications': notification_data,
            'sensor_roles': self.mapping.roles(include_state=True),
            'updated_at': row['updated_at'],
        }
        logger.debug(
            "Wizard status built",
            extra={
                "component": "wizard",
                "current_step": status["current_step"],
                "is_complete": status["is_complete"],
                "trusted_contacts_count": status["trusted_contacts_count"],
                "sensor_roles_count": len(status["sensor_roles"]),
            },
        )
        return status

    def set_step(self, current_step: str, completed_step: str | None = None, complete: bool | None = None) -> dict[str, Any]:
        logger.debug(
            "Wizard step update start",
            extra={"component": "wizard", "current_step": current_step, "completed_step": completed_step, "complete": complete},
        )
        with self.mapping.connect() as con:
            row = con.execute('select completed_steps from setup_state where id = 1').fetchone()
            completed = set(json.loads(row['completed_steps'] or '[]')) if row else set()
            if completed_step:
                completed.add(completed_step)
            con.execute('update setup_state set current_step = ?, completed_steps = ?, is_complete = coalesce(?, is_complete), updated_at = ? where id = 1', (current_step, json.dumps(sorted(completed)), None if complete is None else int(complete), now()))
            con.commit()
        if complete:
            logger.info("Wizard completed", extra={"component": "wizard"})
        else:
            logger.debug("Wizard step updated", extra={"component": "wizard", "current_step": current_step, "completed_steps": sorted(completed)})
        return self.status()

    def profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Wizard profile save start", extra={"component": "wizard", "fields": sorted(payload.keys())})
        timestamp = now()
        notes_provided = payload.get('notes') is not None
        birth_year = normalize_birth_year(payload.get('birth_year'))
        calculated_age = calculate_age(birth_year) if birth_year else None
        with self.mapping.connect() as con:
            existing = con.execute('select notes from sentero_profile where id = 1').fetchone()
            notes = payload.get('notes') if notes_provided else (existing['notes'] if existing else None)
            con.execute('''insert into sentero_profile (id, name, birth_year, age, notes, created_at, updated_at) values (1, ?, ?, ?, ?, ?, ?) on conflict(id) do update set name = excluded.name, birth_year = excluded.birth_year, age = excluded.age, notes = excluded.notes, updated_at = excluded.updated_at''', (payload.get('name'), birth_year, calculated_age, notes, timestamp, timestamp))
            con.commit()
        logger.debug("Wizard profile saved", extra={"component": "wizard", "birth_year_present": bool(birth_year)})
        return self.set_step('prepare_home', 'profile')

    def rooms(self, rooms: list[str]) -> dict[str, Any]:
        logger.debug("Wizard rooms save start", extra={"component": "wizard", "room_count": len(rooms)})
        clean_rooms = []
        for room in rooms:
            value = str(room or '').strip()
            if value and value not in clean_rooms:
                clean_rooms.append(value[:80])
        with self.mapping.connect() as con:
            con.execute('update setup_state set selected_rooms_json = ?, updated_at = ? where id = 1', (json.dumps(clean_rooms), now()))
            con.commit()
        logger.debug("Wizard rooms saved", extra={"component": "wizard", "rooms": clean_rooms})
        return self.set_step('sensors', 'rooms')

    def sensors(self) -> dict[str, Any]:
        logger.info("Wizard sensors step completed", extra={"component": "wizard"})
        return self.set_step('contacts', 'sensors')

    def contact(self, payload: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Wizard contact save start", extra={"component": "wizard", "fields": sorted(payload.keys())})
        timestamp = now()
        name = str(payload.get('name') or '').strip()
        email = normalize_email(payload.get('email'))
        channels = normalize_channels(payload.get('preferred_channels'), email=email)
        phone = normalize_text(payload.get('phone'))
        telegram_chat_id = normalize_text(payload.get('telegram_chat_id'))
        whatsapp_phone_number = normalize_text(payload.get('whatsapp_phone_number') or payload.get('phone'))
        primary_contact = int(bool(payload.get('primary_contact', False)))
        validate_contact_channels(channels, email, telegram_chat_id, whatsapp_phone_number)
        if not name:
            raise ValueError('name is required')
        if 'email' in channels and not is_valid_email(email):
            raise ValueError('valid email is required')
        with self.mapping.connect() as con:
            if primary_contact:
                con.execute('update trusted_contacts set primary_contact = 0 where active = 1')
            existing_primary = con.execute('select id from trusted_contacts where active = 1 and primary_contact = 1').fetchone()
            if not existing_primary:
                primary_contact = 1
            existing = con.execute('select id from trusted_contacts where lower(email) = ? and active = 1', (email,)).fetchone() if email else None
            if existing:
                con.execute(
                    '''update trusted_contacts
                       set name = ?, relationship = ?, email = ?, phone = ?, telegram_chat_id = ?,
                           whatsapp_phone_number = ?, preferred_channels = ?, notification_enabled = ?, primary_contact = ?, updated_at = ?
                       where id = ?''',
                    (name, payload.get('relationship'), email, phone, telegram_chat_id, whatsapp_phone_number, json.dumps(channels), int(bool(payload.get('notification_enabled', True))), primary_contact, timestamp, existing['id']),
                )
            else:
                con.execute(
                    '''insert into trusted_contacts
                       (name, relationship, email, phone, telegram_chat_id, whatsapp_phone_number, preferred_channels, notification_enabled, primary_contact, active, created_at, updated_at)
                       values (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)''',
                    (name, payload.get('relationship'), email, phone, telegram_chat_id, whatsapp_phone_number, json.dumps(channels), int(bool(payload.get('notification_enabled', True))), primary_contact, timestamp, timestamp),
                )
            con.commit()
        logger.info("Trusted contact saved", extra={"component": "wizard", "channels": channels, "primary_contact": bool(primary_contact)})
        return self.set_step('notifications', 'contacts')

    def update_contact(self, contact_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Wizard contact update start", extra={"component": "wizard", "contact_id": contact_id, "fields": sorted(payload.keys())})
        name = str(payload.get('name') or '').strip()
        email = normalize_email(payload.get('email'))
        channels = normalize_channels(payload.get('preferred_channels'), email=email)
        phone = normalize_text(payload.get('phone'))
        telegram_chat_id = normalize_text(payload.get('telegram_chat_id'))
        whatsapp_phone_number = normalize_text(payload.get('whatsapp_phone_number') or payload.get('phone'))
        primary_contact = int(bool(payload.get('primary_contact', False)))
        validate_contact_channels(channels, email, telegram_chat_id, whatsapp_phone_number)
        if not name:
            raise ValueError('name is required')
        if 'email' in channels and not is_valid_email(email):
            raise ValueError('valid email is required')
        with self.mapping.connect() as con:
            row = con.execute('select id from trusted_contacts where id = ? and active = 1', (contact_id,)).fetchone()
            if not row:
                raise ValueError('contact not found')
            if email:
                duplicate = con.execute('select id from trusted_contacts where lower(email) = ? and active = 1 and id != ?', (email, contact_id)).fetchone()
                if duplicate:
                    raise ValueError('email already exists')
            if primary_contact:
                con.execute('update trusted_contacts set primary_contact = 0 where active = 1')
            con.execute(
                '''update trusted_contacts
                   set name = ?, relationship = ?, email = ?, phone = ?, telegram_chat_id = ?,
                       whatsapp_phone_number = ?, preferred_channels = ?, notification_enabled = ?, primary_contact = ?, updated_at = ?
                   where id = ?''',
                (name, payload.get('relationship'), email, phone, telegram_chat_id, whatsapp_phone_number, json.dumps(channels), int(bool(payload.get('notification_enabled', True))), primary_contact, now(), contact_id),
            )
            con.commit()
        logger.info("Trusted contact updated", extra={"component": "wizard", "contact_id": contact_id})
        return self.status()

    def delete_contact(self, contact_id: int) -> dict[str, Any]:
        logger.debug("Wizard contact delete start", extra={"component": "wizard", "contact_id": contact_id})
        with self.mapping.connect() as con:
            con.execute('update trusted_contacts set active = 0, updated_at = ? where id = ?', (now(), contact_id))
            con.commit()
        logger.info("Trusted contact deleted", extra={"component": "wizard", "contact_id": contact_id})
        return self.status()

    def notifications(self, payload: dict[str, Any]) -> dict[str, Any]:
        logger.debug("Wizard notifications save start", extra={"component": "wizard", "fields": sorted(payload.keys())})
        with self.mapping.connect() as con:
            con.execute('''insert into notification_preferences (id, anomalies, critical, daily_summary, updated_at) values (1, ?, ?, ?, ?) on conflict(id) do update set anomalies = excluded.anomalies, critical = excluded.critical, daily_summary = excluded.daily_summary, updated_at = excluded.updated_at''', (int(bool(payload.get('anomalies', True))), int(bool(payload.get('critical', True))), int(bool(payload.get('daily_summary', False))), now()))
            con.commit()
        logger.debug("Wizard notifications saved", extra={"component": "wizard"})
        return self.set_step('complete', 'notifications')

    def complete(self) -> dict[str, Any]:
        logger.debug("Wizard completion validation start", extra={"component": "wizard"})
        with self.mapping.connect() as con:
            contact = con.execute("select id from trusted_contacts where active = 1 and email is not null and trim(email) != '' limit 1").fetchone()
            email_channel = con.execute("select * from notification_channel_settings where channel = 'email'").fetchone()
        if not contact:
            raise ValueError('trusted contact with email required')
        if not email_channel:
            raise ValueError('email channel required')
        config = json.loads(email_channel['config_json'] or '{}')
        if not bool(email_channel['enabled']) or not config.get('smtp_host'):
            raise ValueError('email channel required')
        logger.info("Wizard completion validated", extra={"component": "wizard"})
        return self.set_step('complete', 'complete', complete=True)


def normalize_email(value: Any) -> str:
    return str(value or '').strip().lower()


def normalize_birth_year(value: Any) -> int | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        year = int(text)
    except ValueError as exc:
        raise ValueError('valid birth year is required') from exc
    current_year = datetime.now().year
    if year < 1900 or year > current_year:
        raise ValueError('valid birth year is required')
    return year


def calculate_age(birth_year: int) -> int:
    return max(0, datetime.now().year - birth_year)


def normalize_text(value: Any) -> str:
    return str(value or '').strip()


def is_valid_email(value: str) -> bool:
    return '@' in value and '.' in value.rsplit('@', 1)[-1]


def normalize_channels(value: Any, email: str = '') -> list[str]:
    explicit_value = value is not None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value]
    source = value if isinstance(value, list) else (['email'] if email else [])
    channels = []
    for item in source:
        channel = str(item or '').strip().lower()
        if channel in {'email', 'telegram', 'whatsapp'} and channel not in channels:
            channels.append(channel)
    if not channels and not explicit_value and email:
        channels = ['email']
    if channels and 'email' not in channels and email:
        channels.insert(0, 'email')
    return channels


def validate_contact_channels(channels: list[str], email: str, telegram_chat_id: str, whatsapp_phone_number: str) -> None:
    if 'email' in channels and not email:
        raise ValueError('email is required')
    if 'telegram' in channels and not telegram_chat_id:
        raise ValueError('telegram chat id is required')
    if 'whatsapp' in channels and not whatsapp_phone_number:
        raise ValueError('whatsapp phone number is required')
