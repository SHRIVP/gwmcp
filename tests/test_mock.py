import asyncio
import pytest

from client import EverywireError
from mock import MockEverywireClient
from views import policy_view_text


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def gw():
    return MockEverywireClient()


def test_open_policy_has_details(gw):
    v = run(gw.get_policy_view("PA-200001-01"))
    assert v["account"]["name"] == "Jane Smith"
    assert [x["model"] for x in v["vehicles"]] == ["Camry"]
    assert v["premium"] == 1180.0
    assert v["draft"] is None and v["canEdit"]


def test_full_policy_change_flow(gw):
    v = run(gw.start_policy_change("PA-200001-01", "2026-12-01", "Add new car"))
    assert v["draft"]["status"] == "Draft"
    job = v["draft"]["jobNumber"]

    v = run(gw.add_vehicle("PA-200001-01", 2024, "Honda", "Civic", "2hgfe1f30rh000099"))
    added = [x for x in v["draft"]["vehicles"] if x["change"] == "added"]
    assert added and added[0]["vin"] == "2HGFE1F30RH000099"
    assert len(v["vehicles"]) == 1  # live policy untouched

    v = run(gw.update_coverage("PA-200001-01", "PACollision", "$1,000 deductible"))
    col = next(c for c in v["draft"]["coverages"] if c["code"] == "PACollision")
    assert col["previousValue"] == "$500 deductible" and col["change"] == "changed"

    v = run(gw.quote_policy_change("PA-200001-01"))
    q = v["draft"]["quote"]
    assert v["draft"]["status"] == "Quoted"
    assert q["annualPremium"] == 1180.0 + 528.0 - 110.0
    assert 0 < q["changeInCost"] < q["annualPremium"] - 1180.0  # prorated

    v = run(gw.bind_policy_change("PA-200001-01"))
    assert v["draft"] is None
    assert v["premium"] == q["annualPremium"]
    assert [x["model"] for x in v["vehicles"]] == ["Camry", "Civic"]
    assert any(j["jobNumber"] == job and j["status"] == "Bound" for j in v["jobs"])

    found = run(gw.search("/jobs", {"jobNumber": job}))["data"]
    assert found[0]["attributes"]["status"] == "Bound"


