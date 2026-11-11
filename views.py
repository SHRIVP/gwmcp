"""Shared policy view model.

Both the mock and (later) the live Everywire client hand this module a
normalized policy record plus its open policy change, and get back the one
structure the UI and Claude both read:

- ``build_policy_view`` -> dict sent as ``structuredContent`` (the UI renders it)
- ``policy_view_text``  -> plain-text summary sent as ``content`` (Claude reads it)

Normalized policy record::

    {
      "policyNumber", "status", "effectiveDate", "expirationDate",
      "product": {"code", "name"},
      "account": {"accountNumber", "name"},
      "premium": float,                      # annual premium
      "vehicles": [{"id", "year", "make", "model", "vin"}],
      "drivers": [{"id", "firstName", "lastName", "dateOfBirth", "licenseNumber"}],
      "coverages": [{"code", "name", "value", "options": [str]}],
      "version": int,                        # bumps on every change
    }

Open policy change (draft job)::

    {
      "jobNumber", "status" ("Draft" | "Quoted"), "effectiveDate", "description",
      "kind": "change" | "submission",          # submission = new business
      "vehicles": [...], "drivers": [...], "coverages": [...],  # full proposed state
      "quote": {"annualPremium", "changeInCost"} | None,
    }
"""

from __future__ import annotations

EDITABLE_STATUSES = ("inForce", "scheduled")


def _diff_by_id(current: list[dict], proposed: list[dict]) -> list[dict]:
    """Mark rows added or removed by the change, matching on id (vehicles, drivers)."""
    proposed_ids = {v["id"] for v in proposed}
    current_ids = {v["id"] for v in current}
    rows = [{**v, "change": None if v["id"] in current_ids else "added"} for v in proposed]
    rows += [{**v, "change": "removed"} for v in current if v["id"] not in proposed_ids]
    return rows


def _diff_coverages(current: list[dict], proposed: list[dict]) -> list[dict]:
    before = {c["code"]: c["value"] for c in current}
    rows = []
    for c in proposed:
        prev = before.get(c["code"])
        changed = prev is not None and prev != c["value"]
        rows.append({**c, "previousValue": prev if changed else None, "change": "changed" if changed else None})
    return rows


def build_policy_view(policy: dict, draft: dict | None, jobs: list[dict]) -> dict:
    view = {
        "policyNumber": policy["policyNumber"],
        "status": policy["status"],
        "effectiveDate": policy["effectiveDate"],
        "expirationDate": policy["expirationDate"],
        "product": policy["product"],
        "account": policy["account"],
        "premium": policy["premium"],
        "vehicles": policy.get("vehicles", []),
        "drivers": policy.get("drivers", []),
        "home": policy.get("home"),
        "coverages": policy.get("coverages", []),
        "canEdit": policy["status"] in EDITABLE_STATUSES,
        "version": policy.get("version", 0),
        "jobs": jobs,
        "draft": None,
    }
    if draft:
        view["draft"] = {
            "kind": draft.get("kind", "change"),
            "jobNumber": draft["jobNumber"],
            "status": draft["status"],
            "effectiveDate": draft["effectiveDate"],
            "description": draft.get("description") or "",
            "vehicles": _diff_by_id(view["vehicles"], draft["vehicles"]),
            "drivers": _diff_by_id(view["drivers"], draft.get("drivers", [])),
            "home": draft.get("home"),
            "coverages": _diff_coverages(view["coverages"], draft["coverages"]),
            "quote": draft.get("quote"),
        }
    return view


def _money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def _vehicle_label(v: dict) -> str:
    vin = f" (VIN {v['vin']})" if v.get("vin") else ""
    return f"{v['year']} {v['make']} {v['model']}{vin} [id {v['id']}]"


def _driver_label(d: dict) -> str:
    lic = f", license {d['licenseNumber']}" if d.get("licenseNumber") else ""
    return f"{d['firstName']} {d['lastName']} (born {d['dateOfBirth']}{lic}) [id {d['id']}]"


