from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.sensors.service import SenteroSensorService
from backend.services.auth_service import SenteroAuthService
from backend.services.box_network_service import BoxNetworkService
from backend.services.device_mapping_service import DeviceMappingService
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
    auth: SenteroAuthService
    update: SenteroUpdateService
    sensors: SenteroSensorService
    sensor_manager: SensorManager
    box_network: BoxNetworkService


@lru_cache(maxsize=1)
def get_services() -> SenteroServices:
    mapping = DeviceMappingService()
    return SenteroServices(
        mapping=mapping,
        setup=SenteroSetupService(mapping),
        sentero=SenteroService(mapping),
        notification=NotificationService(mapping),
        auth=SenteroAuthService(mapping),
        update=SenteroUpdateService(),
        sensors=SenteroSensorService(mapping),
        sensor_manager=SensorManager(mapping),
        box_network=BoxNetworkService(mapping),
    )


def reset_services_for_tests() -> None:
    get_services.cache_clear()
