import asyncio
from datetime import date, timedelta

import pytest

from client import EverywireError
from mock import MockEverywireClient, _one_year_after, home_factors
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


def days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def test_start_submission_creates_draft_policy(gw):
    v = run(gw.start_submission("ACC-10001", "PersonalAuto", days(10), "New car policy"))
    assert v["policyNumber"] == "PA-200007-01"
    assert v["status"] == "submission" and v["premium"] == 0.0
    assert v["expirationDate"] == _one_year_after(date.fromisoformat(days(10))).isoformat()
    d = v["draft"]
    assert d["kind"] == "submission" and d["status"] == "Draft" and d["jobNumber"].startswith("SUB-")
    assert {c["code"]: c["value"] for c in d["coverages"]} == {
        "PALiability": "100/300", "PACollision": "$500 deductible",
        "PAComprehensive": "$500 deductible", "PARoadside": "Not covered"}
    jobs = run(gw.search("/jobs", {"jobType": "Submission", "status": "Draft"}))["data"]
    assert d["jobNumber"] in [j["attributes"]["jobNumber"] for j in jobs]
    assert "New submission" in policy_view_text(v)


def test_homeowners_submission_numbering_and_coverages(gw):
    run(gw.start_submission("ACC-10001", "PersonalAuto", days(10)))
    v = run(gw.start_submission("ACC-10001", "HomeOwners", days(10)))
    assert v["policyNumber"] == "HO-200008-01"
    assert {c["code"]: c["value"] for c in v["draft"]["coverages"]} == {
        "HODwelling": "$350,000", "HODeductible": "$1,000", "HOWaterBackup": "Not covered"}


def test_auto_submission_quote_requires_vehicle_and_driver(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(10)))["policyNumber"]
    with pytest.raises(EverywireError, match="one vehicle and one driver"):
        run(gw.quote_policy_change(num))
    run(gw.add_vehicle(num, 2024, "Honda", "Civic"))
    with pytest.raises(EverywireError, match="one vehicle and one driver"):
        run(gw.quote_policy_change(num))


def test_auto_submission_quote_and_bind(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(0)))["policyNumber"]
    run(gw.add_vehicle(num, 2024, "Honda", "Civic"))
    run(gw.add_driver(num, "Maria", "Lopez", "1985-02-03"))
    q = run(gw.quote_policy_change(num))
    assert q["draft"]["quote"]["annualPremium"] == 688 + 420 + 9 * 12 == 1216.0
    assert q["draft"]["quote"]["changeInCost"] == 1216.0
    text = policy_view_text(q)
    assert "Quoted: annual premium $1,216.00" in text and "change in cost" not in text
    v = run(gw.bind_policy_change(num))
    assert v["policyNumber"] == num and v["status"] == "inForce"
    assert v["premium"] == 1216.0 and v["draft"] is None
    assert [x["model"] for x in v["vehicles"]] == ["Civic"]
    job = next(j for j in v["jobs"] if j["jobType"] == "Submission")
    assert job["status"] == "Bound"
    found = run(gw.search("/policies", {"policyNumber": num}))["data"]
    assert found[0]["attributes"]["status"] == "inForce"


def test_future_submission_binds_scheduled(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(60)))["policyNumber"]
    run(gw.add_vehicle(num, 2024, "Honda", "Civic"))
    run(gw.add_driver(num, "Maria", "Lopez", "1985-02-03"))
    run(gw.quote_policy_change(num))
    assert run(gw.bind_policy_change(num))["status"] == "scheduled"


def test_bound_submission_can_then_be_changed(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(0)))["policyNumber"]
    run(gw.add_vehicle(num, 2024, "Honda", "Civic"))
    run(gw.add_driver(num, "Maria", "Lopez", "1985-02-03"))
    run(gw.quote_policy_change(num))
    run(gw.bind_policy_change(num))
    v = run(gw.start_policy_change(num, days(5)))
    assert v["draft"]["kind"] == "change"