def _home_lines(home: dict, open_draft: bool) -> list[str]:
    lookup = home.get("lookup") or {}
    geo, flood = lookup.get("geocode") or {}, lookup.get("flood") or {}
    lines = ["Home:"]
    if geo.get("status") == "ok":
        lines.append(f"  • Address: {home['address']} (matched: {geo['matchedAddress']}, "
                     f"{geo.get('county') or 'county unknown'}, {geo.get('state')}; source {geo['source']})")
    elif geo.get("status") == "no_match":
        lines.append(f"  • Address: {home['address']} (address not verified: no match in {geo['source']})")
    else:
        lines.append(f"  • Address: {home['address']} (address check unavailable)")
    if flood.get("status") == "ok":
        lines.append(f"  • Flood zone: {flood['zone']} (source {flood['source']})")
    elif flood.get("status") == "not_mapped":
        lines.append(f"  • Flood zone: outside mapped flood areas (source {flood['source']})")
    elif flood.get("status") == "failed":
        lines.append("  • Flood zone: unavailable (FEMA lookup failed" +
                     ("; it will be retried)" if open_draft else ")"))
    details = [
        f"built {home['yearBuilt']}" if home.get("yearBuilt") else "year built not given",
        (home.get("constructionType") or "construction not given").replace("_", " "),
        f"{home['squareFeet']:,} sq ft" if home.get("squareFeet") else "square footage not given",
        f"roof {home['roofYear']}" if home.get("roofYear") else "roof year not given",
        f"{home['stories']} stories" if home.get("stories") else "stories not given",
    ]
    lines.append("  • " + " · ".join(details))
    return lines


def policy_view_text(view: dict, headline: str | None = None) -> str:
    lines = []
    submission = (view.get("draft") or {}).get("kind") == "submission"
    if headline:
        if submission:
            headline = headline.replace("the policy change", "the new submission")
        lines += [headline, ""]
    lines.append(
        f"Policy {view['policyNumber']} · {view['product']['name']} · {view['status']} · "
        f"{view['effectiveDate']} → {view['expirationDate']}"
    )
    lines.append(f"Account {view['account']['accountNumber']} · {view['account']['name']}")
    if view["status"] in ("submission", "withdrawn"):
        lines.append("Annual premium: not issued yet")
    else:
        lines.append(f"Annual premium: {_money(view['premium'])}")

    if view["vehicles"]:
        lines.append("Vehicles:")
        lines += [f"  • {_vehicle_label(v)}" for v in view["vehicles"]]
    if view["drivers"]:
        lines.append("Drivers:")
        lines += [f"  • {_driver_label(d)}" for d in view["drivers"]]
    home = (view.get("draft") or {}).get("home") or view.get("home")
    if home:
        lines += _home_lines(home, bool(view.get("draft")))
    elif view["product"]["code"] == "HomeOwners" and view.get("draft", {}) and view["draft"]["kind"] == "submission":
        lines.append("Home: no details yet — call set_home_details with the property address.")
    lines.append("Coverages (code: value — allowed values):")
    lines += [f"  • {c['code']}: {c['value']} — {', '.join(c['options'])}" for c in view["coverages"]]

    d = view.get("draft")
    if d:
        lines.append("")
        title = "New submission" if d["kind"] == "submission" else "Open policy change"
        lines.append(f"{title} {d['jobNumber']} · {d['status']} · effective {d['effectiveDate']}")
        if d["description"]:
            lines.append(f"  Description: {d['description']}")
        changes = [f"  + added {_vehicle_label(v)}" for v in d["vehicles"] if v["change"] == "added"]
        changes += [f"  − removed {_vehicle_label(v)}" for v in d["vehicles"] if v["change"] == "removed"]
        changes += [f"  + added driver {_driver_label(x)}" for x in d["drivers"] if x["change"] == "added"]
        changes += [f"  − removed driver {_driver_label(x)}" for x in d["drivers"] if x["change"] == "removed"]
        changes += [
            f"  ~ {c['code']}: {c['previousValue']} → {c['value']}" for c in d["coverages"] if c["change"]
        ]
        lines += changes or ([] if d["kind"] == "submission" else ["  (no changes yet)"])
        if d["quote"]:
            q = d["quote"]
            for f in q.get("factors") or []:
                lines.append(f"  {f['name']}: {f['detail']} ×{f['multiplier']}")
            if d["kind"] == "submission":
                lines.append(f"  Quoted: annual premium {_money(q['annualPremium'])}")
                lines.append("  The user binds the new policy with the Bind button in the Policy System view.")
            else:
                lines.append(
                    f"  Quoted: new annual premium {_money(q['annualPremium'])}, "
                    f"change in cost for remaining term {_money(q['changeInCost'])}"
                )
                lines.append("  The user binds the change with the Bind button in the Policy System view.")
        else:
            lines.append("  Not quoted yet — call quote_policy_change when the edits are done.")
    elif view["canEdit"]:
        lines.append("")
        lines.append("No open policy change. Call start_policy_change before editing.")
    else:
        lines.append("")
        lines.append(f"This policy is {view['status']} and can't be changed.")
    return "\n".join(lines)
