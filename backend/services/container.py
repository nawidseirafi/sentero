from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from backend.services.auth_service import SenteroAuthService
from backend.services.commissioning_service import CommissioningService
from backend.services.device_mapping_service import DeviceMappingService
from backend.services.notification_service import NotificationService
from backend.services.service import SenteroService
from backend.services.setup_service import SenteroSetupService
from backend.services.update_service import SenteroUpdateService


@dataclass(frozen=True)
class SenteroServices:
    mapping: DeviceMappingService
    setup: SenteroSetupService
    sentero: SenteroService
    commissioning: CommissioningService
    notification: NotificationService
    auth: SenteroAuthService
    update: SenteroUpdateService


@lru_cache(maxsize=1)
def get_services() -> SenteroServices:
    mapping = DeviceMappingService()
    return SenteroServices(
        mapping=mapping,
        setup=SenteroSetupService(mapping),
        sentero=SenteroService(mapping),
        commissioning=CommissioningService(mapping=mapping),
        notification=NotificationService(mapping),
        auth=SenteroAuthService(mapping),
        update=SenteroUpdateService(),
    )


def reset_services_for_tests() -> None:
    get_services.cache_clear()
