import asyncio

import httpx

from property_data import lookup_failed, lookup_property

ADDRESS = "12 Oak St, Springfield, IL 62704"

CENSUS_OK = {"result": {"addressMatches": [{
    "matchedAddress": "12 OAK ST, SPRINGFIELD, IL, 62704",
    "coordinates": {"x": -89.65, "y": 39.78},
    "addressComponents": {"state": "IL"},
    "geographies": {"Counties": [{"NAME": "Sangamon County"}]},
}]}}
FEMA_AE = {"features": [{"attributes": {"FLD_ZONE": "AE", "ZONE_SUBTY": None}}]}


def run(handler):
    return asyncio.run(lookup_property(ADDRESS, transport=httpx.MockTransport(handler)))


def router(census=None, fema=None, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        body = census if "census" in request.url.host else fema
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body)
    return handler


def test_lookup_ok():
    r = run(router(CENSUS_OK, FEMA_AE))
    assert r["input"] == ADDRESS
    g, f = r["geocode"], r["flood"]
    assert g["status"] == "ok" and g["state"] == "IL" and g["county"] == "Sangamon County"
    assert g["matchedAddress"] == "12 OAK ST, SPRINGFIELD, IL, 62704"
    assert (g["lat"], g["lon"]) == (39.78, -89.65)
    assert g["source"] == "US Census Geocoder"
    assert f["status"] == "ok" and f["zone"] == "AE" and f["source"] == "FEMA National Flood Hazard Layer"
    assert lookup_failed(r) is False


def test_lookup_no_match():
    r = run(router({"result": {"addressMatches": []}}, FEMA_AE))
    assert r["geocode"]["status"] == "no_match"
    assert r["flood"]["status"] == "skipped"
    assert lookup_failed(r) is False


def test_flood_not_mapped():
    r = run(router(CENSUS_OK, {"features": []}))
    assert r["flood"]["status"] == "not_mapped" and r["flood"]["zone"] is None


def test_fema_error_body_is_failed():
    r = run(router(CENSUS_OK, {"error": {"code": 500, "message": "boom"}}))
    assert r["flood"]["status"] == "failed"
    assert lookup_failed(r)


def test_timeout_is_failed():
    r = run(router(httpx.ConnectTimeout("slow"), FEMA_AE))
    assert r["geocode"]["status"] == "failed"
    assert r["flood"]["status"] == "skipped"
    assert lookup_failed(r)


def test_non_200_is_failed():
    def handler(request):
        return httpx.Response(503, text="down")
    assert run(handler)["geocode"]["status"] == "failed"


def test_fema_query_params():
    seen = []
    run(router(CENSUS_OK, FEMA_AE, seen))
    fema = next(r for r in seen if "fema" in r.url.host)
    q = fema.url.params
    assert q["geometry"] == "-89.65,39.78"
    assert q["geometryType"] == "esriGeometryPoint"
    assert q["inSR"] == "4326"
    assert q["outFields"] == "FLD_ZONE,ZONE_SUBTY"
    assert q["returnGeometry"] == "false"
    assert q["f"] == "json"
    census = next(r for r in seen if "census" in r.url.host)
    assert census.url.params["address"] == ADDRESS
    assert census.url.params["benchmark"] == "Public_AR_Current"


def test_lookup_failed_handles_none():
    assert lookup_failed(None) is False
