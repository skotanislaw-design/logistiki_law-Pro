"""Tenant identity and query helpers."""

import secrets

LEGACY_FIRM_ID = "legacy-default-firm"


def new_firm_id() -> str:
    return f"firm-{secrets.token_hex(16)}"


def firm_id_for(user: dict) -> str:
    return user.get("firm_id") or LEGACY_FIRM_ID


def tenant_query(user: dict, query: dict | None = None) -> dict:
    # Always let the authenticated tenant win over caller-supplied criteria.
    return {**(query or {}), "firm_id": firm_id_for(user)}
