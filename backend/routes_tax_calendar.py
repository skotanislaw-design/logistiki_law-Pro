from fastapi import APIRouter, Depends
from db import db
from auth import get_current_user
from tenant import tenant_query
from tax_utils import athens_today, generate_tax_calendar, next_vat_deadline, next_efka_deadline

router = APIRouter(prefix="/tax-calendar", tags=["tax-calendar"])


@router.get("")
async def get_tax_calendar(user: dict = Depends(get_current_user)):
    settings = await db.settings.find_one(tenant_query(user, {}))
    vat_period = settings.get("vat_period", "monthly") if settings else "monthly"
    today = athens_today()
    return {
        "deadlines": generate_tax_calendar(today, vat_period),
        "next_vat_deadline": next_vat_deadline(today, vat_period).isoformat(),
        "next_efka_deadline": next_efka_deadline(today).isoformat(),
    }
