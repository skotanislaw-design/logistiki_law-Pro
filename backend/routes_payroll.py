from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bson import ObjectId
from db import db
from models import PayrollEmployee, PayrollRun
from auth import get_current_user
from tenant import tenant_query, firm_id_for
from tax_utils import afm_error, calculate_payroll, is_valid_afm

router = APIRouter(prefix="/payroll", tags=["payroll"])


def _validate_employee_afm(payload: PayrollEmployee) -> None:
    """Έλεγχος στη διαδρομή, όχι στο μοντέλο (όπως στους πελάτες): το μοντέλο διαβάζει και υπάρχουσες εγγραφές, και
    ένας παλιός εργαζόμενος με λανθασμένο ΑΦΜ θα γινόταν αόρατος στη λίστα αντί να διορθωθεί. Το κενό επιτρέπεται."""
    afm = (payload.afm or "").strip()
    payload.afm = afm
    if afm and not is_valid_afm(afm):
        raise HTTPException(status_code=400, detail=afm_error(afm, "ΑΦΜ εργαζομένου"))


@router.get("/employees")
async def list_employees(user: dict = Depends(get_current_user)):
    docs = await db.payroll_employees.find(tenant_query(user)).sort("name", 1).to_list(500)
    return [PayrollEmployee.from_mongo(d) for d in docs]


@router.post("/employees")
async def create_employee(payload: PayrollEmployee, user: dict = Depends(get_current_user)):
    _validate_employee_afm(payload)
    payload.id = None
    payload.firm_id = firm_id_for(user)
    result = await db.payroll_employees.insert_one(payload.to_mongo())
    doc = await db.payroll_employees.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return PayrollEmployee.from_mongo(doc)


@router.put("/employees/{employee_id}")
async def update_employee(employee_id: str, payload: PayrollEmployee, user: dict = Depends(get_current_user)):
    _validate_employee_afm(payload)
    payload.id = employee_id
    data = payload.to_mongo()
    data.pop("_id", None)
    data.pop("firm_id", None)
    result = await db.payroll_employees.update_one(tenant_query(user, {"_id": ObjectId(employee_id)}), {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ο εργαζόμενος δεν βρέθηκε")
    doc = await db.payroll_employees.find_one(tenant_query(user, {"_id": ObjectId(employee_id)}))
    return PayrollEmployee.from_mongo(doc)


@router.delete("/employees/{employee_id}")
async def delete_employee(employee_id: str, user: dict = Depends(get_current_user)):
    result = await db.payroll_employees.delete_one(tenant_query(user, {"_id": ObjectId(employee_id)}))
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Ο εργαζόμενος δεν βρέθηκε")
    return {"message": "Ο εργαζόμενος διαγράφηκε"}


class RunPayrollRequest(BaseModel):
    employee_id: str
    period: str


@router.post("/run")
async def run_payroll(payload: RunPayrollRequest, user: dict = Depends(get_current_user)):
    employee = await db.payroll_employees.find_one(tenant_query(user, {"_id": ObjectId(payload.employee_id)}))
    if not employee:
        raise HTTPException(status_code=404, detail="Ο εργαζόμενος δεν βρέθηκε")
    calc = calculate_payroll(employee["gross_salary"], employee.get("dependents", 0))
    run = PayrollRun(
        employee_id=payload.employee_id,
        firm_id=firm_id_for(user),
        employee_name=employee["name"],
        period=payload.period,
        **calc,
    )
    existing = await db.payroll_runs.find_one(tenant_query(user, {"employee_id": payload.employee_id, "period": payload.period}))
    if existing:
        raise HTTPException(status_code=400, detail="Η μισθοδοσία για αυτή την περίοδο έχει ήδη υπολογιστεί")
    result = await db.payroll_runs.insert_one(run.to_mongo())
    doc = await db.payroll_runs.find_one(tenant_query(user, {"_id": result.inserted_id}))
    return PayrollRun.from_mongo(doc)


@router.get("/runs")
async def list_runs(user: dict = Depends(get_current_user)):
    docs = await db.payroll_runs.find(tenant_query(user)).sort("created_at", -1).to_list(1000)
    return [PayrollRun.from_mongo(d) for d in docs]