def test_feb_29_term():
    assert _one_year_after(date(2028, 2, 29)) == date(2029, 2, 28)
    assert _one_year_after(date(2026, 12, 1)) == date(2027, 12, 1)


def test_withdrawn_submission(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(10)))["policyNumber"]
    v = run(gw.withdraw_policy_change(num))
    assert v["status"] == "withdrawn" and v["draft"] is None
    assert next(j for j in v["jobs"] if j["jobType"] == "Submission")["status"] == "Withdrawn"
    with pytest.raises(EverywireError, match="no open policy change"):
        run(gw.add_vehicle(num, 2024, "Honda", "Civic"))
    with pytest.raises(EverywireError, match="withdrawn") as e:
        run(gw.start_policy_change(num, days(12)))
    assert e.value.status == 409


def test_cannot_start_change_on_open_submission(gw):
    num = run(gw.start_submission("ACC-10001", "PersonalAuto", days(10)))["policyNumber"]
    with pytest.raises(EverywireError) as e:
        run(gw.start_policy_change(num, days(12)))
    assert e.value.status == 409


@pytest.mark.parametrize("acct, product, eff, match", [
    ("ACC-10001", "CommercialPackage", 10, "PersonalAuto or HomeOwners"),
    ("ACC-99999", "PersonalAuto", 10, "not found"),
    ("ACC-10001", "PersonalAuto", -31, "30 days"),
    ("ACC-10001", "PersonalAuto", 366, "365 days"),
])
def test_start_submission_validation(gw, acct, product, eff, match):
    with pytest.raises(EverywireError, match=match):
        run(gw.start_submission(acct, product, days(eff)))


def test_start_submission_bad_date(gw):
    with pytest.raises(EverywireError, match="YYYY-MM-DD"):
        run(gw.start_submission("ACC-10001", "PersonalAuto", "12/01/2026"))


def test_new_account_submission(gw):
    acct = run(gw.create_account(**MARIA))["attributes"]["accountNumber"]
    v = run(gw.start_submission(acct, "homeowners", days(10)))
    assert v["account"]["name"] == "Maria Lopez" and v["product"]["code"] == "HomeOwners"


def test_policy_change_kind(gw):
    v = run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    assert v["draft"]["kind"] == "change"
    run(gw.add_vehicle("PA-200001-01", 2024, "Honda", "Civic"))
    q = run(gw.quote_policy_change("PA-200001-01"))["draft"]["quote"]
    assert q["changeInCost"] != q["annualPremium"]


def test_old_state_drafts_get_kind(gw):
    run(gw.start_policy_change("PA-200001-01", "2026-12-01"))
    state = gw.to_state()
    for d in state["_drafts"].values():
        d.pop("kind")
    state.pop("_next_policy", None)
    restored = MockEverywireClient.from_state(state)
    assert run(restored.get_policy_view("PA-200001-01"))["draft"]["kind"] == "change"
    assert run(restored.start_submission("ACC-10001", "PersonalAuto", days(1)))["policyNumber"] == "PA-200007-01"


# ------------------------------------------------------- home details / rating

ADDR = "12 Oak St, Springfield, IL 62704"


def lookup_result(address=ADDR, geocode="ok", state="IL", flood="ok", zone="X"):
    ok = geocode == "ok"
    return {
        "input": address,
        "geocode": {"status": geocode, "source": "US Census Geocoder",
                    "matchedAddress": "12 OAK ST, SPRINGFIELD, IL, 62704" if ok else None,
                    "state": state if ok else None, "county": "Sangamon County" if ok else None,
                    "lat": 39.78 if ok else None, "lon": -89.65 if ok else None},
        "flood": {"status": flood if ok else "skipped", "source": "FEMA National Flood Hazard Layer",
                  "zone": zone if ok and flood == "ok" else None, "subtype": None},
    }


class FakeLookup:
    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[str] = []

    async def __call__(self, address):
        self.calls.append(address)
        r = self.results.pop(0) if len(self.results) > 1 else (self.results[0] if self.results else lookup_result())
        return {**r, "input": address}


@pytest.fixture
def fake():
    return FakeLookup(lookup_result())


