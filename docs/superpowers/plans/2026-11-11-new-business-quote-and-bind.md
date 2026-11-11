# New Business Quote and Bind Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a business user create an account, build a Personal Auto or Homeowners submission, quote it (homeowners priced from home details plus Census/FEMA public data) and bind it from the Policy System view.

**Architecture:** A submission is an open draft in the mock's existing `_drafts` map, keyed by a policy number assigned at start, so every existing edit, quote and withdraw tool works on it unchanged. A new `property_data.py` does the two public lookups behind an injectable async callable. `PersistentMockClient` runs lookups *before* its load/apply/save loop and passes the result in. Rating gains a homeowners factor breakdown that applies only to records with home details.

**Tech Stack:** Python 3.10+, `mcp` low-level server, `httpx` (already a dependency), pytest. UI is the single-file `ui/policy_view.html`.

**Spec:** `docs/superpowers/specs/2026-11-11-new-business-quote-and-bind-design.md`

## Global Constraints

- Mock mode only. `EverywireClient.supports_editing` stays `False`.
- Products for new business: `PersonalAuto` and `HomeOwners` only. Any other code is a 400 error.
- Claude can't bind. `bind_policy_change` stays `app_only=True`. No new tool binds.
- Lookups: `httpx` with a **5 second** timeout per request, no API keys. Census: `https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress`. FEMA: `https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query`.
- Tests never touch the network. Every test that reaches a lookup injects a fake or an `httpx.MockTransport`.
- Existing seed policies, their premiums and all existing tests stay unchanged.
- Every new piece of mock state is in `_STATE_FIELDS`. State saved before this change loads through `from_state` with defaults.
- Copy: user-facing text says "Policy System" and "Everywire", never the old product or vendor names.

## Review Focus

1. **The Census geocoder returns zero matches** (a typo or PO box). Expected: the address is stored with status `no_match`, it isn't retried, the view says "address not verified", and the state and flood factors are neutral. Test in Task 1 and Task 4.
2. **FEMA answers HTTP 200 with an `{"error": …}` body**, which ArcGIS does. Expected: treated as `failed` (retried later), not as "not mapped". Test in Task 1.
3. **`start_policy_change` called on a submission or a withdrawn submission.** Expected: refused with a clear 409. Edits after withdraw fail with "no open policy change". Test in Task 3.
4. **The effective date is Feb 29.** Expected: expiration is Feb 28 of the next year, not a crash. Test in Task 3.
5. **Construction type given loosely**, like `"Masonry Veneer"` or `"masonry-veneer"`. Expected: normalized to `masonry_veneer`. Unknown values are a 400 error that lists the allowed ones. Test in Task 4.

---

### Task 0: Baseline commit

The branch has uncommitted work (policy-editing UI, the rename, `add_driver`). Commit it first so each task below gets a clean diff.

- [ ] **Step 1:** Run `uv run pytest -q`. Expected: `27 passed`.
- [ ] **Step 2:** Commit everything except `.env` files. Check with `git status` that no secrets are staged.

```bash
git add -A && git status --short
git commit -m "Policy editing UI, Everywire/Policy System rename, add_driver"
```

---

### Task 1: Public property data lookups

**Files:**
- Create: `property_data.py`
- Test: `tests/test_property_data.py`

**Interfaces:**
- Produces: `async def lookup_property(address: str, transport: httpx.AsyncBaseTransport | None = None) -> dict`. It never raises; it returns:

```python
{
  "input": "12 Oak St, Springfield, IL 62704",
  "geocode": {"status": "ok" | "no_match" | "failed", "source": "US Census Geocoder",
              "matchedAddress": str | None, "state": str | None,   # 2-letter, e.g. "IL"
              "county": str | None, "lat": float | None, "lon": float | None},
  "flood":   {"status": "ok" | "not_mapped" | "failed" | "skipped", "source": "FEMA National Flood Hazard Layer",
              "zone": str | None, "subtype": str | None},
}
```

