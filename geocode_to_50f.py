"""
geocode_to_50f.py
=================
Step 1 of the SWIFT MT Field 50F pipeline.

Takes a raw, unformatted address string, calls the Google Maps Geocoding API
to parse it into structured components, then maps those components to an
AddressInput object ready to be passed into format_field_50f() from
swift_mt50f.py.

Usage
-----
    from geocode_to_50f import geocode_address
    from swift_mt50f import format_field_50f

    addr_input = geocode_address(
        raw_address = "Mr. President 1600 Pennsylvania Ave NW Washington, DC 20500, USA",
        api_key     = "YOUR_GOOGLE_MAPS_API_KEY",
    )
    result = format_field_50f(addr_input)
    print(result)

Requirements
------------
    pip install requests
"""

import re
import requests
from typing import Optional

# Import the AddressInput dataclass from the companion script.
# Both files must be in the same directory (or on PYTHONPATH).
from swift_mt50f import AddressInput


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEOCODING_URL = "https://maps.googleapis.com/maps/api/geocode/json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_component(components: list, *types: str, use_short: bool = False) -> Optional[str]:
    """
    Extract a single value from a Google address_components list.

    Parameters
    ----------
    components : list
        The address_components array from the Google Geocoding response.
    *types : str
        One or more Google component type strings to match (first match wins).
    use_short : bool
        If True, return short_name (e.g. "US"); otherwise return long_name.
    """
    for component in components:
        for t in types:
            if t in component.get("types", []):
                return component["short_name"] if use_short else component["long_name"]
    return None


def _extract_name_from_raw(raw_address: str) -> tuple[str, str]:
    """
    Attempt to split a raw address string into (name, address_remainder).

    Strategy: the name is assumed to be any text that precedes the first
    token that looks like a street number (one or more digits optionally
    followed by a letter, e.g. "1600", "10A").

    Examples
    --------
    "Mr. President 1600 Pennsylvania Ave NW Washington DC"
        → ("Mr. President", "1600 Pennsylvania Ave NW Washington DC")

    "Acme Corp, 221B Baker Street, London"
        → ("Acme Corp", "221B Baker Street, London")

    "742 Evergreen Terrace, Springfield"
        → ("", "742 Evergreen Terrace, Springfield")   # no name prefix found
    """
    # Match a street number: digits at a word boundary, optionally followed
    # by a single letter (e.g. "221B"), preceded by a word boundary.
    pattern = re.compile(r"\b(\d+[A-Za-z]?)\b")
    match = pattern.search(raw_address)

    if match and match.start() > 0:
        name_part    = raw_address[:match.start()].strip().strip(",").strip()
        address_part = raw_address[match.start():].strip()
        return name_part, address_part
    else:
        # No leading name found — entire string is the address
        return "", raw_address.strip()


def _build_street(components: list) -> Optional[str]:
    """Combine street_number + route into a single street line."""
    number = _get_component(components, "street_number")
    route  = _get_component(components, "route")
    if number and route:
        return f"{number} {route}"
    return route or number or None


def _build_town(components: list) -> Optional[str]:
    """
    Combine city + state/province abbreviation + postal code into a single
    town string for subfield 3.

    The combined value is returned as-is; swift_mt50f.py will auto-split it
    across two '3/' lines if it exceeds 29 characters.
    """
    city       = _get_component(components, "locality", "postal_town",
                                 "sublocality_level_1")
    state      = _get_component(components, "administrative_area_level_1",
                                 use_short=True)   # e.g. "DC", "CA"
    postcode   = _get_component(components, "postal_code")

    parts = [p for p in [city, state, postcode] if p]
    return " ".join(parts) if parts else None


# ---------------------------------------------------------------------------
# Main integration function
# ---------------------------------------------------------------------------