@pytest.fixture
def hgw(fake):
    return MockEverywireClient(property_lookup=fake)


def ho_submission(client, eff=10):
    return run(client.start_submission("ACC-10001", "HomeOwners", days(eff)))["policyNumber"]


def full_details(client, num):
    return run(client.set_home_details(num, ADDR, 1998, "masonry", 2100, 2019, 2))


def test_home_factors_table():
    home = {"address": ADDR, "yearBuilt": 1998, "constructionType": "masonry", "squareFeet": 2100,
            "roofYear": 2019, "stories": 2, "lookup": lookup_result(state="FL", zone="AE")}
    factors = home_factors(home, date(2026, 12, 1))
    assert [f["name"] for f in factors] == ["Home age", "Construction", "Size", "Roof age", "Stories",
                                            "Flood zone", "State"]
    assert [f["multiplier"] for f in factors] == [1.12, 0.92, 1.025, 0.95, 1.03, 1.35, 1.45]


@pytest.mark.parametrize("zone, mult", [("VE", 1.60), ("A", 1.35), ("AE", 1.35), ("X", 1.0), ("D", 1.0)])
def test_flood_zone_multipliers(zone, mult):
    home = {"address": ADDR, "lookup": lookup_result(zone=zone)}
    assert next(f for f in home_factors(home, date(2026, 12, 1)) if f["name"] == "Flood zone")["multiplier"] == mult


def test_homeowners_submission_quote(hgw):
    num = ho_submission(hgw)
    full_details(hgw, num)
    q = run(hgw.quote_policy_change(num))["draft"]["quote"]
    assert q["annualPremium"] == round(1450 * 1.12 * 0.92 * 1.025 * 0.95 * 1.03 * 1.0 * 1.0, 2)
    assert q["changeInCost"] == q["annualPremium"]
    assert len(q["factors"]) == 7
    text = policy_view_text(run(hgw.get_policy_view(num)))
    assert "Construction: masonry ×0.92" in text
    assert "12 OAK ST, SPRINGFIELD, IL, 62704" in text and "FEMA National Flood Hazard Layer" in text


def test_homeowners_submission_requires_details(hgw):
    num = ho_submission(hgw)
    with pytest.raises(EverywireError, match="address"):
        run(hgw.quote_policy_change(num))
    run(hgw.set_home_details(num, ADDR))
    with pytest.raises(EverywireError) as e:
        run(hgw.quote_policy_change(num))
    for field in ("year built", "construction type", "square footage"):
        assert field in str(e.value)


def test_lookup_runs_once_then_only_on_address_change(hgw, fake):
    num = ho_submission(hgw)
    run(hgw.set_home_details(num, ADDR))
    run(hgw.set_home_details(num, None, 1998))
    run(hgw.set_home_details(num, ADDR, None, "frame"))  # same address: no new lookup
    assert fake.calls == [ADDR]
    run(hgw.set_home_details(num, "14 Oak St, Springfield, IL 62704"))
    assert fake.calls == [ADDR, "14 Oak St, Springfield, IL 62704"]


def test_failed_lookup_retried_on_next_call_and_quote():
    fake = FakeLookup(lookup_result(geocode="failed"), lookup_result(geocode="failed"), lookup_result())
    gw = MockEverywireClient(property_lookup=fake)
    num = ho_submission(gw)
    run(gw.set_home_details(num, ADDR))
    run(gw.set_home_details(num, None, 1998, "frame", 1800))  # retries (still failing)
    assert len(fake.calls) == 2
    v = run(gw.quote_policy_change(num))  # retries again, succeeds
    assert len(fake.calls) == 3
    assert v["draft"]["home"]["lookup"]["geocode"]["status"] == "ok"
    run(gw.quote_policy_change(num))
    assert len(fake.calls) == 3


