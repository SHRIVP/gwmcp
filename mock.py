"""In-memory Policy System stand-in.

Search data keeps the JSON:API-ish shape the search tools already format.
Policy details (vehicles, coverages, premium) and the policy-change workflow
(start → edit → quote → bind / withdraw) are stateful per client instance, so
edits made through Claude persist for the life of the server process.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone

from client import EverywireError
from views import EDITABLE_STATUSES, build_policy_view

ACCOUNTS = [
    {
        "id": "account:1001",
        "attributes": {
            "accountNumber": "ACC-10001",
            "firstName": "Jane",
            "lastName": "Smith",
            "emailAddress": "jane.smith@example.com",
        },
    },
    {
        "id": "account:1002",
        "attributes": {
            "accountNumber": "ACC-10002",
            "firstName": "Robert",
            "lastName": "Johnson",
            "emailAddress": "robert.johnson@example.com",
        },
    },
    {
        "id": "account:1003",
        "attributes": {
            "accountNumber": "ACC-10003",
            "companyName": "Acme Corporation",
            "emailAddress": "insurance@acmecorp.com",
        },
    },
    {
        "id": "account:1004",
        "attributes": {
            "accountNumber": "ACC-10004",
            "companyName": "Smith & Partners LLC",
            "emailAddress": "admin@smithpartners.com",
        },
    },
    {
        "id": "account:1005",
        "attributes": {
            "accountNumber": "ACC-10005",
            "firstName": "Emily",
            "lastName": "Smith",
            "emailAddress": "emily.smith@example.com",
        },
    },
]

POLICIES = [
    {
        "id": "policy:2001",
        "attributes": {
            "policyNumber": "PA-200001-01",
            "status": "inForce",
            "effectiveDate": "2026-06-01",
            "expirationDate": "2027-06-01",
            "product": {"name": "Personal Auto", "code": "PersonalAuto"},
            "account": {"accountNumber": "ACC-10001"},
        },
    },
    {
        "id": "policy:2002",
        "attributes": {
            "policyNumber": "HO-200002-01",
            "status": "inForce",
            "effectiveDate": "2026-03-15",
            "expirationDate": "2027-03-15",
            "product": {"name": "Homeowners", "code": "HomeOwners"},
            "account": {"accountNumber": "ACC-10001"},
        },
    },
    {
        "id": "policy:2003",
        "attributes": {
            "policyNumber": "PA-200003-01",
            "status": "canceled",
            "effectiveDate": "2025-01-01",
            "expirationDate": "2026-01-01",
            "product": {"name": "Personal Auto", "code": "PersonalAuto"},
            "account": {"accountNumber": "ACC-10002"},
        },
    },
    {
        "id": "policy:2004",
        "attributes": {
            "policyNumber": "CP-200004-01",
            "status": "inForce",
            "effectiveDate": "2026-07-01",
            "expirationDate": "2027-07-01",
            "product": {"name": "Commercial Package", "code": "CommercialPackage"},
            "account": {"accountNumber": "ACC-10003"},
        },
    },
    {
        "id": "policy:2005",
        "attributes": {
            "policyNumber": "HO-200005-01",
            "status": "expired",
            "effectiveDate": "2024-09-01",
            "expirationDate": "2025-09-01",
            "product": {"name": "Homeowners", "code": "HomeOwners"},
            "account": {"accountNumber": "ACC-10005"},
        },
    },
    {
        "id": "policy:2006",
        "attributes": {
            "policyNumber": "PA-200006-01",
            "status": "scheduled",
            "effectiveDate": "2027-01-01",
            "expirationDate": "2028-01-01",
            "product": {"name": "Personal Auto", "code": "PersonalAuto"},
            "account": {"accountNumber": "ACC-10004"},
        },
    },
]

JOBS = [
    {
        "id": "job:3001",
        "attributes": {
            "jobNumber": "SUB-300001",
            "jobType": "Submission",
            "status": "Bound",
            "policy": {"policyNumber": "PA-200001-01"},
            "account": {"accountNumber": "ACC-10001"},
            "createTime": "2026-05-15T10:30:00Z",
        },
    },
    {
        "id": "job:3002",
        "attributes": {
            "jobNumber": "RNW-300002",
            "jobType": "Renewal",
            "status": "Quoted",
            "policy": {"policyNumber": "HO-200002-01"},
            "account": {"accountNumber": "ACC-10001"},
            "createTime": "2026-11-01T09:00:00Z",
        },
    },
    {
        "id": "job:3003",
        "attributes": {
            "jobNumber": "END-300003",
            "jobType": "Endorsement",
            "status": "Bound",
            "policy": {"policyNumber": "HO-200002-01"},
            "account": {"accountNumber": "ACC-10001"},
            "createTime": "2026-08-20T14:15:00Z",
        },
    },
    {
        "id": "job:3004",
        "attributes": {
            "jobNumber": "CAN-300004",
            "jobType": "Cancellation",
            "status": "Bound",
            "policy": {"policyNumber": "PA-200003-01"},
            "account": {"accountNumber": "ACC-10002"},
            "createTime": "2025-11-01T11:00:00Z",
        },
    },
    {
        "id": "job:3005",
        "attributes": {
            "jobNumber": "SUB-300005",
            "jobType": "Submission",
            "status": "Bound",
            "policy": {"policyNumber": "CP-200004-01"},
            "account": {"accountNumber": "ACC-10003"},
            "createTime": "2026-06-10T08:45:00Z",
        },
    },
    {
        "id": "job:3006",
        "attributes": {
            "jobNumber": "RNW-300006",
            "jobType": "Renewal",
            "status": "Withdrawn",
            "policy": {"policyNumber": "HO-200005-01"},
            "account": {"accountNumber": "ACC-10005"},
            "createTime": "2025-06-15T13:00:00Z",
        },
    },
]

# ---------------------------------------------------------------------------
# Coverage catalog and rating (mock only — a real tenant's product model
# defines its own coverage codes, terms and rating)
# ---------------------------------------------------------------------------

# code -> (display name, {option: annual premium adjustment})
COVERAGE_CATALOG: dict[str, tuple[str, dict[str, float]]] = {
    "PALiability": ("Bodily Injury & Property Damage Liability",
                    {"50/100": -120, "100/300": 0, "250/500": 180, "500/1000": 390}),
    "PACollision": ("Collision", {"$250 deductible": 140, "$500 deductible": 0,
                                  "$1,000 deductible": -110, "Not covered": -420}),
    "PAComprehensive": ("Comprehensive", {"$250 deductible": 60, "$500 deductible": 0,
                                          "$1,000 deductible": -45, "Not covered": -160}),
    "PARoadside": ("Roadside Assistance", {"Not covered": 0, "Included": 35}),
    "HODwelling": ("Dwelling (Coverage A)", {"$300,000": -150, "$350,000": 0, "$400,000": 160, "$500,000": 420}),
    "HODeductible": ("All-Peril Deductible", {"$500": 120, "$1,000": 0, "$2,500": -190}),
    "HOWaterBackup": ("Water Backup", {"Not covered": 0, "$10,000": 75, "$25,000": 140}),
    "CPBuilding": ("Building", {"$1,000,000": -600, "$1,200,000": 0, "$1,500,000": 900}),
    "CPGeneralLiability": ("General Liability", {"$1M/$2M": 0, "$2M/$4M": 1250}),
    "CPCyber": ("Cyber Liability", {"Not covered": 0, "$250,000": 680, "$1,000,000": 1650}),
}

BASE_PREMIUM = {"PersonalAuto": 688.0, "HomeOwners": 1450.0, "CommercialPackage": 8400.0}

# policyNumber -> (vehicles, {coverage code: value})
POLICY_DETAILS: dict[str, tuple[list[dict], dict[str, str]]] = {
    "PA-200001-01": (
        [{"id": "veh-1", "year": 2021, "make": "Toyota", "model": "Camry", "vin": "4T1B11HK5MU000001"}],
        {"PALiability": "100/300", "PACollision": "$500 deductible",
         "PAComprehensive": "$500 deductible", "PARoadside": "Not covered"},
    ),
    "HO-200002-01": ([], {"HODwelling": "$350,000", "HODeductible": "$1,000", "HOWaterBackup": "Not covered"}),
    "PA-200003-01": (
        [{"id": "veh-2", "year": 2017, "make": "Subaru", "model": "Outback", "vin": "4S4BSANC5H3000002"}],
        {"PALiability": "50/100", "PACollision": "$1,000 deductible",
         "PAComprehensive": "$1,000 deductible", "PARoadside": "Not covered"},
    ),
    "CP-200004-01": ([], {"CPBuilding": "$1,200,000", "CPGeneralLiability": "$1M/$2M", "CPCyber": "Not covered"}),
    "HO-200005-01": ([], {"HODwelling": "$300,000", "HODeductible": "$1,000", "HOWaterBackup": "Not covered"}),
    "PA-200006-01": (
        [
            {"id": "veh-3", "year": 2023, "make": "Ford", "model": "F-150", "vin": "1FTFW1E50PF000003"},
            {"id": "veh-4", "year": 2022, "make": "Honda", "model": "Accord", "vin": "1HGCV1F30NA000004"},
        ],
        {"PALiability": "250/500", "PACollision": "$500 deductible",
         "PAComprehensive": "$500 deductible", "PARoadside": "Included"},
    ),
}


# policyNumber -> drivers listed on the policy (Personal Auto only)
POLICY_DRIVERS: dict[str, list[dict]] = {
    "PA-200001-01": [{"id": "drv-1", "firstName": "Jane", "lastName": "Smith",
                      "dateOfBirth": "1985-04-12", "licenseNumber": "S530-4128-5512"}],
    "PA-200003-01": [{"id": "drv-2", "firstName": "Robert", "lastName": "Johnson",
                      "dateOfBirth": "1972-09-30", "licenseNumber": "J525-7230-9001"}],
    "PA-200006-01": [{"id": "drv-3", "firstName": "Dana", "lastName": "Smith",
                      "dateOfBirth": "1979-01-22", "licenseNumber": "S530-1979-0122"}],
}

YOUNG_DRIVER_AGE = 25
YOUNG_DRIVER_SURCHARGE = 350.0


def _age(dob: str, as_of: date) -> int:
    born = date.fromisoformat(dob)
    return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))


def _driver_rate(d: dict, as_of: date) -> float:
    return YOUNG_DRIVER_SURCHARGE if _age(d["dateOfBirth"], as_of) < YOUNG_DRIVER_AGE else 0.0


def _vehicle_rate(v: dict) -> float:
    return 420.0 + max(0, int(v["year"]) - 2015) * 12.0


def rate(product_code: str, vehicles: list[dict], coverages: list[dict],
         drivers: list[dict] = (), as_of: date | None = None) -> float:
    total = BASE_PREMIUM.get(product_code, 1000.0)
    total += sum(_vehicle_rate(v) for v in vehicles)
    total += sum(_driver_rate(d, as_of or date.today()) for d in drivers)
    for c in coverages:
        total += COVERAGE_CATALOG[c["code"]][1].get(c["value"], 0)
    return round(total, 2)


def _coverage_rows(values: dict[str, str]) -> list[dict]:
    return [
        {"code": code, "name": COVERAGE_CATALOG[code][0], "value": val,
         "options": list(COVERAGE_CATALOG[code][1])}
        for code, val in values.items()
    ]


def _account_name(a: dict) -> str:
    return a.get("companyName") or f"{a.get('firstName', '')} {a.get('lastName', '')}".strip()


def _match(value: str | None, target: str, partial: bool = False) -> bool:
    if value is None:
        return True
    if partial:
        return value.lower() in target.lower()
    return value.lower() == target.lower()


class MockEverywireClient:
    """Stateful mock. Each instance owns its own copy of the data."""

    supports_editing = True

    def __init__(self) -> None:
        self.accounts = copy.deepcopy(ACCOUNTS)
        self.policies = copy.deepcopy(POLICIES)
        self.jobs = copy.deepcopy(JOBS)
        self._details: dict[str, dict] = {}
        for pol in self.policies:
            a = pol["attributes"]
            vehicles, cov_values = copy.deepcopy(POLICY_DETAILS[a["policyNumber"]])
            drivers = copy.deepcopy(POLICY_DRIVERS.get(a["policyNumber"], []))
            coverages = _coverage_rows(cov_values)
            self._details[a["policyNumber"]] = {
                "vehicles": vehicles,
                "drivers": drivers,
                "coverages": coverages,
                "premium": rate(a["product"]["code"], vehicles, coverages,
                                drivers, date.fromisoformat(a["effectiveDate"])),
                "version": 1,
            }
        self._drafts: dict[str, dict] = {}  # policyNumber -> open policy change
        self._next_job = 300007
        self._next_vehicle = 5
        self._next_driver = 4

    # ------------------------------------------------------------------- state

    _STATE_FIELDS = ("accounts", "policies", "jobs", "_details", "_drafts", "_next_job", "_next_vehicle",
                     "_next_driver")

    def to_state(self) -> dict:
        """Everything the mock knows, as JSON-safe data (for persisting between requests)."""
        return {f: copy.deepcopy(getattr(self, f)) for f in self._STATE_FIELDS}

    @classmethod
    def from_state(cls, state: dict) -> "MockEverywireClient":
        client = cls()
        for f in cls._STATE_FIELDS:
            if f in state:
                setattr(client, f, copy.deepcopy(state[f]))
        # State saved before drivers existed has none on its policies or drafts.
        for rec in (*client._details.values(), *client._drafts.values()):
            rec.setdefault("drivers", [])
        return client

    # ------------------------------------------------------------------ search

    async def search(self, path: str, params: dict) -> dict:
        page_size = int(params.get("pageSize") or 25)

        if path == "/policies":
            results = self._search_policies(params)
        elif path == "/accounts":
            results = self._search_accounts(params)
        elif path == "/jobs":
            results = self._search_jobs(params)
        else:
            results = []

        return {"data": results[:page_size]}

    def _search_policies(self, p: dict) -> list:
        out = []
        for pol in self.policies:
            a = pol["attributes"]
            acct = a["account"]["accountNumber"]
            prod = a["product"]["code"]
            if not _match(p.get("policyNumber"), a["policyNumber"]):
                continue
            if not _match(p.get("accountNumber"), acct):
                continue
            if not _match(p.get("status"), a["status"]):
                continue
            if not _match(p.get("productCode"), prod):
                continue
            if p.get("effectiveDateStart") and a["effectiveDate"] < p["effectiveDateStart"]:
                continue
            if p.get("effectiveDateEnd") and a["effectiveDate"] > p["effectiveDateEnd"]:
                continue
            out.append(pol)
        return out

    def _search_accounts(self, p: dict) -> list:
        out = []
        for acct in self.accounts:
            a = acct["attributes"]
            if not _match(p.get("accountNumber"), a["accountNumber"]):
                continue
            if p.get("firstName") and not _match(p["firstName"], a.get("firstName", ""), partial=True):
                continue
            if p.get("lastName") and not _match(p["lastName"], a.get("lastName", ""), partial=True):
                continue
            if p.get("companyName") and not _match(p["companyName"], a.get("companyName", ""), partial=True):
                continue
            if not _match(p.get("emailAddress"), a.get("emailAddress", "")):
                continue
            out.append(acct)
        return out

    def _search_jobs(self, p: dict) -> list:
        out = []
        for job in self.jobs:
            a = job["attributes"]
            pol_num = a["policy"]["policyNumber"]
            acct_num = a["account"]["accountNumber"]
            if not _match(p.get("jobNumber"), a["jobNumber"]):
                continue
            if not _match(p.get("policyNumber"), pol_num):
                continue
            if not _match(p.get("accountNumber"), acct_num):
                continue
            if not _match(p.get("jobType"), a["jobType"]):
                continue
            if not _match(p.get("status"), a["status"]):
                continue
            out.append(job)
        return out

    # ----------------------------------------------------------------- helpers

    def _policy(self, policy_number: str) -> dict:
        for pol in self.policies:
            if pol["attributes"]["policyNumber"].lower() == (policy_number or "").strip().lower():
                return pol
        raise EverywireError(404, f"Policy {policy_number} not found")

    def _record(self, pol: dict) -> dict:
        a = pol["attributes"]
        acct = next(x["attributes"] for x in self.accounts
                    if x["attributes"]["accountNumber"] == a["account"]["accountNumber"])
        d = self._details[a["policyNumber"]]
        return {
            "policyNumber": a["policyNumber"],
            "status": a["status"],
            "effectiveDate": a["effectiveDate"],
            "expirationDate": a["expirationDate"],
            "product": a["product"],
            "account": {"accountNumber": acct["accountNumber"], "name": _account_name(acct)},
            **d,
        }

    def _job_rows(self, policy_number: str) -> list[dict]:
        rows = [j["attributes"] for j in self.jobs if j["attributes"]["policy"]["policyNumber"] == policy_number]
        rows.sort(key=lambda a: a["createTime"], reverse=True)
        return [{k: a[k] for k in ("jobNumber", "jobType", "status", "createTime")} for a in rows]

    def _view(self, policy_number: str) -> dict:
        pol = self._policy(policy_number)
        num = pol["attributes"]["policyNumber"]
        return build_policy_view(self._record(pol), self._drafts.get(num), self._job_rows(num))

    def _set_job_status(self, job_number: str, status: str) -> None:
        for j in self.jobs:
            if j["attributes"]["jobNumber"] == job_number:
                j["attributes"]["status"] = status

    def _bump(self, policy_number: str) -> None:
        self._details[policy_number]["version"] += 1

    def _draft(self, policy_number: str) -> tuple[str, dict]:
        num = self._policy(policy_number)["attributes"]["policyNumber"]
        draft = self._drafts.get(num)
        if not draft:
            raise EverywireError(409, f"Policy {num} has no open policy change. Call start_policy_change first.")
        return num, draft

    def _edited(self, num: str, draft: dict) -> dict:
        """Any edit to a quoted change sends it back to Draft (it must be re-quoted)."""
        if draft["status"] == "Quoted":
            draft["status"] = "Draft"
            draft["quote"] = None
            self._set_job_status(draft["jobNumber"], "Draft")
        self._bump(num)
        return self._view(num)

    # ------------------------------------------------------------- public API

    async def get_policy_view(self, policy_number: str) -> dict:
        return self._view(policy_number)

    async def start_policy_change(self, policy_number: str, effective_date: str, description: str = "") -> dict:
        pol = self._policy(policy_number)
        a = pol["attributes"]
        num = a["policyNumber"]
        if a["status"] not in EDITABLE_STATUSES:
            raise EverywireError(409, f"Policy {num} is {a['status']}; only in-force or scheduled policies can be changed.")
        if num in self._drafts:
            raise EverywireError(409, f"Policy {num} already has open policy change {self._drafts[num]['jobNumber']}. "
                                      "Edit that one, or withdraw it first.")
        try:
            eff = date.fromisoformat(effective_date)
        except (TypeError, ValueError):
            raise EverywireError(400, f"effectiveDate must be YYYY-MM-DD, got {effective_date!r}")
        if not (date.fromisoformat(a["effectiveDate"]) <= eff < date.fromisoformat(a["expirationDate"])):
            raise EverywireError(400, f"effectiveDate {effective_date} is outside the policy term "
                                      f"{a['effectiveDate']} → {a['expirationDate']}.")

        job_number = f"END-{self._next_job}"
        self._next_job += 1
        d = self._details[num]
        self._drafts[num] = {
            "jobNumber": job_number,
            "status": "Draft",
            "effectiveDate": eff.isoformat(),
            "description": description or "",
            "vehicles": copy.deepcopy(d["vehicles"]),
            "drivers": copy.deepcopy(d["drivers"]),
            "coverages": copy.deepcopy(d["coverages"]),
            "quote": None,
        }
        self.jobs.append({
            "id": f"job:{job_number}",
            "attributes": {
                "jobNumber": job_number,
                "jobType": "Endorsement",
                "status": "Draft",
                "policy": {"policyNumber": num},
                "account": a["account"],
                "createTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        })
        self._bump(num)
        return self._view(num)

    async def add_vehicle(self, policy_number: str, year: int, make: str, model: str, vin: str | None = None) -> dict:
        num, draft = self._draft(policy_number)
        if self._record(self._policy(num))["product"]["code"] != "PersonalAuto":
            raise EverywireError(400, f"Policy {num} is not a Personal Auto policy; it has no vehicles.")
        try:
            year = int(year)
        except (TypeError, ValueError):
            raise EverywireError(400, f"year must be a number, got {year!r}")
        if not (1950 <= year <= date.today().year + 1) or not make or not model:
            raise EverywireError(400, "Provide a valid year, make and model.")
        draft["vehicles"].append({
            "id": f"veh-{self._next_vehicle}", "year": year, "make": make.strip(),
            "model": model.strip(), "vin": (vin or "").strip().upper(),
        })
        self._next_vehicle += 1
        return self._edited(num, draft)

    async def remove_vehicle(self, policy_number: str, vehicle_id: str) -> dict:
        num, draft = self._draft(policy_number)
        if not any(v["id"] == vehicle_id for v in draft["vehicles"]):
            ids = ", ".join(v["id"] for v in draft["vehicles"]) or "none"
            raise EverywireError(404, f"Vehicle {vehicle_id} isn't on the policy change. Vehicle ids: {ids}.")
        if len(draft["vehicles"]) == 1:
            raise EverywireError(400, "A Personal Auto policy needs at least one vehicle.")
        draft["vehicles"] = [v for v in draft["vehicles"] if v["id"] != vehicle_id]
        return self._edited(num, draft)

    async def add_driver(self, policy_number: str, first_name: str, last_name: str, date_of_birth: str,
                         license_number: str | None = None) -> dict:
        num, draft = self._draft(policy_number)
        if self._record(self._policy(num))["product"]["code"] != "PersonalAuto":
            raise EverywireError(400, f"Policy {num} is not a Personal Auto policy; it has no drivers.")
        first, last = (first_name or "").strip(), (last_name or "").strip()
        if not first or not last:
            raise EverywireError(400, "Provide the driver's first and last name.")
        try:
            dob = date.fromisoformat(date_of_birth)
        except (TypeError, ValueError):
            raise EverywireError(400, f"dateOfBirth must be YYYY-MM-DD, got {date_of_birth!r}")
        age = _age(dob.isoformat(), date.fromisoformat(draft["effectiveDate"]))
        if not 15 <= age <= 110:
            raise EverywireError(400, f"A driver must be 15 or older on the change's effective date; "
                                      f"{first} {last} would be {age}.")
        draft["drivers"].append({
            "id": f"drv-{self._next_driver}", "firstName": first, "lastName": last,
            "dateOfBirth": dob.isoformat(), "licenseNumber": (license_number or "").strip().upper(),
        })
        self._next_driver += 1
        return self._edited(num, draft)

    async def update_coverage(self, policy_number: str, coverage_code: str, value: str) -> dict:
        num, draft = self._draft(policy_number)
        cov = next((c for c in draft["coverages"] if c["code"].lower() == (coverage_code or "").lower()), None)
        if not cov:
            codes = ", ".join(c["code"] for c in draft["coverages"])
            raise EverywireError(404, f"Coverage {coverage_code} isn't on policy {num}. Coverage codes: {codes}.")
        match = next((o for o in cov["options"] if o.lower() == (value or "").strip().lower()), None)
        if not match:
            raise EverywireError(400, f"{value!r} isn't an allowed value for {cov['code']}. "
                                      f"Allowed: {', '.join(cov['options'])}.")
        cov["value"] = match
        return self._edited(num, draft)

    async def quote_policy_change(self, policy_number: str) -> dict:
        num, draft = self._draft(policy_number)
        rec = self._record(self._policy(num))
        start = date.fromisoformat(draft["effectiveDate"])
        new_annual = rate(rec["product"]["code"], draft["vehicles"], draft["coverages"], draft["drivers"], start)
        term_start = date.fromisoformat(rec["effectiveDate"])
        term_end = date.fromisoformat(rec["expirationDate"])
        fraction = (term_end - start).days / (term_end - term_start).days
        draft["quote"] = {
            "annualPremium": new_annual,
            "changeInCost": round((new_annual - rec["premium"]) * fraction, 2),
        }
        draft["status"] = "Quoted"
        self._set_job_status(draft["jobNumber"], "Quoted")
        self._bump(num)
        return self._view(num)

    async def bind_policy_change(self, policy_number: str) -> dict:
        num, draft = self._draft(policy_number)
        if draft["status"] != "Quoted":
            raise EverywireError(409, f"Policy change {draft['jobNumber']} must be quoted before it can be bound.")
        d = self._details[num]
        d["vehicles"] = draft["vehicles"]
        d["drivers"] = draft["drivers"]
        d["coverages"] = draft["coverages"]
        d["premium"] = draft["quote"]["annualPremium"]
        self._set_job_status(draft["jobNumber"], "Bound")
        del self._drafts[num]
        self._bump(num)
        return self._view(num)

    async def withdraw_policy_change(self, policy_number: str) -> dict:
        num, draft = self._draft(policy_number)
        self._set_job_status(draft["jobNumber"], "Withdrawn")
        del self._drafts[num]
        self._bump(num)
        return self._view(num)
