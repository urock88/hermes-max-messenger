"""TLS trust for MAX and Sber hosts.

``platform-api2.max.ru`` (and Sber's SaluteSpeech hosts) present certificates issued by the Russian
Trusted Root CA of the Ministry of Digital Development. That root is in no default trust store, so
the context is certifi's roots plus that one root, shipped in ``assets/certs``. Verification is never
switched off.
"""

from __future__ import annotations

import logging
import ssl
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT_CA_PATH = Path(__file__).resolve().parent.parent / "assets" / "certs" / "russian_trusted_root_ca.pem"


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """certifi (or the system store) plus the Russian Trusted Root CA."""
    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - certifi ships with httpx
        context = ssl.create_default_context()
    if ROOT_CA_PATH.is_file():
        context.load_verify_locations(cafile=str(ROOT_CA_PATH))
    else:  # pragma: no cover - only a broken checkout gets here
        logger.warning("MAX: %s is missing; platform-api2.max.ru will fail TLS verification", ROOT_CA_PATH)
    return context