- Produces: `def lookup_failed(result: dict | None) -> bool`. True when `geocode.status == "failed"` or `flood.status == "failed"`.
- The flood lookup is `skipped` unless the geocode status is `ok`.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport` handlers that route on `request.url.host`:
  - `test_lookup_ok`: Census JSON with one `addressMatches` entry (`matchedAddress`, `coordinates.x/y`, `addressComponents.state="IL"`, `geographies.Counties[0].NAME="Sangamon County"`), and FEMA `{"features":[{"attributes":{"FLD_ZONE":"AE","ZONE_SUBTY":null}}]}`. Assert `geocode.status=="ok"`, `state=="IL"`, `county=="Sangamon County"`, `flood.zone=="AE"`, and `lookup_failed(r) is False`.
  - `test_lookup_no_match`: Census returns `addressMatches: []`. Assert `geocode.status=="no_match"`, `flood.status=="skipped"`, and `lookup_failed(r) is False`.
  - `test_flood_not_mapped`: FEMA returns `{"features": []}`. Assert `flood.status=="not_mapped"`.
  - `test_fema_error_body_is_failed` (Review Focus 2): FEMA returns 200 with `{"error":{"code":500}}`. Assert `flood.status=="failed"` and `lookup_failed(r)`.
  - `test_timeout_is_failed`: the Census handler raises `httpx.ConnectTimeout`. Assert `geocode.status=="failed"` and that no exception escapes.
  - `test_fema_query_params`: capture the FEMA request. Assert it sends `geometry=f"{lon},{lat}"`, `geometryType=esriGeometryPoint`, `inSR=4326`, `outFields=FLD_ZONE,ZONE_SUBTY`, `returnGeometry=false` and `f=json`.
- [ ] **Step 2:** Run `uv run pytest tests/test_property_data.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement `lookup_property` and `lookup_failed`.** Census params: `address`, `benchmark=Public_AR_Current`, `vintage=Current_Current`, `format=json`. Use the first match. Any `httpx.HTTPError`, non-200 response, or JSON/key error marks that step `failed`. Use one `httpx.AsyncClient(transport=transport, timeout=5)`.
- [ ] **Step 4:** Run `uv run pytest tests/test_property_data.py -v`. Expected: PASS.
- [ ] **Step 5: Commit** with `git add property_data.py tests/test_property_data.py && git commit -m "Add Census and FEMA property lookups"`.

---

### Task 2: Create accounts

**Files:**
- Modify: `mock.py` (new method, `_next_account` counter, `_STATE_FIELDS`)
- Modify: `state_store.py` (passthrough)
- Modify: `server.py` (tool, dispatch, `_fmt_account` shows phone and address)
- Test: `tests/test_mock.py`, `tests/test_state_store.py`

**Interfaces:**
- Produces: `async def create_account(self, first_name: str | None, last_name: str | None, company_name: str | None, email: str, address_line1: str, city: str, state: str, postal_code: str, phone: str | None = None) -> dict`. It returns the new account item in the same `{"id", "attributes"}` shape as `ACCOUNTS`. Attributes add `phone` and `primaryAddress: {"addressLine1", "city", "state", "postalCode"}`.
- Account numbers are `ACC-{n}`. `self._next_account` starts at `10006`.
- Tool `create_account` (not a UI tool, `read_only=False`). Required: `email`, `addressLine1`, `city`, `state` and `postalCode`. Also `firstName`/`lastName` or `companyName`. It returns text: `"Created account ACC-10006."` plus `_fmt_account(...)`.

- [ ] **Step 1: Write failing tests** in `tests/test_mock.py`:
  - `test_create_person_account`: create Maria Lopez. Assert the number is `"ACC-10006"` and that `search("/accounts", {"lastName": "Lopez"})` finds it.
  - `test_create_company_account`: a company-only account works. A second account gets `ACC-10007`.
  - `test_create_account_validation`, parametrized. Each case gets a `EverywireError` whose message contains the given text:
    - no names → `"name"`
    - both a person's name and a company → `"either"`
    - an email without `@` → `"email"`
    - state `"Illinois"` → `"2-letter"`
    - postal code `"627"` → `"ZIP"`
  - `test_old_state_without_account_counter_loads`: pop `_next_account` from `to_state()`. After `from_state`, the next account is still `ACC-10006`.
