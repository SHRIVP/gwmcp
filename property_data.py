"""Free public property data for homeowners quotes (no API keys).

- US Census Geocoder: confirms the address and gives coordinates, state and county.
- FEMA National Flood Hazard Layer: the flood zone at those coordinates.

``lookup_property`` never raises. Each step reports a status, so a slow or
unavailable service only leaves that factor unknown; it never stops a quote.
"""

from __future__ import annotations

import httpx

CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
FEMA_URL = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
CENSUS_SOURCE = "US Census Geocoder"
FEMA_SOURCE = "FEMA National Flood Hazard Layer"
TIMEOUT_SECONDS = 5


def _geocode_result(status: str, match: dict | None = None) -> dict:
    out = {"status": status, "source": CENSUS_SOURCE, "matchedAddress": None,
           "state": None, "county": None, "lat": None, "lon": None}
    if match:
        counties = (match.get("geographies") or {}).get("Counties") or []
        out.update(
            matchedAddress=match.get("matchedAddress"),
            state=(match.get("addressComponents") or {}).get("state"),
            county=counties[0].get("NAME") if counties else None,
            lat=float(match["coordinates"]["y"]),
            lon=float(match["coordinates"]["x"]),
        )
    return out


def _flood_result(status: str, zone: str | None = None, subtype: str | None = None) -> dict:
    return {"status": status, "source": FEMA_SOURCE, "zone": zone, "subtype": subtype}


async def _geocode(http: httpx.AsyncClient, address: str) -> dict:
    try:
        resp = await http.get(CENSUS_URL, params={
            "address": address, "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "format": "json",
        })
        if resp.status_code != 200:
            return _geocode_result("failed")
        matches = resp.json()["result"]["addressMatches"]
        return _geocode_result("ok", matches[0]) if matches else _geocode_result("no_match")
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return _geocode_result("failed")


async def _flood_zone(http: httpx.AsyncClient, lat: float, lon: float) -> dict:
    try:
        resp = await http.get(FEMA_URL, params={
            "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY",
            "returnGeometry": "false", "f": "json",
        })
        body = resp.json() if resp.status_code == 200 else None
        # ArcGIS reports errors with HTTP 200 and an "error" body.
        if not isinstance(body, dict) or "error" in body or "features" not in body:
            return _flood_result("failed")
        if not body["features"]:
            return _flood_result("not_mapped")
        attrs = body["features"][0]["attributes"]
        return _flood_result("ok", attrs.get("FLD_ZONE"), attrs.get("ZONE_SUBTY"))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return _flood_result("failed")


async def lookup_property(address: str, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as http:
        geocode = await _geocode(http, address)
        if geocode["status"] == "ok":
            flood = await _flood_zone(http, geocode["lat"], geocode["lon"])
        else:
            flood = _flood_result("skipped")
    return {"input": address, "geocode": geocode, "flood": flood}


def lookup_failed(result: dict | None) -> bool:
    """True when a step errored or timed out (worth retrying), not for a clean 'no match'."""
    if not result:
        return False
    return result["geocode"]["status"] == "failed" or result["flood"]["status"] == "failed"
