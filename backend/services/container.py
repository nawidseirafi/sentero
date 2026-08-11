from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.sensors.service import SenteroSensorService
from backend.services.audit_service import AuditService
from backend.services.auth_service import SenteroAuthService
from backend.services.box_network_service import BoxNetworkService
from backend.services.consent_service import ConsentService
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.export_service import ExportService
from backend.services.notification_service import NotificationService
from backend.services.sensor_manager import SensorManager
from backend.services.service import SenteroService
from backend.services.setup_service import SenteroSetupService
from backend.services.update_service import SenteroUpdateService


@dataclass(frozen=True)
class SenteroServices:
    mapping: DeviceMappingService
    setup: SenteroSetupService
    sentero: SenteroService
    notification: NotificationService
    consent: ConsentService
    auth: SenteroAuthService
    update: SenteroUpdateService
    sensors: SenteroSensorService
    sensor_manager: SensorManager
    box_network: BoxNetworkService
    exports: ExportService
    audit: AuditService


@lru_cache(maxsize=1)
def get_services() -> SenteroServices:
    mapping = DeviceMappingService()
    sentero = SenteroService(mapping)
    sensors = SenteroSensorService(mapping)
    return SenteroServices(
        mapping=mapping,
        setup=SenteroSetupService(mapping),
        sentero=sentero,
        notification=NotificationService(mapping),
        consent=ConsentService(mapping),
        auth=SenteroAuthService(mapping),
        update=SenteroUpdateService(),
        sensors=sensors,
        sensor_manager=SensorManager(mapping),
        box_network=BoxNetworkService(mapping),
        exports=ExportService(mapping, sentero=sentero, sensors=sensors),
        audit=AuditService(mapping),
    )


def reset_services_for_tests() -> None:
    get_services.cache_clear()