- [ ] **Step 2:** Run `uv run pytest tests/test_mock.py -v -k account`. Expected: FAIL.
- [ ] **Step 3: Implement `create_account`.** Uppercase the state and strip every field. Accept ZIP+4. Store the email as `emailAddress`. Add `_next_account` to `_STATE_FIELDS`. Add the `PersistentMockClient.create_account` passthrough (write). Add the server tool and a `name == "create_account"` branch in `call_tool`; that branch checks `supports_editing` the same way `_call_edit_tool` does. Extend `_fmt_account` with `Phone:` and `Address:` lines when present.
- [ ] **Step 4:** Add a test to `tests/test_state_store.py`, `test_create_account_persists`, following that file's existing fake-store pattern. Create through one `PersistentMockClient`, then find the account through a fresh one. Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** with `git commit -am "Add create_account tool"` (plus `git add` for any new files).

---

### Task 3: Submissions: start, quote rules, bind, withdraw

**Files:**
- Modify: `mock.py`, `views.py`, `state_store.py`, `server.py`
- Test: `tests/test_mock.py`, `tests/test_http.py`

**Interfaces:**
- Consumes: Task 2's `create_account`.
- Produces: `async def start_submission(self, account_number: str, product_code: str, effective_date: str, description: str = "") -> dict` (a policy view). It creates:
  - A policy record with status `"submission"`, product `{"name": "Personal Auto"|"Homeowners", "code": ...}`, `effectiveDate` and `expirationDate = effective + 1 year` (Feb 29 → Feb 28).
  - Its number: `PA-{n}-01` / `HO-{n}-01`, using `self._next_policy`, which starts at `200007`.
  - `_details[num]` = `{"vehicles": [], "drivers": [], "coverages": <standard options: the 0-adjustment value of each of the product's COVERAGE_CATALOG codes>, "home": None, "premium": 0.0, "version": 1}`.
  - `_drafts[num]`, shaped like a policy change, plus `"kind": "submission"` and `"home": None`. Policy changes get `"kind": "change"`.
  - A job `SUB-{_next_job}`, with `jobType` `"Submission"` and status `"Draft"`.
- Effective date rule: no more than 30 days before today and no more than 365 days after.
- The coverage codes for each product are the existing `PA*` and `HO*` codes in `COVERAGE_CATALOG`. Give `COVERAGE_CATALOG` entries a product prefix lookup: `PersonalAuto → "PA"`, `HomeOwners → "HO"`.
- `quote_policy_change` on a submission:
  - Personal Auto needs at least one vehicle and at least one driver. Error: `"Add at least one vehicle and one driver before quoting."`
  - The quote's `changeInCost` equals `annualPremium` (the full term).
- `bind_policy_change` on a submission sets the policy status: `"inForce"` if the effective date ≤ today, otherwise `"scheduled"`. The job becomes `"Bound"`. The policy number is unchanged.
- `withdraw_policy_change` on a submission sets the policy status to `"withdrawn"` and the job to `"Withdrawn"`.
- `start_policy_change` refuses `submission` and `withdrawn` policies through the existing `EDITABLE_STATUSES` check.
- The view's `draft` gains `"kind"`. `policy_view_text` heads a submission with `New submission {jobNumber} · {status} · effective {date}`. A quoted submission prints `Quoted: annual premium $X`, with no change-in-cost line.
- Tool `start_submission`: `ui=True`. Required: `accountNumber`, `productCode` (enum `["PersonalAuto","HomeOwners"]`), `effectiveDate`. Optional: `description`. Headline: `"Started a new submission."` Its description says to call `create_account` first for a new customer.
- Update the descriptions of the quote, withdraw and bind tools to say "policy change or new submission". Add `"submission"` and `"withdrawn"` to the `search_policies` status enum.