def test_no_match_not_retried_and_prices_neutral():
    fake = FakeLookup(lookup_result(geocode="no_match"))
    gw = MockEverywireClient(property_lookup=fake)
    num = ho_submission(gw)
    run(gw.set_home_details(num, ADDR, 1998, "masonry", 2100))
    v = run(gw.quote_policy_change(num))
    assert len(fake.calls) == 1
    factors = {f["name"]: f for f in v["draft"]["quote"]["factors"]}
    assert factors["Flood zone"]["multiplier"] == 1.0 and factors["State"]["multiplier"] == 1.0
    assert "address not verified" in policy_view_text(v)


def test_failed_lookup_still_quotes(hgw):
    gw = MockEverywireClient(property_lookup=FakeLookup(lookup_result(geocode="failed")))
    num = ho_submission(gw)
    run(gw.set_home_details(num, ADDR, 1998, "masonry", 2100))
    v = run(gw.quote_policy_change(num))
    factors = {f["name"]: f for f in v["draft"]["quote"]["factors"]}
    assert factors["State"]["detail"] == "unavailable" and v["draft"]["quote"]["annualPremium"] > 0


@pytest.mark.parametrize("given", ["Masonry Veneer", "masonry-veneer", " MASONRY_VENEER "])
def test_construction_type_normalized(hgw, given):
    num = ho_submission(hgw)
    v = run(hgw.set_home_details(num, ADDR, None, given))
    assert v["draft"]["home"]["constructionType"] == "masonry_veneer"


def test_construction_type_unknown(hgw):
    num = ho_submission(hgw)
    with pytest.raises(EverywireError, match="frame, masonry_veneer, masonry, superior"):
        run(hgw.set_home_details(num, ADDR, None, "straw"))


@pytest.mark.parametrize("args, match", [
    ((ADDR, 1700), "year built"),
    ((ADDR, None, None, 50), "square footage"),
    ((ADDR, 1998, None, None, 1990), "roof"),
    ((ADDR, None, None, None, None, 9), "stories"),
    ((None, 1998), "address"),
    ((ADDR, "old"), "year built"),
])
def test_home_details_validation(hgw, args, match):
    num = ho_submission(hgw)
    with pytest.raises(EverywireError, match=match):
        run(hgw.set_home_details(num, *args))


def test_home_details_rejected_on_auto(hgw):
    num = run(hgw.start_submission("ACC-10001", "PersonalAuto", days(10)))["policyNumber"]
    with pytest.raises(EverywireError, match="not a Homeowners policy"):
        run(hgw.set_home_details(num, ADDR))


def test_bound_home_carries_into_policy_change(hgw):
    num = ho_submission(hgw, eff=0)
    full_details(hgw, num)
    run(hgw.quote_policy_change(num))
    bound = run(hgw.bind_policy_change(num))
    assert bound["home"]["yearBuilt"] == 1998 and bound["status"] == "inForce"
    v = run(hgw.start_policy_change(num, days(5)))
    assert v["draft"]["home"] == bound["home"]
    run(hgw.set_home_details(num, None, None, None, None, 2000))  # roof age >20: factor 0.95 → 1.20
    q = run(hgw.quote_policy_change(num))["draft"]["quote"]
    assert q["changeInCost"] != 0 and len(q["factors"]) == 7


def test_seed_homeowners_unchanged(hgw, fake):
    before = run(hgw.get_policy_view("HO-200002-01"))
    assert before["premium"] == 1450.0 and before["home"] is None
    run(hgw.start_policy_change("HO-200002-01", "2026-12-01"))
    run(hgw.update_coverage("HO-200002-01", "HOWaterBackup", "$10,000"))
    q = run(hgw.quote_policy_change("HO-200002-01"))["draft"]["quote"]
    assert q["factors"] == [] and q["annualPremium"] == 1525.0
    assert 0 < q["changeInCost"] < 75
    assert fake.calls == []


def test_submission_text_wording(hgw):
    num = ho_submission(hgw)
    full_details(hgw, num)
    text = policy_view_text(run(hgw.quote_policy_change(num)), "Quoted the policy change.")
    assert "Annual premium: $0.00" not in text and "Annual premium: not issued yet" in text
    assert "(no changes yet)" not in text
    assert text.startswith("Quoted the new submission.")
