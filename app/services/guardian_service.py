"""Guardian Service — Family member monitoring and configuration.

Allows a guardian (e.g., son/daughter) to set up SAHAY for an elderly
parent remotely. Guardians configure trusted portals, spending limits,
stored credentials (encrypted), and receive real-time notifications.
"""

import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

# Derive encryption key from environment or generate one
_ENCRYPTION_KEY = os.environ.get("SAHAY_ENCRYPTION_KEY", "")
if not _ENCRYPTION_KEY:
    _ENCRYPTION_KEY = Fernet.generate_key().decode()
    logger.info("Generated new encryption key (set SAHAY_ENCRYPTION_KEY to persist)")

_fernet = Fernet(
    _ENCRYPTION_KEY if isinstance(_ENCRYPTION_KEY, bytes)
    else _ENCRYPTION_KEY.encode() if len(_ENCRYPTION_KEY) == 44
    else base64.urlsafe_b64encode(hashlib.sha256(_ENCRYPTION_KEY.encode()).digest())
)


def _encrypt(plaintext: str) -> str:
    """Encrypt a string value."""
    return _fernet.encrypt(plaintext.encode()).decode()


def _decrypt(ciphertext: str) -> str:
    """Decrypt an encrypted string value."""
    return _fernet.decrypt(ciphertext.encode()).decode()


@dataclass
class StoredCredential:
    """An encrypted credential for a specific service."""
    service_domain: str
    username_encrypted: str
    password_encrypted: str
    label: str = ""
    added_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    def decrypt_username(self) -> str:
        return _decrypt(self.username_encrypted)

    def decrypt_password(self) -> str:
        return _decrypt(self.password_encrypted)


@dataclass
class GuardianConfig:
    """Guardian configuration for a user."""
    guardian_id: str
    user_id: str
    guardian_name: str = ""
    guardian_phone: str = ""
    user_name: str = ""

    # Security settings
    allowed_domains: list[str] = field(default_factory=lambda: [
        "umang.gov.in", "irctc.co.in", "digilocker.gov.in",
        "parivahan.gov.in", "passportindia.gov.in",
        "onlinesbi.sbi", "services.india.gov.in",
        "google.com", "bing.com",
    ])
    spending_cap_inr: float = 5000.0
    require_confirmation_above: float = 500.0

    # Stored credentials (encrypted)
    credentials: list[StoredCredential] = field(default_factory=list)

    # Notification settings
    notify_on_task_complete: bool = True
    notify_on_payment: bool = True
    notify_on_login: bool = True
    notification_endpoint: str = ""  # webhook URL

    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["credentials"] = [c.to_dict() for c in self.credentials]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "GuardianConfig":
        creds = [StoredCredential(**c) for c in data.pop("credentials", [])]
        config = cls(**{k: v for k, v in data.items()
                        if k in cls.__dataclass_fields__})
        config.credentials = creds
        return config