- [ ] **Step 1: Write failing tests** in `tests/test_mock.py`. Effective dates are computed from `date.today()` so the tests don't go stale.
  - `test_start_submission_creates_draft_policy`: on ACC-10001 Personal Auto, assert:
    - the number is `"PA-200007-01"`
    - `v["status"] == "submission"`
    - `v["draft"]["kind"] == "submission"`
    - the draft's coverage values are `{"PALiability": "100/300", "PACollision": "$500 deductible", "PAComprehensive": "$500 deductible", "PARoadside": "Not covered"}`
    - `search("/jobs", {"jobType": "Submission", "status": "Draft"})` includes the new `SUB-` job
  - `test_auto_submission_quote_requires_vehicle_and_driver`: quoting an empty submission → error containing `"one vehicle and one driver"`.
  - `test_auto_submission_quote_and_bind`: add a 2024 Honda Civic and a driver born in 1985, then quote. Assert `annualPremium == 688 + 420 + 9*12 == 1216.0` and `changeInCost == annualPremium`. Bind with the effective date set to today. Assert status `"inForce"`, the same policy number, premium `1216.0`, no draft, and the job `"Bound"`.
  - `test_future_submission_binds_scheduled`: effective date today + 60 → `"scheduled"`.
  - `test_feb_29_term` (Review Focus 4): call the private term helper `_one_year_after(date(2028, 2, 29)) == date(2029, 2, 28)`.
  - `test_withdrawn_submission` (Review Focus 3): after withdraw, status is `"withdrawn"`. `add_vehicle` raises a match on `"no open policy change"`. `start_policy_change` raises a 409 match on `"withdrawn"`.
  - `test_cannot_start_change_on_open_submission` (Review Focus 3): `start_policy_change` on a `submission` policy → 409.
  - `test_start_submission_validation`, parametrized:
    - product `"CommercialPackage"` → `"PersonalAuto or HomeOwners"`
    - unknown account → `"not found"`
    - effective date today − 31 → `"30 days"`
    - today + 366 → `"365 days"`
  - `test_policy_change_still_quotes_change_in_cost`: the existing behavior on PA-200001-01 is unchanged, and the draft has `kind == "change"`.
- [ ] **Step 2:** Run `uv run pytest tests/test_mock.py -v`. Expected: the new tests FAIL and the old ones PASS.
- [ ] **Step 3: Implement it** in `mock.py`, `views.py` and the server tool. Add `_next_policy` to `_STATE_FIELDS`. In `from_state`, `setdefault("kind", "change")` and `setdefault("home", None)` on drafts, and `setdefault("home", None)` on details. Add the `PersistentMockClient.start_submission` passthrough (write).
- [ ] **Step 4:** Add `test_new_business_auto_over_http` to `tests/test_http.py`. The steps are `create_account` → `start_submission` (PersonalAuto, today + 10) → `add_vehicle` → `add_driver` → `quote_policy_change`. Assert:
  - the quote result's `structuredContent["draft"]["kind"] == "submission"`
  - the quote has an `annualPremium` greater than 0
  - `tools["start_submission"].meta["ui"]["resourceUri"]` is the view URI
- [ ] **Step 5:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 6: Commit** with `git commit -am "Add new-business submissions for Personal Auto and Homeowners"`.

---

### Task 4: Home details and homeowners rating

**Files:**
- Modify: `mock.py`, `views.py`, `state_store.py`, `server.py`
- Test: `tests/test_mock.py`, `tests/test_state_store.py`