def test_editing_quoted_change_requires_requote(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    run(gw.quote_policy_change("PA-200001-01"))
    v = run(gw.update_coverage("PA-200001-01", "PARoadside", "included"))
    assert v["draft"]["status"] == "Draft" and v["draft"]["quote"] is None
    with pytest.raises(EverywireError, match="must be quoted"):
        run(gw.bind_policy_change("PA-200001-01"))


def test_withdraw_leaves_policy_unchanged(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    run(gw.add_vehicle("PA-200001-01", 2020, "Mazda", "CX-5"))
    v = run(gw.withdraw_policy_change("PA-200001-01"))
    assert v["draft"] is None and len(v["vehicles"]) == 1


@pytest.mark.parametrize("call, message", [
    (lambda gw: gw.start_policy_change("PA-200003-01", "2025-06-01"), "canceled"),
    (lambda gw: gw.start_policy_change("PA-200001-01", "2030-01-01"), "outside the policy term"),
    (lambda gw: gw.start_policy_change("PA-200001-01", "next week"), "YYYY-MM-DD"),
    (lambda gw: gw.add_vehicle("PA-200001-01", 2024, "Honda", "Civic"), "start_policy_change"),
    (lambda gw: gw.get_policy_view("XX-1"), "not found"),
])
def test_rejections(gw, call, message):
    with pytest.raises(EverywireError, match=message):
        run(call(gw))


def test_invalid_edits(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    with pytest.raises(EverywireError, match="Allowed"):
        run(gw.update_coverage("PA-200001-01", "PACollision", "$9 deductible"))
    with pytest.raises(EverywireError, match="at least one vehicle"):
        run(gw.remove_vehicle("PA-200001-01", "veh-1"))
    with pytest.raises(EverywireError, match="already has open policy change"):
        run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    with pytest.raises(EverywireError, match="no vehicles"):
        run(gw.start_policy_change("HO-200002-01", "2026-12-01"))
        run(gw.add_vehicle("HO-200002-01", 2024, "Honda", "Civic"))


def test_text_summary_mentions_changes(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    v = run(gw.add_vehicle("PA-200001-01", 2024, "Honda", "Civic"))
    text = policy_view_text(v, "Added the vehicle.")
    assert "+ added 2024 Honda Civic" in text and "Not quoted yet" in text


def test_add_driver_lands_in_draft_only(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    v = run(gw.add_driver("PA-200001-01", "Sam", "Smith", "1990-03-01", "s530-0001"))
    added = [x for x in v["draft"]["drivers"] if x["change"] == "added"]
    assert [(x["firstName"], x["licenseNumber"]) for x in added] == [("Sam", "S530-0001")]
    assert [x["firstName"] for x in v["drivers"]] == ["Jane"]  # live policy untouched
    assert "+ added driver Sam Smith" in policy_view_text(v, "Added the driver.")


def test_young_driver_surcharge_and_bind(gw):
    base = run(gw.get_policy_view("PA-200001-01"))["premium"]
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    run(gw.add_driver("PA-200001-01", "Sam", "Smith", "2007-06-15"))  # 19 on the effective date
    q = run(gw.quote_policy_change("PA-200001-01"))["draft"]["quote"]
    assert q["annualPremium"] == base + 350
    v = run(gw.bind_policy_change("PA-200001-01"))
    assert [x["firstName"] for x in v["drivers"]] == ["Jane", "Sam"] and v["premium"] == base + 350


def test_adult_driver_has_no_surcharge(gw):
    base = run(gw.get_policy_view("PA-200001-01"))["premium"]
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    run(gw.add_driver("PA-200001-01", "Sam", "Smith", "1990-03-01"))
    assert run(gw.quote_policy_change("PA-200001-01"))["draft"]["quote"]["annualPremium"] == base


@pytest.mark.parametrize("args, match", [
    (("Sam", "Smith", "12/01/1990"), "YYYY-MM-DD"),
    (("Sam", "", "1990-03-01"), "first and last name"),
    (("Sam", "Smith", "2015-01-01"), "15 or older"),
])
def test_add_driver_validation(gw, args, match):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    with pytest.raises(EverywireError, match=match):
        run(gw.add_driver("PA-200001-01", *args))


def test_add_driver_rejects_non_auto(gw):
    run(gw.start_policy_change("HO-200002-01", "2026-12-01"))
    with pytest.raises(EverywireError, match="no drivers"):
        run(gw.add_driver("HO-200002-01", "Sam", "Smith", "1990-03-01"))


def test_old_state_without_drivers_loads(gw):
    state = gw.to_state()
    for d in state["_details"].values():
        d.pop("drivers")
    restored = MockEverywireClient.from_state(state)
    assert run(restored.get_policy_view("PA-200001-01"))["drivers"] == []


# --------------------------------------------------------------- new business

MARIA = dict(first_name="Maria", last_name="Lopez", company_name=None, email="maria@example.com",
             address_line1="12 Oak St", city="Springfield", state="il", postal_code="62704")


def test_create_person_account(gw):
    acct = run(gw.create_account(**MARIA, phone="217-555-0100"))
    a = acct["attributes"]
    assert a["accountNumber"] == "ACC-10006"
    assert a["primaryAddress"] == {"addressLine1": "12 Oak St", "city": "Springfield",
                                   "state": "IL", "postalCode": "62704"}
    assert a["phone"] == "217-555-0100" and a["emailAddress"] == "maria@example.com"
    found = run(gw.search("/accounts", {"lastName": "Lopez"}))["data"]
    assert [x["attributes"]["accountNumber"] for x in found] == ["ACC-10006"]


def test_create_company_account(gw):
    run(gw.create_account(**MARIA))
    acct = run(gw.create_account(**{**MARIA, "first_name": None, "last_name": None,
                                    "company_name": "Lopez Bakery LLC"}))
    assert acct["attributes"]["accountNumber"] == "ACC-10007"
    assert acct["attributes"]["companyName"] == "Lopez Bakery LLC"


@pytest.mark.parametrize("override, match", [
    ({"first_name": None, "last_name": None}, "name"),
    ({"company_name": "Acme"}, "either"),
    ({"email": "maria.example.com"}, "email"),
    ({"state": "Illinois"}, "2-letter"),
    ({"postal_code": "627"}, "ZIP"),
    ({"city": " "}, "address"),
])
def test_create_account_validation(gw, override, match):
    with pytest.raises(EverywireError, match=match):
        run(gw.create_account(**{**MARIA, **override}))


def test_create_account_accepts_zip_plus_four(gw):
    acct = run(gw.create_account(**{**MARIA, "postal_code": "62704-1234"}))
    assert acct["attributes"]["primaryAddress"]["postalCode"] == "62704-1234"


def test_old_state_without_account_counter_loads(gw):
    state = gw.to_state()
    state.pop("_next_account", None)
    restored = MockEverywireClient.from_state(state)
    assert run(restored.create_account(**MARIA))["attributes"]["accountNumber"] == "ACC-10006"
