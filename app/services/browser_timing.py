from collections import OrderedDict
from threading import Lock
from time import time
from uuid import UUID

from jose import JWTError, jwt
from structlog import get_logger

from app.schemas.browser_timing import BrowserTiming

logger = get_logger()
RECEIPT_LIFETIME_SECONDS = 300
MAX_RECENT_RECEIPTS = 10000


class InvalidTimingReceipt(ValueError):
    pass


def create_timing_receipt(request_id: str, operation: str, secret_key: str) -> str:
    return jwt.encode(
        {
            "sub": request_id,
            "operation": operation,
            "aud": "browser-response-timing",
            "exp": int(time()) + RECEIPT_LIFETIME_SECONDS,
        },
        secret_key,
        algorithm="HS256",
    )


class BrowserTimingService:
    def __init__(self):
        self._recent: OrderedDict[str, int] = OrderedDict()
        self._lock = Lock()

    def record(self, timing: BrowserTiming, receipt: str, secret_key: str) -> None:
        try:
            claims = jwt.decode(
                receipt,
                secret_key,
                algorithms=["HS256"],
                audience="browser-response-timing",
                options={"require_exp": True, "require_sub": True, "require_aud": True},
            )
            request_id = str(UUID(claims["sub"]))
            expires_at = int(claims["exp"])
            if claims.get("operation") != timing.operation:
                raise ValueError("Operation mismatch")
        except (JWTError, ValueError, TypeError, KeyError) as exc:
            raise InvalidTimingReceipt("Invalid or expired timing receipt") from exc

        now = time()
        with self._lock:
            for previous_id, expiry in list(self._recent.items()):
                if expiry <= now:
                    del self._recent[previous_id]
            # Best-effort per-instance deduplication; these client measurements
            # are advisory diagnostics, never a billing or availability source.
            if request_id in self._recent or len(self._recent) >= MAX_RECENT_RECEIPTS:
                return
            self._recent[request_id] = expires_at

        logger.info(
            "Browser response timing",
            response_request_id=request_id,
            traffic_class="telemetry",
            **timing.model_dump(),
        )


browser_timing_service = BrowserTimingService()
