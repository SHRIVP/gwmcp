# New business: quote and bind for new accounts

Status: draft for review · 2026-11-11

## Intent

Everywire is a (fictional) insurance company. A business user, someone who would
normally work in a Guidewire-style policy system, wants to sell new business from
inside Claude. That means creating an account for a new customer, building a
submission, quoting it and binding it into an in-force policy.

Today the server can only search existing records and edit existing policies through
policy changes (`start_policy_change` → edit → `quote_policy_change` → Bind in the
view). This adds the same kind of flow for **new** policies.

**This is a demo.** It runs on the mock client (`GW_MOCK=true`). Prices must be
believable and explainable, not real filed rates. The live `EverywireClient` stays
read-only (`supports_editing = False`).

## Goals

1. Create a new account (person or company) from chat.
2. Start a **submission** for that account, or for an existing one, for one of two
   products: **Personal Auto** or **Homeowners**.
3. Build the submission with the tools the user already knows:
   - Auto: `add_vehicle`, `remove_vehicle`, `add_driver`, `update_coverage`.
   - Homeowners: a new tool for home details, plus `update_coverage`.
4. For Homeowners, enter the property address and home details, and enrich them
   automatically with **free public data**:
   - **US Census Geocoder**: confirms the address is real and returns the matched
     address, coordinates, state and county. No API key needed.
   - **FEMA National Flood Hazard Layer**: returns the flood zone at those
     coordinates (public ArcGIS REST service, no key needed).
5. Quote the submission. Homeowners pricing uses the home details and the public
   data, and the quote shows **which factors raised or lowered the price**.
6. The user binds by clicking **Bind** in the Policy System view. Claude still can't
   bind (same `app_only` rule as `bind_policy_change`). Binding creates an in-force
   (or scheduled) policy and a Bound `Submission` job.
7. The Policy System view shows a submission as it's built, the same way it shows a
   policy change today.

## Non-goals

- Commercial Package new business.
- Real filed rates, underwriting rules, referrals or declines.
- Billing, payment plans, policy documents and ID cards.
- New-business support in the live `EverywireClient`.
- Using public data for auto pricing. Auto pricing stays as it is today: vehicles,
  young drivers and coverages.
- Paid or keyed property-data services (ATTOM, CoreLogic, Zillow and the like).
- A `remove_driver` tool. The user said it isn't needed.

## Anti-goals

These would make the work a failure even if it technically ran:

- **The demo stops when a public lookup is slow or down.** Lookups must time out
  quickly (a few seconds). If one fails, the submission still quotes, with that
  factor left neutral and the view saying "flood zone unavailable" or "address not
  verified".
- **Claude can bind.** Binding stays a human click.
- **The price can't be explained.** Every homeowners factor must appear in the quote
  breakdown, both in the text Claude reads and in the view.
- **Tests call the real network.** The public lookups must be swappable, and tests
  must never call Census or FEMA.
- **Home details are made up silently.** If Claude fills in year built or square
  footage from a web search, the tool description must tell it to say where the
  number came from and ask the user to confirm it before quoting.

## The flow, as the user experiences it

1. *"New customer: Maria Lopez, maria@example.com, 12 Oak St, Springfield IL 62704.
   She wants home insurance from Dec 1."*
2. Claude calls `create_account`, then `start_submission` (Homeowners, effective
   2026-12-01). The view opens, showing a new submission in Draft.
3. Claude calls the home-details tool with the address and whatever details the user
   gave. The server geocodes the address and looks up the flood zone. The view shows
   the matched address, county, flood zone and the source of each.
4. The user adds details ("built 1998, brick, 2,100 sq ft, roof replaced 2019") and
   picks coverages. Claude calls the edit tools, and each call updates the view.
