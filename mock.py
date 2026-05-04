from __future__ import annotations

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
            "effectiveDate": "2024-06-01",
            "expirationDate": "2025-06-01",
            "product": {"name": "Personal Auto", "code": "PersonalAuto"},
            "account": {"accountNumber": "ACC-10001"},
        },
    },
    {
        "id": "policy:2002",
        "attributes": {
            "policyNumber": "HO-200002-01",
            "status": "inForce",
            "effectiveDate": "2024-03-15",
            "expirationDate": "2025-03-15",
            "product": {"name": "Homeowners", "code": "HomeOwners"},
            "account": {"accountNumber": "ACC-10001"},
        },
    },
    {
        "id": "policy:2003",
        "attributes": {
            "policyNumber": "PA-200003-01",
            "status": "canceled",
            "effectiveDate": "2023-01-01",
            "expirationDate": "2024-01-01",
            "product": {"name": "Personal Auto", "code": "PersonalAuto"},
            "account": {"accountNumber": "ACC-10002"},
        },
    },
    {
        "id": "policy:2004",
        "attributes": {
            "policyNumber": "CP-200004-01",
            "status": "inForce",
            "effectiveDate": "2024-07-01",
            "expirationDate": "2025-07-01",
            "product": {"name": "Commercial Package", "code": "CommercialPackage"},
            "account": {"accountNumber": "ACC-10003"},
        },
    },
    {
        "id": "policy:2005",
        "attributes": {
            "policyNumber": "HO-200005-01",
            "status": "expired",
            "effectiveDate": "2022-09-01",
            "expirationDate": "2023-09-01",
            "product": {"name": "Homeowners", "code": "HomeOwners"},
            "account": {"accountNumber": "ACC-10005"},
        },
    },
    {
        "id": "policy:2006",
        "attributes": {
            "policyNumber": "PA-200006-01",
            "status": "scheduled",
            "effectiveDate": "2025-08-01",
            "expirationDate": "2026-08-01",
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
            "createTime": "2024-05-15T10:30:00Z",
        },
    },
    {
        "id": "job:3002",
        "attributes": {
            "jobNumber": "RNW-300002",
            "jobType": "Renewal",
            "status": "Quoted",
            "policy": {"policyNumber": "PA-200001-01"},
            "account": {"accountNumber": "ACC-10001"},
            "createTime": "2025-03-01T09:00:00Z",
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
            "createTime": "2024-08-20T14:15:00Z",
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
            "createTime": "2023-11-01T11:00:00Z",
        },
    },
    {
        "id": "job:3005",
        "attributes": {
            "jobNumber": "SUB-300005",
            "jobType": "Submission",
            "status": "Draft",
            "policy": {"policyNumber": "CP-200004-01"},
            "account": {"accountNumber": "ACC-10003"},
            "createTime": "2024-06-10T08:45:00Z",
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
            "createTime": "2023-06-15T13:00:00Z",
        },
    },
]


def _match(value: str | None, target: str, partial: bool = False) -> bool:
    if value is None:
        return True
    if partial:
        return value.lower() in target.lower()
    return value.lower() == target.lower()


class MockGuidewireClient:
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
        for pol in POLICIES:
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
        for acct in ACCOUNTS:
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
        for job in JOBS:
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