**Interfaces:**
- Consumes: Task 1's `lookup_property` and `lookup_failed`. Task 3's draft `kind` and `home`.
- `MockEverywireClient.__init__(self, property_lookup=None)`. `self.property_lookup` is an async `(address) -> dict` and defaults to `property_data.lookup_property`. `from_state(state, property_lookup=None)` forwards it. It is not part of the persisted state.
- Produces: `async def set_home_details(self, policy_number, address=None, year_built=None, construction_type=None, square_feet=None, roof_year=None, stories=None, lookup: dict | None = None) -> dict` (a view).
  - It works on any open draft (a submission or a change) on a `HomeOwners` policy. On other products it's a 400 error containing `"not a Homeowners policy"`.
  - `None` arguments leave fields unchanged. The first call must include `address`.
  - Validation errors:
    - year built: 1800 to the current year
    - square feet: 300 to 20000
    - roof year: from year built to the current year
    - stories: 1 to 4
    - construction type: one of `frame | masonry_veneer | masonry | superior`, normalized by lowercasing and turning spaces and hyphens into `_`. An unknown value's error lists the allowed values.
  - Lookups: if `lookup` is None, and the address is new or changed or `lookup_failed(stored)`, it awaits `self.property_lookup(address)`.
  - The draft's `home` becomes `{"address", "yearBuilt", "constructionType", "squareFeet", "roofYear", "stories", "lookup"}`.
- Produces: `async def home_lookup_needed(self, policy_number: str, address: str | None) -> str | None`. It's read-only and returns the address that would be looked up, or None.
- `quote_policy_change(self, policy_number, lookup: dict | None = None)`: if the draft has `home` and `lookup_failed(home["lookup"])`, it retries (using the given `lookup` or `self.property_lookup`) before rating.
- A Homeowners **submission** quote needs `address`, `yearBuilt`, `constructionType` and `squareFeet`. The error lists each missing field by display name, for example `"Missing home details: year built, square footage."`
- Produces: `def home_factors(home: dict, as_of: date) -> list[dict]` in `mock.py`. Each entry is `{"name": str, "detail": str, "multiplier": float}`, in this order:

| name | rule (age = as_of.year − year) | multiplier |
|---|---|---|
| Home age | ≤10 / ≤25 / ≤50 / >50 | 0.90 / 1.00 / 1.12 / 1.25 |
| Construction | frame / masonry_veneer / masonry / superior | 1.10 / 1.00 / 0.92 / 0.85 |
| Size | `min(1.6, max(0.75, 0.5 + 0.5 * sqft / 2000))`, rounded to 3 decimals | — |
| Roof age | missing / ≤10 / ≤20 / >20 | 1.00 / 0.95 / 1.05 / 1.20 |
| Stories | missing or 1 / 2 / 3+ | 1.00 / 1.03 / 1.08 |
| Flood zone | zone starts with V / starts with A / anything else or unknown | 1.60 / 1.35 / 1.00 |
| State | `{"FL":1.45,"LA":1.40,"TX":1.30,"OK":1.25,"CA":1.15,"CO":1.10,"NY":1.05,"OH":0.90,"ID":0.85}`, else (or unknown) | 1.00 |

  When a value is unknown or the lookup failed, `detail` says so (`"unavailable"`, `"address not verified"`, `"not given"`).
- `rate(...)` gains a `home: dict | None = None` keyword. For `HomeOwners` with `home`: `BASE_PREMIUM × ∏ multipliers + coverage adjustments`, rounded to 2 decimals. Without `home`, the result is unchanged. The quote stores `quote["factors"] = home_factors(...)` when home is present, and `[]` otherwise.
- `start_policy_change` copies `_details[num]["home"]` into the draft. `bind_policy_change` copies `draft["home"]` back.
- `build_policy_view`: `view["home"]` (the live policy) and `view["draft"]["home"]` (proposed), both passed through as is. `policy_view_text`:
  - prints a `Home:` block: the address as entered and as matched; county and state; flood zone; and each detail with its source (`US Census Geocoder` / `FEMA National Flood Hazard Layer`)
  - prints each quote factor as `  {name}: {detail} ×{multiplier}`
  - when the lookup status is `no_match`, prints `address not verified`
