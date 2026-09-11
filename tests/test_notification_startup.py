from __future__ import annotations

import asyncio
import json
import smtplib
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import uvicorn

from backend import main
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.notification_service import EmailConnectionError, NotificationService


class OutboxFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mapping = DeviceMappingService(database_path=Path(self.tmp.name) / "test.db")
        self.notification = NotificationService(self.mapping)

    def seed(self, count, channel="email"):
        with self.mapping.connect() as con:
            con.executemany(
                """insert into notification_outbox
                   (channel, severity, title, text, contact_json, status, original_created_at)
                   values (?, 'red', 'Test', 'Body', ?, 'pending', ?)""",
                [(channel, json.dumps({"email": "test@example.invalid"}),
                  "2026-09-01T00:00:00+00:00")] * count,
            )
            con.commit()


class QueueBackoffTests(OutboxFixture, unittest.TestCase):
    def test_auth_backoff_survives_service_restart_and_preserves_backlog(self):
        self.seed(885)
        provider = Mock()
        provider.send.side_effect = smtplib.SMTPAuthenticationError(535, b"basic authentication is disabled")
        self.notification.providers["email"] = provider
        instant = datetime(2026, 9, 11, tzinfo=timezone.utc)
        with patch("backend.services.notification_service.now", return_value=instant.isoformat()):
            self.notification.process_pending_queue()
            restarted = NotificationService(self.mapping)
            restarted.providers["email"] = provider
            for _ in range(10):
                restarted.process_pending_queue()
        self.assertEqual(provider.send.call_count, 1)
        self.assertEqual(restarted.queue_status()["queue"], {"failed": 1, "pending": 884})
        with patch("backend.services.notification_service.now", return_value=(instant + timedelta(hours=1)).isoformat()):
            restarted.process_pending_queue()
        self.assertEqual(provider.send.call_count, 2)

    def test_real_email_provider_wrapped_auth_error_gets_long_backoff(self):
        self.seed(50)
        error = EmailConnectionError("smtp.example.invalid", 587, "SMTPAuthenticationError")
        self.notification.providers["email"] = Mock(send=Mock(side_effect=error))
        self.notification.process_pending_queue()
        self.notification.process_pending_queue()
        self.notification.providers["email"].send.assert_called_once()

    def test_transient_failure_backs_off_and_other_channel_is_not_starved(self):
        self.seed(885)
        self.seed(1, "telegram")
        email = Mock(send=Mock(side_effect=TimeoutError()))
        telegram = Mock(send=Mock(return_value=None))
        self.notification.providers.update(email=email, telegram=telegram)
        instant = datetime(2026, 9, 11, tzinfo=timezone.utc)
        with patch("backend.services.notification_service.now", return_value=instant.isoformat()):
            self.notification.process_pending_queue()
            self.notification.process_pending_queue()
        email.send.assert_called_once()
        telegram.send.assert_called_once()
        with patch("backend.services.notification_service.now", return_value=(instant + timedelta(seconds=60)).isoformat()):
            self.notification.process_pending_queue()
        self.assertEqual(email.send.call_count, 2)
        with patch("backend.services.notification_service.now", return_value=(instant + timedelta(seconds=119)).isoformat()):
            self.notification.process_pending_queue()
        self.assertEqual(email.send.call_count, 2)

    def test_parallel_workers_are_excluded_without_holding_db_write_lock(self):
        self.seed(2)
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        def send(*args):
            entered.set()
            release.wait(3)
        provider = Mock(send=Mock(side_effect=send))
        self.notification.providers["email"] = provider
        second = NotificationService(self.mapping)
        second.providers["email"] = provider
        thread = threading.Thread(target=self.notification.process_pending_queue, kwargs={"stop_event": stop})
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(second.process_pending_queue()["skipped"], "worker_running")
            self.seed(1)  # SQLite remains writable during the provider call.
            stop.set()
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        provider.send.assert_called_once()
        self.assertEqual(self.notification.queue_status()["queue"], {"pending": 2, "sent": 1})


class StartupTests(OutboxFixture, unittest.IsolatedAsyncioTestCase):
    async def exercise_server(self, count, queue_error=False, success=False):
        self.seed(count)
        entered, release = threading.Event(), threading.Event()
        def deliver(*args):
            entered.set()
            release.wait(5)
            if not success:
                raise smtplib.SMTPAuthenticationError(535, b"basic authentication is disabled")
        self.notification.providers["email"] = Mock(send=Mock(side_effect=deliver))
        if queue_error:
            def fail(**kwargs):
                entered.set()
                raise RuntimeError("queue unavailable")
            self.notification.process_pending_queue = Mock(side_effect=fail)
        services = SimpleNamespace(mapping=Mock(), sentero=Mock(), notification=self.notification,
                                   network=Mock(), mail_assistant=Mock(), telegram_assistant=Mock(), update=Mock())
        async def idle():
            await asyncio.Event().wait()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(16)
            port = sock.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(main.app, log_level="error", lifespan="on"))
            with patch.object(main, "get_services", return_value=services), \
                 patch.object(main, "behavior_snapshot_loop", idle), \
                 patch.object(main, "network_maintenance_loop", idle), \
                 patch.object(main, "mail_assistant_loop", idle), \
                 patch.object(main, "telegram_assistant_loop", idle):
                started = time.monotonic()
                task = asyncio.create_task(server.serve(sockets=[sock]))
                try:
                    async with asyncio.timeout(2):
                        while not server.started:
                            await asyncio.sleep(0.01)
                    self.assertLess(time.monotonic() - started, 2)
                    services.network.ensure_first_boot_setup.assert_not_called()
                    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
                        self.assertEqual((await client.get("/health")).json(), {"status": "ok"})
                        self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                        self.assertEqual((await client.get("/health")).status_code, 200)
                        self.assertFalse(task.done())
                finally:
                    release.set()
                    server.should_exit = True
                    await asyncio.wait_for(task, 4)
        self.assertEqual(self.notification._pending_count(), 0 if success else count)

    async def test_50_slow_auth_failures_do_not_block_uvicorn_startup_or_health(self):
        await self.exercise_server(50)

    async def test_885_notifications_do_not_delay_startup(self):
        await self.exercise_server(885)

    async def test_queue_exception_is_isolated_from_uvicorn(self):
        await self.exercise_server(50, queue_error=True)

    async def test_background_worker_delivers_after_startup(self):
        await self.exercise_server(1, success=True)

    async def test_shutdown_waits_for_current_delivery_and_stops_batch(self):
        self.seed(50)
        entered, release, stop = threading.Event(), threading.Event(), threading.Event()
        def deliver(*args):
            entered.set()
            release.wait(5)
        provider = Mock(send=Mock(side_effect=deliver))
        self.notification.providers["email"] = provider
        with patch.object(main, "get_services", return_value=SimpleNamespace(notification=self.notification)):
            task = asyncio.create_task(main.notification_queue_loop(stop))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 3))
                task.cancel()
                await asyncio.sleep(0.02)
                self.assertTrue(stop.is_set())
                self.assertFalse(task.done())
            finally:
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 3)
        provider.send.assert_called_once()
        self.assertEqual(self.notification.queue_status()["queue"], {"pending": 49, "sent": 1})


if __name__ == "__main__":
    unittest.main()
