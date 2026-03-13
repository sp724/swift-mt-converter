# swift-mt-converter

A Python toolkit for converting unformatted postal addresses into the structured **SWIFT MT Field 50F** format, used to identify the Ordering Customer in SWIFT MT103 wire payment messages.

The pipeline has two stages:

1. **`geocode_to_50f.py`** — calls the Google Maps Geocoding API to parse a raw address string into structured components
2. **`swift_mt50f.py`** — validates and formats those components into a compliant `:50F:` field, enforcing all SWIFT MT Field 50F rules

---

## Field 50F Overview

Field 50F identifies the ordering customer in a SWIFT MT103 message. It has a strict multi-line structure:

```
:50F:/ACCOUNTNUMBER          ← Line 1: account or party identifier (mandatory)
1/Full Name                  ← Subfield 1: name (mandatory)
2/Street Address             ← Subfield 2: address line (optional, max 2x)
3/CC/Town Postcode           ← Subfield 3: country + town (mandatory, auto-split if long)
4/YYYYMMDD                   ← Subfield 4: date of birth (optional, paired with 5)
5/CC/Place of Birth          ← Subfield 5: place of birth (optional, paired with 4)
6/CC/Customer ID             ← Subfield 6: customer ID number (optional)
7/CC/National ID             ← Subfield 7: national identity number (optional)
8/Additional Info            ← Subfield 8: additional information (optional, max 2x)
```

---

## Pipeline Architecture

```
Raw address string
        │
        ▼
 geocode_to_50f.py
  ├── Extracts name prefix from raw string
  ├── Calls Google Maps Geocoding API
  ├── Maps address_components → AddressInput
        │
        ▼
  swift_mt50f.py
  ├── Validates Line 1 format (T54, T55, T73)
  ├── Validates all subfields (T56, T50, T73, T74)
  ├── Auto-splits long town names across two 3/ lines
  └── Returns formatted :50F: field + any error codes
```

---

## Installation

```bash
git clone https://github.com/sp724/swift-mt-converter.git
cd swift-mt-converter
pip install requests
```

---

## Configuration

The Google Maps Geocoding API key is **never hardcoded**. Load it from an environment variable:

```bash
export GOOGLE_MAPS_KEY="your_api_key_here"
```

Then reference it in your code:

```python
import os
key = os.environ["GOOGLE_MAPS_KEY"]
```

---

## Usage

### Full pipeline — raw address to formatted Field 50F

```python
from geocode_to_50f import geocode_address
from swift_mt50f import format_field_50f
import os

addr_input = geocode_address(
    raw_address    = "Mr. John Smith 1600 Pennsylvania Ave NW Washington, DC 20500, USA",
    api_key        = os.environ["GOOGLE_MAPS_KEY"],
    account_number = "US0012345678",
)

result = format_field_50f(addr_input)

if result.is_valid:
    print(result)
else:
    for error in result.errors:
        print(error)
```

**Output:**
```
:50F:/US0012345678
1/Mr. John Smith
2/1600 Pennsylvania Avenue NW
3/US/Washington DC 20500
```

### Format only — supply pre-parsed address components directly

```python
from swift_mt50f import address_dict_to_50f

result = address_dict_to_50f({
    "account_number": "GB12345678901234",
    "name":           "Jane Doe",
    "address_lines":  ["10 Baker Street"],
    "country_code":   "GB",
    "town":           "London NW1 6XE",
})

print(result)
```

### Testing without a live API key

```python
from geocode_to_50f import geocode_address_mock
from swift_mt50f import format_field_50f

addr_input = geocode_address_mock(
    "Mr. President 1600 Pennsylvania Ave NW Washington, DC 20500, USA",
    account_number = "US0001600PENNAVE",
)

result = format_field_50f(addr_input)
print(result)
```

---

## Validation & Error Codes

The formatter enforces the full set of SWIFT MT Field 50F validation rules:

| Error Code | Description |
|---|---|
| `T50` | Invalid date — must be `YYYYMMDD` and not in the future |
| `T54` | Invalid Line 1 format — must be `/<account>` or `<4!a>/<CC>/<id>` |
| `T55` | Invalid 4!a code word — must be one of `ARNU CCPT CUST DRLC EMPL NIDN SOSE TXID` |
| `T56` | General subfield validation error — ordering, mandatory fields, occurrence limits |
| `T73` | Invalid ISO 3166-1 alpha-2 country code |
| `T74` | ICM rule — town subfield after CC exceeds 35 characters |

---

## Long Town Names

Town names longer than 29 characters are automatically split across two `3/` lines:

```
3/DE/Frankfurt am Main Sachsenhaus   ← first 29 chars of town on line 1
3/en HE 60594                        ← remainder on line 2 (max 33 chars)
```

The combined capacity is **62 characters** (29 + 33). A `T56` error is raised if the town name exceeds this limit.

---

## File Structure

```
swift-mt-converter/
├── swift_mt50f.py       # Field 50F formatter and validator
├── geocode_to_50f.py    # Google Maps integration layer
├── .gitignore
└── README.md
```

---

## Requirements

- Python 3.10+
- `requests` library (`pip install requests`)
- Google Maps Geocoding API key (for live geocoding only)

---

## License

MIT