def geocode_address(
    raw_address: str,
    api_key: str,
    # Optional overrides — supply these when the raw string does not contain
    # enough information or you want to force specific Field 50F values.
    account_number:    Optional[str] = None,
    id_code:           Optional[str] = None,
    id_country_code:   Optional[str] = None,
    identifier:        Optional[str] = None,
    date_of_birth:     Optional[str] = None,
    birth_country_code: Optional[str] = None,
    birth_place:       Optional[str] = None,
    cust_id_country:   Optional[str] = None,
    cust_id_number:    Optional[str] = None,
    nat_id_country:    Optional[str] = None,
    nat_id_number:     Optional[str] = None,
    additional_info:   Optional[list] = None,
) -> AddressInput:
    """
    Call the Google Maps Geocoding API and map the response to an AddressInput.

    Parameters
    ----------
    raw_address : str
        The full, unformatted address string, optionally prefixed with a name.
        e.g. "Mr. President 1600 Pennsylvania Ave NW Washington, DC 20500, USA"

    api_key : str
        Your Google Maps Geocoding API key.

    account_number / id_code / ... : optional
        Field 50F Line 1 and supplementary subfield values that cannot be
        derived from a geocoding API and must be supplied by the caller.

    Returns
    -------
    AddressInput
        Populated and ready to pass into swift_mt50f.format_field_50f().

    Raises
    ------
    ValueError
        If the Google API returns a non-OK status or no results are found.
    requests.HTTPError
        On HTTP-level failures.
    """

    # ------------------------------------------------------------------
    # 1. Extract leading name from the raw string before sending to Google
    # ------------------------------------------------------------------
    name, address_for_geocoding = _extract_name_from_raw(raw_address)

    # ------------------------------------------------------------------
    # 2. Call Google Maps Geocoding API
    # ------------------------------------------------------------------
    params = {
        "address": address_for_geocoding,
        "key":     api_key,
    }

    response = requests.get(GEOCODING_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("status") != "OK":
        raise ValueError(
            f"Google Geocoding API returned status '{data.get('status')}' "
            f"for address: '{address_for_geocoding}'. "
            f"Error message: {data.get('error_message', 'none')}"
        )

    if not data.get("results"):
        raise ValueError(f"No geocoding results found for: '{address_for_geocoding}'")

    # Use the first (best) result
    result     = data["results"][0]
    components = result.get("address_components", [])

    # ------------------------------------------------------------------
    # 3. Map Google components → AddressInput fields
    # ------------------------------------------------------------------

    # Street address → address_lines[0]
    street = _build_street(components)
    address_lines = [street] if street else []

    # Sub-premise (apartment/suite/floor) → address_lines[1]
    subpremise = _get_component(components, "subpremise")
    if subpremise:
        address_lines.append(subpremise)

    # Country code (ISO 3166-1 alpha-2, already correct format for Field 50F)
    country_code = _get_component(components, "country", use_short=True)

    # Town = city + state abbreviation + postcode (auto-split handled downstream)
    town = _build_town(components)

    # ------------------------------------------------------------------
    # 4. Build and return the AddressInput
    # ------------------------------------------------------------------
    return AddressInput(
        # Line 1 — must be provided by caller; cannot be geocoded
        account_number   = account_number,
        id_code          = id_code,
        id_country_code  = id_country_code,
        identifier       = identifier,

        # Subfield 1 — extracted from the leading name in the raw string
        name             = name,

        # Subfields 2 & 3 — from Google components
        address_lines    = address_lines,
        country_code     = country_code,
        town             = town,

        # Subfields 4–8 — caller-supplied only (not available from geocoding)
        date_of_birth    = date_of_birth,
        birth_country_code = birth_country_code,
        birth_place      = birth_place,
        cust_id_country  = cust_id_country,
        cust_id_number   = cust_id_number,
        nat_id_country   = nat_id_country,
        nat_id_number    = nat_id_number,
        additional_info  = additional_info or [],
    )


# ---------------------------------------------------------------------------
# Mock geocoder — for testing without a live API key
# ---------------------------------------------------------------------------

def _mock_google_response(address: str) -> dict:
    """
    Returns a hardcoded Google-shaped response for well-known test addresses.
    Extend this dict to add more test cases.
    """
    MOCK_DB = {
        "1600 Pennsylvania Ave NW Washington, DC 20500, USA": {
            "status": "OK",
            "results": [{
                "address_components": [
                    {"long_name": "1600",                    "short_name": "1600",   "types": ["street_number"]},
                    {"long_name": "Pennsylvania Avenue NW",  "short_name": "Pennsylvania Ave NW", "types": ["route"]},
                    {"long_name": "Washington",              "short_name": "Washington", "types": ["locality"]},
                    {"long_name": "District of Columbia",    "short_name": "DC",     "types": ["administrative_area_level_1"]},
                    {"long_name": "20500",                   "short_name": "20500",  "types": ["postal_code"]},
                    {"long_name": "United States",           "short_name": "US",     "types": ["country"]},
                ]
            }]
        },
        "221B Baker Street, London NW1 6XE, UK": {
            "status": "OK",
            "results": [{
                "address_components": [
                    {"long_name": "221B",           "short_name": "221B",   "types": ["street_number"]},
                    {"long_name": "Baker Street",   "short_name": "Baker St", "types": ["route"]},
                    {"long_name": "London",         "short_name": "London", "types": ["locality"]},
                    {"long_name": "England",        "short_name": "ENG",    "types": ["administrative_area_level_1"]},
                    {"long_name": "NW1 6XE",        "short_name": "NW1 6XE","types": ["postal_code"]},
                    {"long_name": "United Kingdom", "short_name": "GB",     "types": ["country"]},
                ]
            }]
        },
        "Hauptstrasse 100, 60594 Frankfurt am Main Sachsenhausen, Germany": {
            "status": "OK",
            "results": [{
                "address_components": [
                    {"long_name": "100",                            "short_name": "100",    "types": ["street_number"]},
                    {"long_name": "Hauptstrasse",                   "short_name": "Hauptstr.", "types": ["route"]},
                    {"long_name": "Frankfurt am Main Sachsenhausen","short_name": "Frankfurt am Main Sachsenhausen", "types": ["locality"]},
                    {"long_name": "Hesse",                          "short_name": "HE",     "types": ["administrative_area_level_1"]},
                    {"long_name": "60594",                          "short_name": "60594",  "types": ["postal_code"]},
                    {"long_name": "Germany",                        "short_name": "DE",     "types": ["country"]},
                ]
            }]
        },
    }

    # Fuzzy match — strip leading name portion before looking up
    _, addr_only = _extract_name_from_raw(address)
    for key, val in MOCK_DB.items():
        if addr_only.strip().lower() in key.lower() or key.lower() in addr_only.strip().lower():
            return val

    return {"status": "ZERO_RESULTS", "results": []}


def geocode_address_mock(
    raw_address: str,
    **kwargs,
) -> AddressInput:
    """
    Drop-in replacement for geocode_address() that uses the mock DB instead
    of calling the live Google API.  Accepts the same keyword arguments.
    """
    name, address_for_geocoding = _extract_name_from_raw(raw_address)
    data = _mock_google_response(address_for_geocoding)

    if data.get("status") != "OK" or not data.get("results"):
        raise ValueError(f"Mock geocoder: no result for '{address_for_geocoding}'")

    components    = data["results"][0]["address_components"]
    street        = _build_street(components)
    address_lines = [street] if street else []
    subpremise    = _get_component(components, "subpremise")
    if subpremise:
        address_lines.append(subpremise)

    return AddressInput(
        account_number   = kwargs.get("account_number"),
        id_code          = kwargs.get("id_code"),
        id_country_code  = kwargs.get("id_country_code"),
        identifier       = kwargs.get("identifier"),
        name             = name,
        address_lines    = address_lines,
        country_code     = _get_component(components, "country", use_short=True),
        town             = _build_town(components),
        date_of_birth    = kwargs.get("date_of_birth"),
        birth_country_code = kwargs.get("birth_country_code"),
        birth_place      = kwargs.get("birth_place"),
        cust_id_country  = kwargs.get("cust_id_country"),
        cust_id_number   = kwargs.get("cust_id_number"),
        nat_id_country   = kwargs.get("nat_id_country"),
        nat_id_number    = kwargs.get("nat_id_number"),
        additional_info  = kwargs.get("additional_info") or [],
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from swift_mt50f import format_field_50f

    test_cases = [
        {
            "label": "The White House (account number, Line 1 Format A)",
            "raw":   "Mr. President 1600 Pennsylvania Ave NW Washington, DC 20500, USA",
            "kwargs": {"account_number": "US0001600PENNAVE"},
        },
        {
            "label": "Sherlock Holmes (passport, Line 1 Format B)",
            "raw":   "Mr. Sherlock Holmes 221B Baker Street, London NW1 6XE, UK",
            "kwargs": {
                "id_code":         "CCPT",
                "id_country_code": "GB",
                "identifier":      "SH007GB",
            },
        },
        {
            "label": "Long town name — Frankfurt (auto-split across two 3/ lines)",
            "raw":   "Mustermann GmbH Hauptstrasse 100, 60594 Frankfurt am Main Sachsenhausen, Germany",
            "kwargs": {"account_number": "DE89370400440532013000"},
        },
    ]

    for tc in test_cases:
        print("=" * 60)
        print(tc["label"])
        print("=" * 60)
        addr_input = geocode_address_mock(tc["raw"], **tc["kwargs"])
        result     = format_field_50f(addr_input)
        print(str(result))
        print("Valid:", result.is_valid)
        if result.errors:
            print("Errors:")
            for e in result.errors:
                print("  -", e)
        print()
