"""Read-only header view of existing operational alerts."""

from fastapi import APIRouter, Depends, Request

from app.dependencies import require_current_user_api
from app.operational_alerts import load_operational_alerts


router = APIRouter(prefix="/api/operational-alerts", tags=["operational-alerts"])


@router.get("")
def operational_alert_status(request: Request, user=Depends(require_current_user_api)):
    """Return one owner's full count and a five-item presentation preview."""
    alerts = load_operational_alerts(request, user)
    return {
        "count": len(alerts),
        "alerts": [
            {"severity": alert.severity, "title": alert.title, "message": alert.message}
            for alert in alerts[:5]
        ],
    }