5. Claude calls `quote_policy_change` (or a submission equivalent; builder's call).
   The view shows the annual premium and a line per factor, for example
   "Construction: masonry, −8%" or "Flood zone AE, +35%".
6. The user clicks **Bind**, then confirms. The policy goes in force under the same
   policy number it had as a submission.

Auto is the same, but without home details. Before it can be quoted, an auto
submission needs at least one vehicle and at least one driver.

## Decisions

The user decided:

- Products: Personal Auto and Homeowners.
- Homeowners: the user enters the address and home details; the system prices it as
  well as it can using public data.
- This is a demo, not real pricing.
- Binding works as it does today: the user clicks Bind in the view.

My calls (the user hasn't confirmed these):

- **Accounts.** `create_account` takes either first and last name or a company name,
  plus email, phone (optional) and a mailing address. The account address is stored
  but not verified; only the property address on a homeowners submission is
  geocoded. It returns a new `ACC-…` number. New accounts show up in
  `search_accounts`.
- **Policy numbers.** The policy number (`PA-…` / `HO-…`) is assigned when the
  submission starts and **does not change at bind**. Binding only changes the status,
  and so every existing edit tool keeps working keyed on
  `policyNumber`. The submission's job number is `SUB-…`. Until bind, the policy's
  status is a non-in-force status (for example `submission`). It doesn't count as
  in force anywhere, and `start_policy_change` must refuse it.
- **Default coverages.** A new submission starts with each product's standard options
  (the ones with a 0 adjustment in `COVERAGE_CATALOG`).
- **Home details.** Address (required); year built, construction type (frame,
  masonry, masonry veneer, superior/fire-resistive), square footage, roof year,
  stories. Calling the tool again updates the fields given and keeps the rest. If the
  address changes, the lookups run again.
- **Quote requirements.** Homeowners needs an address, year built, construction type
  and square footage. The quote fails with a clear message listing what's missing.
  Roof year and stories are optional; if missing, their factor is neutral.
- **Rating shape.** `base premium × product of factors`, plus the coverage
  adjustments that already exist. The factors are home age, construction, size, roof
  age, flood zone (A/V zones up, X down or neutral, unknown neutral) and state (a
  small table, other states 1.0). The exact numbers are the builder's call; keep them
  plausible, for example a total spread of roughly 0.6× to 2.5×.
- **Where lookup results live.** Successful results are stored on the submission, so
  quoting again doesn't call the network again. An address change triggers new
  lookups. A lookup that **failed** (a timeout or error, as opposed to a clean "no
  match") is stored as failed and retried on the next home-details call and on quote,
  so one bad moment doesn't leave a demo submission unverified for good. A clean "no
  match" or "outside mapped flood areas" is a real result, isn't retried, and is
  priced as neutral.
- **Home details on existing policies.** Home details and the new factor pricing go
  with the policy. On bind, the details and lookup results are copied onto the policy,
  and a later policy change copies them into its draft, the same way it copies
  vehicles today. The home-details tool also works inside a policy change on a
  Homeowners policy (for example "roof replaced in 2025"). Factor pricing applies to
  any Homeowners record that has home details. Records without them, such as the seed
  policies `HO-200002-01` and `HO-200005-01`, keep today's pricing (base plus coverage
  adjustments), and the quote rule doesn't apply to them. So existing policies and
  premiums stay the same, and changes on them still quote. The rule that a quote
  needs an address, year built, construction type and square footage applies to
  submissions only.
- **Bind result.** The policy goes `inForce` if the effective date is today or
  earlier, `scheduled` if it's later. The `SUB-…` job becomes Bound, and the policy
  gets a one-year term.

## Constraints

- **The mock now makes network calls.** Census and FEMA are called only when home
  details are set. The calls use `httpx` (already a dependency) with short timeouts.
- **Persistence.** With Redis (`state_store.PersistentMockClient`), every new piece of
  state must survive `to_state` / `from_state`: new accounts, submissions, home
  details, lookup results and counters. State saved before this change must still
  load, as it does for drivers today.
- **Redis writes.** `PersistentMockClient._run` doesn't lock. It loads the state, runs
  the method, and saves only if nothing else saved in the meantime, retrying up to 5
  times. Running lookups inside `_run` would repeat them on every retry and make
  clashes more likely. So do the network lookups **before** `_run`, then apply their
  results inside it.
- **Vercel.** The server runs as serverless functions there. Outbound HTTPS to
  `geocoding.geo.census.gov` and `hazards.fema.gov` must work from those functions.
- **Existing behavior.** Existing tools, tests and the policy-change flow keep
  working. Existing policies and premiums stay the same.

## What's left to the builder

- Tool names and exact parameters. Possible names: `create_account`,
  `start_submission`, `set_home_details`. Also whether quote, withdraw and bind reuse
  the existing `*_policy_change` tools or get submission versions. Whatever is
  clearest for Claude to pick correctly.
- How the view lays out the submission header, home details, data sources and
  premium breakdown, within the existing view's style.
- The exact rating numbers and the state table.
- The exact Census and FEMA endpoints and parameters, and how to parse them.
- How the network lookups are injected so tests can fake them.
- README updates and new preview-host buttons.