- `PersistentMockClient.set_home_details(*a)` and `.quote_policy_change(*a)`:
  1. `addr = await self._run("home_lookup_needed", a[0], <address arg or None>, write=False)`
  2. if `addr`, `lookup = await property_data.lookup_property(addr)` (outside `_run`)
  3. `await self._run(<method>, *args, lookup=lookup, write=True)`

  So `_run` must forward keyword arguments. The persistent client takes `property_lookup` in its constructor for tests.
- Tool `set_home_details`: `ui=True`. Required: `policyNumber`. Its properties match the parameters in camelCase (`yearBuilt`, `constructionType` with an enum, `squareFeet`, `roofYear`, `stories`, `address` as a one-line string). Headline: `"Updated the home details."` The description must tell Claude: "If you take any value from a web search rather than from the user, say where it came from and ask the user to confirm it before quoting."
- Add `"set_home_details"` to the `EDIT_DISPATCH` argument order: `["policyNumber","address","yearBuilt","constructionType","squareFeet","roofYear","stories"]`.

- [ ] **Step 1: Write failing tests** in `tests/test_mock.py`. Use a fixture `fake_lookup` that records calls and returns a configurable dict in the Task 1 shape (default: geocode ok, state `"IL"`, flood zone `"X"`).
  - `test_home_factors_table`: built 1998, masonry, 2100 sq ft, roof 2019, 2 stories, zone AE, state FL, `as_of=date(2026,12,1)`. Assert the multipliers are `[1.12, 0.92, 1.025, 0.95, 1.03, 1.35, 1.45]`.
  - `test_homeowners_submission_quote`: start HO, set details as above with the fake (IL, X), then quote. Assert `annualPremium == round(1450 * 1.12*0.92*1.025*0.95*1.03*1.0*1.0, 2)`, plus 0 for the standard coverages. Assert `len(quote["factors"]) == 7`.
  - `test_homeowners_submission_requires_details`: with only an address set, quoting gives an error containing `"year built"`, `"construction type"` and `"square footage"`.
  - `test_lookup_runs_once_then_only_on_address_change`: set the address, then set the year built → the fake is called once. Change the address → called twice.
  - `test_failed_lookup_retried_on_next_call_and_quote`: the fake first returns geocode `failed`. The next `set_home_details` retries, and so does `quote_policy_change`. Assert the call counts. A `no_match` result is not retried.
  - `test_no_match_prices_neutral` (Review Focus 1): with geocode `no_match`, the flood and state factors are `1.0`, and `policy_view_text` contains `"address not verified"`.
  - `test_construction_type_normalized` (Review Focus 5): `"Masonry Veneer"` and `"masonry-veneer"` → `masonry_veneer`. `"straw"` → an error containing `"frame, masonry_veneer, masonry, superior"`.
  - `test_home_details_validation`, parametrized:
    - year built 1700
    - square feet 50
    - roof year before year built
    - stories 9
    - first call without an address → `"address"`
  - `test_home_details_rejected_on_auto`: on a PA submission → `"not a Homeowners policy"`.
  - `test_bound_home_carries_into_policy_change`: bind the HO submission, then `start_policy_change`. The draft has the same `home`. Set roof year 2025, then quote. The change in cost is non-zero and the quote lists the factors.
  - `test_seed_homeowners_unchanged`: HO-200002-01's premium is unchanged. A change on it updates `HOWaterBackup` to `"$10,000"`, quotes, and gives `factors == []`. Its `changeInCost` is computed as today.
- [ ] **Step 2:** In `tests/test_state_store.py`, add `test_persistent_lookup_runs_outside_run`. Use a fake lookup that asserts it isn't called while a `_run` is in progress: set a flag in a wrapped `store.load`/`save`, or count that the lookup ran exactly once even when the first `save` is forced to fail and retry. Also add `test_home_state_round_trips`.
- [ ] **Step 3:** Run `uv run pytest -q`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement it** as specified in the Interfaces above.
- [ ] **Step 5:** Run `uv run pytest -q`. Expected: all pass, and the network is never touched. Check that `grep -n "lookup_property" tests/` shows it imported only in `test_property_data.py`.
- [ ] **Step 6: Commit** with `git commit -am "Add home details and homeowners factor rating"`.

