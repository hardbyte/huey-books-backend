from collections import OrderedDict
from datetime import datetime, timezone
from threading import Lock
from time import monotonic, time
from uuid import UUID, uuid4

from jose import JWTError, jwt
from opentelemetry import metrics
from structlog import get_logger

from app.schemas.browser_observations import (
    BrowserObservation,
    CoverKind,
    TimingObservation,
)

logger = get_logger()
MAX_RECENT_RECEIPTS = 10000
MAX_REQUESTS_PER_SECOND = 20
SAMPLING_PROBABILITY = 0.1
AUDIENCE = "browser-observation"
meter = metrics.get_meter("huey.browser", "1")
cover_presentations = meter.create_counter(
    "huey.book.cover.presentations", unit="{presentation}"
)
cover_disclosures = meter.create_counter(
    "huey.book.cover.disclosure.views", unit="{view}"
)


class InvalidObservationReceipt(ValueError):
    pass


def _create_receipt(subject: str, claims: dict, secret_key: str) -> str:
    return jwt.encode(
        {"sub": subject, "aud": AUDIENCE, "exp": int(time()) + 900, **claims},
        secret_key,
        algorithm="HS256",
    )


def create_timing_receipt(request_id: str, operation: str, secret_key: str) -> str:
    return _create_receipt(
        request_id, {"purpose": "timing", "operation": operation}, secret_key
    )


def create_cover_receipt(cover_kind: CoverKind, secret_key: str) -> str:
    return _create_receipt(
        str(uuid4()), {"purpose": "cover", "cover_kind": cover_kind}, secret_key
    )


class BrowserObservationService:
    def __init__(self):
        self._recent: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._lock = Lock()
        self._window_started = monotonic()
        self._requests = 0

    def record(
        self, observation: BrowserObservation, receipt: str, secret_key: str
    ) -> None:
        with self._lock:
            now = monotonic()
            if now - self._window_started >= 1:
                self._window_started = now
                self._requests = 0
            if self._requests >= MAX_REQUESTS_PER_SECOND:
                return
            self._requests += 1
        try:
            claims = jwt.decode(
                receipt,
                secret_key,
                algorithms=["HS256"],
                audience=AUDIENCE,
                options={"require_exp": True, "require_sub": True, "require_aud": True},
            )
            subject = str(UUID(claims["sub"]))
            expires_at = int(claims["exp"])
            if isinstance(observation, TimingObservation):
                if (
                    claims.get("purpose") != "timing"
                    or claims.get("operation") != observation.operation
                ):
                    raise ValueError("Timing scope mismatch")
            else:
                if claims.get("purpose") != "cover" or claims.get("cover_kind") not in {
                    "exact",
                    "alternative",
                    "placeholder",
                }:
                    raise ValueError("Cover scope mismatch")
                if observation.cover_kind not in {claims["cover_kind"], "placeholder"}:
                    raise ValueError("Cover kind mismatch")
                if (
                    observation.event == "cover_disclosure_viewed"
                    and observation.cover_kind != "alternative"
                ):
                    raise ValueError("Only alternative covers have disclosures")
        except (JWTError, ValueError, TypeError, KeyError) as exc:
            raise InvalidObservationReceipt(
                "Invalid or expired observation receipt"
            ) from exc

        with self._lock:
            now = time()
            for key, expiry in list(self._recent.items()):
                if expiry <= now:
                    del self._recent[key]
            key = (subject, observation.event)
            # Approximate across replicas/restarts; observations are not billing facts.
            if key in self._recent or len(self._recent) >= MAX_RECENT_RECEIPTS:
                return
            self._recent[key] = expires_at

        common = {
            "traffic_class": "telemetry",
            "schema_version": 1,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "sampling_probability": SAMPLING_PROBABILITY,
        }
        if isinstance(observation, TimingObservation):
            logger.info(
                "Browser response timing",
                response_request_id=subject,
                source_surface="student_chat",
                **common,
                **observation.model_dump(exclude={"event", "schema_version"}),
            )
        else:
            disclosure = observation.event == "cover_disclosure_viewed"
            attributes = {
                "huey.book.cover.kind": observation.cover_kind,
                "huey.ui.surface": "book_list",
                "huey.event.schema_version": 1,
            }
            (cover_disclosures if disclosure else cover_presentations).add(
                1, attributes
            )
            logger.info(
                "huey.book.cover.disclosure_viewed"
                if disclosure
                else "huey.book.cover.presented",
                observation_id=subject,
                source_surface="book_list",
                cover_kind=observation.cover_kind,
                **common,
            )


browser_observation_service = BrowserObservationService()
