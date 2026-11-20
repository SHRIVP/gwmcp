"""In-memory PAS stand-in.

Search data keeps the JSON:API-ish shape the search tools already format.
Policy details (vehicles, coverages, premium) and the policy-change workflow
(start → edit → quote → bind / withdraw) are stateful per client instance, so
edits made through Claude persist for the life of the server process.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone

from client import EverywireError
from property_data import lookup_failed, lookup_property
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

# New business: product code -> (display name, policy number prefix, coverage code prefix)
NEW_BUSINESS_PRODUCTS = {
    "PersonalAuto": ("Personal Auto", "PA", "PA"),
    "HomeOwners": ("Homeowners", "HO", "HO"),
}
SUBMISSION_BACKDATE_DAYS = 30
SUBMISSION_LEAD_DAYS = 365

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


# ------------------------------------------------------------ homeowners rating
# Demo factors, not filed rates. Applied only to Homeowners records that have home details.

CONSTRUCTION_TYPES = {"frame": 1.10, "masonry_veneer": 1.00, "masonry": 0.92, "superior": 0.85}
STATE_FACTORS = {"FL": 1.45, "LA": 1.40, "TX": 1.30, "OK": 1.25, "CA": 1.15, "CO": 1.10,
                 "NY": 1.05, "OH": 0.90, "ID": 0.85}
REQUIRED_HOME_FIELDS = {"address": "address", "yearBuilt": "year built",
                        "constructionType": "construction type", "squareFeet": "square footage"}


def _factor(name: str, detail: str, multiplier: float) -> dict:
    return {"name": name, "detail": detail, "multiplier": multiplier}


def _flood_factor(lookup: dict | None) -> dict:
    flood = (lookup or {}).get("flood") or {}
    status, zone = flood.get("status"), flood.get("zone") or ""
    if status == "ok":
        mult = 1.60 if zone.startswith("V") else 1.35 if zone.startswith("A") else 1.0
        return _factor("Flood zone", f"zone {zone}", mult)
    detail = {"not_mapped": "outside mapped flood areas", "skipped": "address not verified"}.get(status, "unavailable")
    if status == "skipped" and ((lookup or {}).get("geocode") or {}).get("status") == "failed":
        detail = "unavailable"
    return _factor("Flood zone", detail, 1.0)


def _state_factor(lookup: dict | None) -> dict:
    geo = (lookup or {}).get("geocode") or {}
    if geo.get("status") == "ok" and geo.get("state"):
        return _factor("State", geo["state"], STATE_FACTORS.get(geo["state"], 1.0))
    return _factor("State", "address not verified" if geo.get("status") == "no_match" else "unavailable", 1.0)


def home_factors(home: dict, as_of: date) -> list[dict]:
    factors = []
    built = home.get("yearBuilt")
    if built is None:
        factors.append(_factor("Home age", "not given", 1.0))
    else:
        age = as_of.year - built
        factors.append(_factor("Home age", f"{age} years",
                               0.90 if age <= 10 else 1.00 if age <= 25 else 1.12 if age <= 50 else 1.25))
    ctype = home.get("constructionType")
    factors.append(_factor("Construction", ctype.replace("_", " ") if ctype else "not given",
                           CONSTRUCTION_TYPES.get(ctype, 1.0)))
    sqft = home.get("squareFeet")
    factors.append(_factor("Size", f"{sqft:,} sq ft" if sqft else "not given",
                           round(min(1.6, max(0.75, 0.5 + 0.5 * sqft / 2000)), 3) if sqft else 1.0))
    roof = home.get("roofYear")
    if roof is None:
        factors.append(_factor("Roof age", "not given", 1.0))
    else:
        r = as_of.year - roof
        factors.append(_factor("Roof age", f"{r} years", 0.95 if r <= 10 else 1.05 if r <= 20 else 1.20))
    stories = home.get("stories")
    factors.append(_factor("Stories", str(stories) if stories else "not given",
                           1.03 if stories == 2 else 1.08 if stories and stories >= 3 else 1.0))
    factors.append(_flood_factor(home.get("lookup")))
    factors.append(_state_factor(home.get("lookup")))
    return factors


def rate(product_code: str, vehicles: list[dict], coverages: list[dict],
         drivers: list[dict] = (), as_of: date | None = None, home: dict | None = None) -> float:
    total = BASE_PREMIUM.get(product_code, 1000.0)
    if product_code == "HomeOwners" and home:
        for f in home_factors(home, as_of or date.today()):
            total *= f["multiplier"]
    total += sum(_vehicle_rate(v) for v in vehicles)
    total += sum(_driver_rate(d, as_of or date.today()) for d in drivers)
    for c in coverages:
        total += COVERAGE_CATALOG[c["code"]][1].get(c["value"], 0)
    return round(total, 2)


def _one_year_after(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year + 1, day=28)


def _standard_coverages(coverage_prefix: str) -> dict[str, str]:
    """Each of the product's coverages at its standard option (the one with no premium adjustment)."""
    return {code: next(opt for opt, adj in options.items() if adj == 0)
            for code, (_name, options) in COVERAGE_CATALOG.items() if code.startswith(coverage_prefix)}


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

    def __init__(self, property_lookup=None) -> None:
        # async (address) -> lookup result; not persisted. Tests pass a fake.
        self.property_lookup = property_lookup or lookup_property
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
                "home": None,
                "coverages": coverages,
                "premium": rate(a["product"]["code"], vehicles, coverages,
                                drivers, date.fromisoformat(a["effectiveDate"])),
                "version": 1,
            }
        self._drafts: dict[str, dict] = {}  # policyNumber -> open policy change
        self._next_job = 300007
        self._next_vehicle = 5
        self._next_driver = 4
        self._next_account = 10006
        self._next_policy = 200007

    # ------------------------------------------------------------------- state

    _STATE_FIELDS = ("accounts", "policies", "jobs", "_details", "_drafts", "_next_job", "_next_vehicle",
                     "_next_driver", "_next_account", "_next_policy")

    def to_state(self) -> dict:
        """Everything the mock knows, as JSON-safe data (for persisting between requests)."""
        return {f: copy.deepcopy(getattr(self, f)) for f in self._STATE_FIELDS}

    @classmethod
    def from_state(cls, state: dict, property_lookup=None) -> "MockEverywireClient":
        client = cls(property_lookup)
        for f in cls._STATE_FIELDS:
            if f in state:
                setattr(client, f, copy.deepcopy(state[f]))
        # State saved before drivers / home details / submissions existed.
        for rec in (*client._details.values(), *client._drafts.values()):
            rec.setdefault("drivers", [])
            rec.setdefault("home", None)
        for draft in client._drafts.values():
            draft.setdefault("kind", "change")
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

    async def create_account(self, first_name: str | None, last_name: str | None, company_name: str | None,
                             email: str, address_line1: str, city: str, state: str, postal_code: str,
                             phone: str | None = None) -> dict:
        first, last, company = ((x or "").strip() for x in (first_name, last_name, company_name))
        if company and (first or last):
            raise EverywireError(400, "Give either a person's first and last name or a company name, not both.")
        if not company and not (first and last):
            raise EverywireError(400, "Give the customer's first and last name, or a company name.")
        email = (email or "").strip()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise EverywireError(400, f"{email!r} isn't a valid email address.")
        line1, city = (address_line1 or "").strip(), (city or "").strip()
        state, postal = (state or "").strip().upper(), (postal_code or "").strip()
        if not line1 or not city:
            raise EverywireError(400, "The mailing address needs a street address and a city.")
        if len(state) != 2 or not state.isalpha():
            raise EverywireError(400, f"state must be a 2-letter code like IL, got {state!r}.")
        digits = postal.replace("-", "")
        if not (digits.isdigit() and (len(postal) == 5 or (len(postal) == 10 and postal[5] == "-"))):
            raise EverywireError(400, f"postalCode must be a 5-digit ZIP or ZIP+4, got {postal!r}.")

        number = f"ACC-{self._next_account}"
        self._next_account += 1
        attrs = {"accountNumber": number}
        attrs.update({"companyName": company} if company else {"firstName": first, "lastName": last})
        attrs["emailAddress"] = email
        if (phone or "").strip():
            attrs["phone"] = phone.strip()
        attrs["primaryAddress"] = {"addressLine1": line1, "city": city, "state": state, "postalCode": postal}
        account = {"id": f"account:{number}", "attributes": attrs}
        self.accounts.append(account)
        return copy.deepcopy(account)

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
            "home": copy.deepcopy(d["home"]),
            "coverages": copy.deepcopy(d["coverages"]),
            "quote": None,
            "kind": "change",
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

    async def start_submission(self, account_number: str, product_code: str, effective_date: str,
                               description: str = "") -> dict:
        code = next((c for c in NEW_BUSINESS_PRODUCTS if c.lower() == (product_code or "").strip().lower()), None)
        if not code:
            raise EverywireError(400, f"New business is available for PersonalAuto or HomeOwners, "
                                      f"not {product_code!r}.")
        acct = next((x["attributes"] for x in self.accounts
                     if x["attributes"]["accountNumber"].lower() == (account_number or "").strip().lower()), None)
        if not acct:
            raise EverywireError(404, f"Account {account_number} not found. Create it with create_account first.")
        try:
            eff = date.fromisoformat(effective_date)
        except (TypeError, ValueError):
            raise EverywireError(400, f"effectiveDate must be YYYY-MM-DD, got {effective_date!r}")
        today = date.today()
        if (today - eff).days > SUBMISSION_BACKDATE_DAYS:
            raise EverywireError(400, f"effectiveDate can't be more than {SUBMISSION_BACKDATE_DAYS} days ago.")
        if (eff - today).days > SUBMISSION_LEAD_DAYS:
            raise EverywireError(400, f"effectiveDate can't be more than {SUBMISSION_LEAD_DAYS} days ahead.")

        name, number_prefix, coverage_prefix = NEW_BUSINESS_PRODUCTS[code]
        num = f"{number_prefix}-{self._next_policy}-01"
        self._next_policy += 1
        self.policies.append({
            "id": f"policy:{num}",
            "attributes": {
                "policyNumber": num, "status": "submission",
                "effectiveDate": eff.isoformat(), "expirationDate": _one_year_after(eff).isoformat(),
                "product": {"name": name, "code": code},
                "account": {"accountNumber": acct["accountNumber"]},
            },
        })
        coverages = _coverage_rows(_standard_coverages(coverage_prefix))
        self._details[num] = {"vehicles": [], "drivers": [], "home": None, "coverages": coverages,
                              "premium": 0.0, "version": 1}
        job_number = f"SUB-{self._next_job}"
        self._next_job += 1
        self._drafts[num] = {
            "jobNumber": job_number, "status": "Draft", "effectiveDate": eff.isoformat(),
            "description": description or "", "vehicles": [], "drivers": [], "home": None,
            "coverages": copy.deepcopy(coverages), "quote": None, "kind": "submission",
        }
        self.jobs.append({
            "id": f"job:{job_number}",
            "attributes": {
                "jobNumber": job_number, "jobType": "Submission", "status": "Draft",
                "policy": {"policyNumber": num}, "account": {"accountNumber": acct["accountNumber"]},
                "createTime": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        })
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

    async def home_lookup_needed(self, policy_number: str, address: str | None) -> str | None:
        """The address set_home_details / quote would look up now, or None. Read-only."""
        num = self._policy(policy_number)["attributes"]["policyNumber"]
        draft = self._drafts.get(num)
        if not draft or self._record(self._policy(num))["product"]["code"] != "HomeOwners":
            return None
        home = draft.get("home") or {}
        new = (address or "").strip()
        if new and new != home.get("address"):
            return new
        if home.get("address") and lookup_failed(home.get("lookup")):
            return home["address"]
        return None

    async def set_home_details(self, policy_number: str, address: str | None = None, year_built=None,
                               construction_type: str | None = None, square_feet=None, roof_year=None,
                               stories=None, lookup: dict | None = None) -> dict:
        num, draft = self._draft(policy_number)
        if self._record(self._policy(num))["product"]["code"] != "HomeOwners":
            raise EverywireError(400, f"Policy {num} is not a Homeowners policy; it has no home details.")
        home = copy.deepcopy(draft.get("home")) or {
            "address": None, "yearBuilt": None, "constructionType": None, "squareFeet": None,
            "roofYear": None, "stories": None, "lookup": None}
        this_year = date.today().year

        def as_int(value, label):
            try:
                return int(value)
            except (TypeError, ValueError):
                raise EverywireError(400, f"{label} must be a whole number, got {value!r}")

        address = (address or "").strip() or None
        if not address and not home["address"]:
            raise EverywireError(400, "Give the property address first (one line, e.g. "
                                      "'12 Oak St, Springfield, IL 62704').")
        if year_built is not None:
            year_built = as_int(year_built, "year built")
            if not 1800 <= year_built <= this_year:
                raise EverywireError(400, f"year built must be between 1800 and {this_year}.")
            home["yearBuilt"] = year_built
        if construction_type is not None:
            ctype = construction_type.strip().lower().replace(" ", "_").replace("-", "_")
            if ctype not in CONSTRUCTION_TYPES:
                raise EverywireError(400, f"{construction_type!r} isn't a construction type. "
                                          f"Allowed: {', '.join(CONSTRUCTION_TYPES)}.")
            home["constructionType"] = ctype
        if square_feet is not None:
            square_feet = as_int(square_feet, "square footage")
            if not 300 <= square_feet <= 20000:
                raise EverywireError(400, "square footage must be between 300 and 20,000.")
            home["squareFeet"] = square_feet
        if roof_year is not None:
            roof_year = as_int(roof_year, "roof year")
            if not (home["yearBuilt"] or 1800) <= roof_year <= this_year:
                raise EverywireError(400, f"roof year must be between the year built and {this_year}.")
            home["roofYear"] = roof_year
        if stories is not None:
            stories = as_int(stories, "stories")
            if not 1 <= stories <= 4:
                raise EverywireError(400, "stories must be between 1 and 4.")
            home["stories"] = stories

        target = await self.home_lookup_needed(num, address)
        if address:
            home["address"] = address
        draft["home"] = home
        if target:
            if not lookup or lookup.get("input") != target:
                lookup = await self.property_lookup(target)
            home["lookup"] = lookup
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

    async def quote_policy_change(self, policy_number: str, lookup: dict | None = None) -> dict:
        num, draft = self._draft(policy_number)
        rec = self._record(self._policy(num))
        product = rec["product"]["code"]
        submission = draft["kind"] == "submission"
        if submission and product == "PersonalAuto" and not (draft["vehicles"] and draft["drivers"]):
            raise EverywireError(400, "Add at least one vehicle and one driver before quoting.")
        home = draft.get("home")
        if submission and product == "HomeOwners":
            missing = [label for key, label in REQUIRED_HOME_FIELDS.items() if not (home or {}).get(key)]
            if missing:
                raise EverywireError(400, f"Missing home details: {', '.join(missing)}. "
                                          "Add them with set_home_details before quoting.")
        if home and lookup_failed(home.get("lookup")):
            if not lookup or lookup.get("input") != home["address"]:
                lookup = await self.property_lookup(home["address"])
            home["lookup"] = lookup
        start = date.fromisoformat(draft["effectiveDate"])
        new_annual = rate(product, draft["vehicles"], draft["coverages"], draft["drivers"], start, home)
        term_start = date.fromisoformat(rec["effectiveDate"])
        term_end = date.fromisoformat(rec["expirationDate"])
        fraction = (term_end - start).days / (term_end - term_start).days
        draft["quote"] = {
            "annualPremium": new_annual,
            "changeInCost": new_annual if submission else round((new_annual - rec["premium"]) * fraction, 2),
            "factors": home_factors(home, start) if product == "HomeOwners" and home else [],
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
        d["home"] = draft["home"]
        d["coverages"] = draft["coverages"]
        d["premium"] = draft["quote"]["annualPremium"]
        if draft["kind"] == "submission":
            a = self._policy(num)["attributes"]
            a["status"] = "inForce" if date.fromisoformat(a["effectiveDate"]) <= date.today() else "scheduled"
        self._set_job_status(draft["jobNumber"], "Bound")
        del self._drafts[num]
        self._bump(num)
        return self._view(num)

    async def withdraw_policy_change(self, policy_number: str) -> dict:
        num, draft = self._draft(policy_number)
        self._set_job_status(draft["jobNumber"], "Withdrawn")
        if draft["kind"] == "submission":
            self._policy(num)["attributes"]["status"] = "withdrawn"
        del self._drafts[num]
        self._bump(num)
        return self._view(num)