---

### Task 5: View, preview host and README

**Files:**
- Modify: `ui/policy_view.html`, `dev/preview_host.html`, `README.md`

**Interfaces:**
- Consumes: the view fields from Tasks 3 and 4: `draft.kind`, `home`, `draft.home`, `quote.factors`, and the statuses `submission` and `withdrawn`.

- [ ] **Step 1: Update the view.**
  - Add the status labels `submission: "Submission"` and `withdrawn: "Withdrawn"`.
  - When `d.kind === "submission"`:
    - the change card title is `New submission {jobNumber}`
    - the premium line is `Annual premium {money(q.annualPremium)}`, with no strike-through or change line
    - the Bind button reads `Bind new policy`, and the confirm step reads `Confirm: bind {money(annualPremium)}`
    - the bind follow-up text is `Bound. {policyNumber} is now a policy.`
    - the withdraw text is `Submission withdrawn.`
  - Add a **Home** section for `HomeOwners` that shows `d?.home || v.home`:
    - the matched address, or the entered one with an "address not verified" tag
    - county and state, and the flood zone, each with a muted source line
    - year built, construction, square footage, roof year and stories
    - "No home details yet" when there are none
  - When `q.factors.length`, list each factor as `{name} · {detail} · ×{multiplier}` under the premium.
  - The empty-policy hint for a submission is `Ask Claude to add vehicles and drivers` (auto) or `Ask Claude to add the home's details` (homeowners).
- [ ] **Step 2: Update the preview host.** Add a "New business" button group with scripts:
  - `newacct` → `create_account` (Maria Lopez, `12 Oak St`, Springfield, IL, 62704)
  - `newho` → `start_submission` (that account, HomeOwners, a date 14 days ahead computed in JS)
  - `home` → `set_home_details`, with the address `"12 Oak St, Springfield, IL 62704"`, 1998, masonry, 2100, roof year 2019, 2 stories
  - `newquote` → `quote_policy_change`

  The scripts use the policy and account numbers returned by the previous calls. Parse `ACC-\d+` and `HO-\d+-01` from the result text and keep them in variables. Note on the page that "Home details call public Census/FEMA services".
- [ ] **Step 3: Update the README.**
  - Add `create_account`, `start_submission` and `set_home_details` to the tools table.
  - Add a "How new business works" section: the five steps, the Census/FEMA sources, the factor list, and that a failed lookup is priced as neutral and retried.
  - Add an example prompt: *"New customer Maria Lopez … quote home insurance from Dec 1"*.
  - State that this is demo pricing, not filed rates.
- [ ] **Step 4: Verify.**
  - Check the view's script parses: extract the module script with Python and run `node --check` on it, as was done for `add_driver`. Expected: no output, exit 0.
  - Run `uv run pytest -q`. Expected: all pass.
  - Start `GW_MOCK=true GW_TRANSPORT=http uv run python server.py`, open `http://localhost:8000/preview`, and run the four new-business buttons in order. Expected: the view shows the submission, the Home section with sources, then a quote with 7 factors. This step makes real Census/FEMA calls. If they're unreachable from the machine, expect "unavailable" details and a quote that still works.
- [ ] **Step 5: Commit** with `git commit -am "Show submissions and home details in the Policy System view; document new business"`.

---

### Task 6: Final check

- [ ] **Step 1:** Run `uv run pytest -q`. Expected: all pass.
- [ ] **Step 2:** Search the repo for the old product and vendor names. Expected: none found.
- [ ] **Step 3:** Request the whole-branch review (superpowers:requesting-code-review), then superpowers:finishing-a-development-branch.