@dataclass
class TaskNotification:
    """A notification sent to the guardian."""
    notification_id: str
    guardian_id: str
    user_id: str
    task_description: str
    outcome: str
    steps_count: int
    timestamp: float
    notification_type: str = "task_complete"  # task_complete, payment, login, alert
    amount_inr: float = 0.0
    domain: str = ""
    journal_url: str = ""
    read: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class GuardianService:
    """Manages guardian configurations and notifications.

    In-memory store with Firestore persistence when available.
    """

    def __init__(self) -> None:
        self._configs: dict[str, GuardianConfig] = {}  # user_id -> config
        self._notifications: dict[str, list[TaskNotification]] = {}  # guardian_id -> list
        self._shareable_journals: dict[str, dict] = {}  # share_token -> journal data

    async def create_guardian(
        self,
        guardian_name: str,
        guardian_phone: str,
        user_name: str,
        allowed_domains: Optional[list[str]] = None,
        spending_cap: float = 5000.0,
    ) -> GuardianConfig:
        """Set up a new guardian-user relationship."""
        guardian_id = f"guardian-{uuid.uuid4().hex[:8]}"
        user_id = f"user-{uuid.uuid4().hex[:8]}"

        config = GuardianConfig(
            guardian_id=guardian_id,
            user_id=user_id,
            guardian_name=guardian_name,
            guardian_phone=guardian_phone,
            user_name=user_name,
            spending_cap_inr=spending_cap,
        )

        if allowed_domains:
            config.allowed_domains = allowed_domains

        self._configs[user_id] = config
        logger.info(
            "Guardian created: %s for user %s (%s)",
            guardian_name, user_name, user_id,
        )
        return config

    async def get_config(self, user_id: str) -> Optional[GuardianConfig]:
        """Get guardian config for a user."""
        return self._configs.get(user_id)

    async def get_config_by_guardian(self, guardian_id: str) -> Optional[GuardianConfig]:
        """Get config by guardian ID."""
        for config in self._configs.values():
            if config.guardian_id == guardian_id:
                return config
        return None

    async def update_config(self, user_id: str, updates: dict) -> Optional[GuardianConfig]:
        """Update guardian configuration."""
        config = self._configs.get(user_id)
        if not config:
            return None

        for key, value in updates.items():
            if hasattr(config, key) and key not in ("guardian_id", "user_id", "created_at"):
                setattr(config, key, value)

        config.updated_at = time.time()
        return config

    async def add_credential(
        self,
        user_id: str,
        service_domain: str,
        username: str,
        password: str,
        label: str = "",
    ) -> bool:
        """Store an encrypted credential for a service."""
        config = self._configs.get(user_id)
        if not config:
            return False

        cred = StoredCredential(
            service_domain=service_domain,
            username_encrypted=_encrypt(username),
            password_encrypted=_encrypt(password),
            label=label or service_domain,
            added_at=time.time(),
        )
        # Replace existing for same domain
        config.credentials = [
            c for c in config.credentials if c.service_domain != service_domain
        ]
        config.credentials.append(cred)
        logger.info("Credential stored for %s (user %s)", service_domain, user_id)
        return True

    async def get_credential(
        self, user_id: str, domain: str
    ) -> Optional[tuple[str, str]]:
        """Retrieve decrypted credentials for a domain."""
        config = self._configs.get(user_id)
        if not config:
            return None

        for cred in config.credentials:
            if cred.service_domain in domain or domain in cred.service_domain:
                return (cred.decrypt_username(), cred.decrypt_password())
        return None

    def is_domain_allowed(self, user_id: str, url: str) -> bool:
        """Check if a URL's domain is in the allowed list."""
        config = self._configs.get(user_id)
        if not config:
            return True  # No guardian = no restrictions

        from urllib.parse import urlparse
        try:
            domain = urlparse(url).netloc.lower()
        except Exception:
            return False

        for allowed in config.allowed_domains:
            if allowed.lower() in domain or domain in allowed.lower():
                return True
        return False

    def check_spending(self, user_id: str, amount: float) -> dict:
        """Check if a payment amount is within limits."""
        config = self._configs.get(user_id)
        if not config:
            return {"allowed": True, "reason": "no_guardian"}

        if amount > config.spending_cap_inr:
            return {
                "allowed": False,
                "reason": f"Amount ₹{amount:.0f} exceeds spending cap ₹{config.spending_cap_inr:.0f}",
                "cap": config.spending_cap_inr,
            }

        needs_confirm = amount > config.require_confirmation_above
        return {
            "allowed": True,
            "needs_confirmation": needs_confirm,
            "reason": f"Above ₹{config.require_confirmation_above:.0f} confirmation threshold" if needs_confirm else "within_limits",
        }

    async def notify_guardian(
        self,
        user_id: str,
        task_description: str,
        outcome: str,
        steps_count: int = 0,
        notification_type: str = "task_complete",
        amount_inr: float = 0.0,
        domain: str = "",
        journal_url: str = "",
    ) -> Optional[TaskNotification]:
        """Send a notification to the guardian."""
        config = self._configs.get(user_id)
        if not config:
            return None

        # Check notification preferences
        if notification_type == "task_complete" and not config.notify_on_task_complete:
            return None
        if notification_type == "payment" and not config.notify_on_payment:
            return None
        if notification_type == "login" and not config.notify_on_login:
            return None

        notification = TaskNotification(
            notification_id=f"notif-{uuid.uuid4().hex[:8]}",
            guardian_id=config.guardian_id,
            user_id=user_id,
            task_description=task_description,
            outcome=outcome,
            steps_count=steps_count,
            timestamp=time.time(),
            notification_type=notification_type,
            amount_inr=amount_inr,
            domain=domain,
            journal_url=journal_url,
        )

        if config.guardian_id not in self._notifications:
            self._notifications[config.guardian_id] = []
        self._notifications[config.guardian_id].append(notification)

        # TODO: Send webhook if configured
        if config.notification_endpoint:
            logger.info(
                "Would send webhook to %s: %s",
                config.notification_endpoint, notification_type,
            )

        logger.info(
            "Guardian notified [%s]: %s for user %s",
            notification_type, task_description[:60], user_id,
        )
        return notification

    async def get_notifications(
        self, guardian_id: str, limit: int = 20
    ) -> list[TaskNotification]:
        """Get recent notifications for a guardian."""
        notifs = self._notifications.get(guardian_id, [])
        return sorted(notifs, key=lambda n: n.timestamp, reverse=True)[:limit]

    async def create_shareable_journal(
        self, task_id: str, journal_data: dict
    ) -> str:
        """Create a shareable URL token for a task journal."""
        token = uuid.uuid4().hex[:12]
        self._shareable_journals[token] = {
            "task_id": task_id,
            "journal": journal_data,
            "created_at": time.time(),
        }
        return token

    async def get_shared_journal(self, token: str) -> Optional[dict]:
        """Retrieve a shared journal by token."""
        return self._shareable_journals.get(token)
