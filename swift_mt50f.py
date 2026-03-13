"""
SWIFT MT Field 50F - Address Formatter and Validator
=====================================================
Converts an unformatted postal address to the structured :50F: format
and validates the result against SWIFT MT Field 50F rules.

Field 50F Structure:
  Line 1 (mandatory): Either  /<account>  OR  <IdCode>/<CC>/<identifier>
  Line 2 (mandatory): 1/<name>
  Line 3 (mandatory): 3/<CC>[/<town_info>]
  Lines 4-5 (optional): Additional subfields (2-8)

Subfield number reference:
  1 = Name of Ordering Customer
  2 = Address Line
  3 = Country and Town
  4 = Date of Birth  (YYYYMMDD)
  5 = Place of Birth
  6 = Customer Identification Number
  7 = National Identity Number
  8 = Additional Information
"""

import re
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# ISO 3166-1 alpha-2 country codes (representative subset — extend as needed)
# ---------------------------------------------------------------------------
ISO_COUNTRY_CODES = {
    "AD","AE","AF","AG","AI","AL","AM","AO","AQ","AR","AS","AT","AU","AW","AX",
    "AZ","BA","BB","BD","BE","BF","BG","BH","BI","BJ","BL","BM","BN","BO","BQ",
    "BR","BS","BT","BV","BW","BY","BZ","CA","CC","CD","CF","CG","CH","CI","CK",
    "CL","CM","CN","CO","CR","CU","CV","CW","CX","CY","CZ","DE","DJ","DK","DM",
    "DO","DZ","EC","EE","EG","EH","ER","ES","ET","FI","FJ","FK","FM","FO","FR",
    "GA","GB","GD","GE","GF","GG","GH","GI","GL","GM","GN","GP","GQ","GR","GS",
    "GT","GU","GW","GY","HK","HM","HN","HR","HT","HU","ID","IE","IL","IM","IN",
    "IO","IQ","IR","IS","IT","JE","JM","JO","JP","KE","KG","KH","KI","KM","KN",
    "KP","KR","KW","KY","KZ","LA","LB","LC","LI","LK","LR","LS","LT","LU","LV",
    "LY","MA","MC","MD","ME","MF","MG","MH","MK","ML","MM","MN","MO","MP","MQ",
    "MR","MS","MT","MU","MV","MW","MX","MY","MZ","NA","NC","NE","NF","NG","NI",
    "NL","NO","NP","NR","NU","NZ","OM","PA","PE","PF","PG","PH","PK","PL","PM",
    "PN","PR","PS","PT","PW","PY","QA","RE","RO","RS","RU","RW","SA","SB","SC",
    "SD","SE","SG","SH","SI","SJ","SK","SL","SM","SN","SO","SR","SS","ST","SV",
    "SX","SY","SZ","TC","TD","TF","TG","TH","TJ","TK","TL","TM","TN","TO","TR",
    "TT","TV","TW","TZ","UA","UG","UM","US","UY","UZ","VA","VC","VE","VG","VI",
    "VN","VU","WF","WS","XK","YE","YT","ZA","ZM","ZW",
}

# Valid 4!a code words for Line 1 party identifier type
VALID_4A_CODES = {"ARNU", "CCPT", "CUST", "DRLC", "EMPL", "NIDN", "SOSE", "TXID"}


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------
class ErrorCode:
    T50 = "T50 - Invalid date"
    T54 = "T54 - Invalid Line 1 format"
    T55 = "T55 - Invalid 4!a code word"
    T56 = "T56 - Field 50F line validation error"
    T73 = "T73 - Invalid ISO Country Code"
    T74 = "T74 - Subfield after CC exceeds 35 characters (ICM)"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class AddressInput:
    """Raw, unformatted address components supplied by the caller."""
    # Line 1 — choose one of the two formats:
    account_number: Optional[str] = None        # used when Line1 = /<account>
    id_code: Optional[str] = None               # 4!a  e.g. "CCPT"
    id_country_code: Optional[str] = None       # CC   e.g. "GB"
    identifier: Optional[str] = None            # 27x  e.g. passport number

    # Subfield 1 — Name (mandatory)
    name: str = ""

    # Subfield 2 — Address line(s) (optional, up to 2 occurrences)
    address_lines: list = field(default_factory=list)

    # Subfield 3 — Country and Town (mandatory; up to 2 occurrences)
    # town may be up to 62 chars — it will be auto-split across two "3/" lines
    # if it exceeds the 29-char limit on the first occurrence.
    country_code: Optional[str] = None          # CC   e.g. "GB"
    town: Optional[str] = None                  # town / postcode — auto-split if >29 chars

    # Subfield 4 — Date of Birth (optional)
    date_of_birth: Optional[str] = None         # "YYYYMMDD"

    # Subfield 5 — Place of Birth (optional)
    birth_country_code: Optional[str] = None
    birth_place: Optional[str] = None           # ≤30 chars

    # Subfield 6 — Customer Identification Number (optional)
    cust_id_country: Optional[str] = None
    cust_id_number: Optional[str] = None        # ≤30 chars

    # Subfield 7 — National Identity Number (optional)
    nat_id_country: Optional[str] = None
    nat_id_number: Optional[str] = None         # ≤30 chars

    # Subfield 8 — Additional Information (optional, up to 2 occurrences)
    additional_info: list = field(default_factory=list)


@dataclass
class FormattedField50F:
    """Validated, formatted :50F: field ready for inclusion in an MT message."""
    lines: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def __str__(self) -> str:
        return ":50F:" + "\n".join(self.lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_cc(cc: str) -> bool:
    return isinstance(cc, str) and cc.upper() in ISO_COUNTRY_CODES


def _truncate(text: str, max_len: int) -> str:
    """Truncate a string to max_len, stripping leading/trailing whitespace."""
    return text.strip()[:max_len] if text else ""


def _sanitise(text: str, max_len: int) -> str:
    """Remove characters not in the SWIFT X character set and truncate."""
    # SWIFT X set: A-Z, a-z, 0-9, / - ? : ( ) . , ' + space CrLf
    allowed = re.compile(r"[^A-Za-z0-9/\-?:()\.,'\+ ]")
    cleaned = allowed.sub("", text.strip())
    return cleaned[:max_len]


def _validate_date(date_str: str) -> bool:
    """Validate YYYYMMDD date and ensure it is not in the future."""
    try:
        d = datetime.strptime(date_str, "%Y%m%d").date()
        return d <= date.today()
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Core formatter / validator
# ---------------------------------------------------------------------------
def format_field_50f(addr: AddressInput) -> FormattedField50F:
    """
    Convert an AddressInput to a validated :50F: formatted field.

    Returns a FormattedField50F with .lines (list of strings) and
    .errors (list of error-code strings).  If .is_valid is True the
    field is ready for use in a SWIFT MT message.
    """
    result = FormattedField50F()
    errors = result.errors
    lines  = result.lines

    # -----------------------------------------------------------------------
    # LINE 1 — Mandatory
    # Format A: /<account>   (up to 34 chars after the slash)
    # Format B: <4!a>/<CC>/<identifier>
    # -----------------------------------------------------------------------
    if addr.account_number:
        account = _sanitise(addr.account_number, 34)
        line1 = f"/{account}"
        lines.append(line1)

    elif addr.id_code and addr.id_country_code and addr.identifier:
        code = addr.id_code.upper().strip()
        cc   = addr.id_country_code.upper().strip()
        ident = _sanitise(addr.identifier, 27)

        if code not in VALID_4A_CODES:
            errors.append(f"{ErrorCode.T55} — '{code}' is not a valid 4!a code word. "
                          f"Valid values: {sorted(VALID_4A_CODES)}")

        if not _validate_cc(cc):
            errors.append(f"{ErrorCode.T73} — '{cc}' is not a valid ISO 3166-1 country code")

        line1 = f"{code}/{cc}/{ident}"
        lines.append(line1)

    else:
        errors.append(f"{ErrorCode.T54} — Line 1 must be either '/<account>' "
                      "or '<4!a>/<CC>/<identifier>'")
        lines.append("/UNKNOWN")   # placeholder so we can continue validation

    # -----------------------------------------------------------------------
    # LINE 2 — Subfield 1 / Name  (mandatory)
    # Format: "1/"33x
    # -----------------------------------------------------------------------
    if not addr.name or not addr.name.strip():
        errors.append(f"{ErrorCode.T56} — Subfield 1 (Name) is mandatory")
    else:
        name = _sanitise(addr.name, 33)
        lines.append(f"1/{name}")

    # -----------------------------------------------------------------------
    # SUBFIELD 2 — Address line(s)  (optional, max 2 occurrences)
    # Format: "2/"33x
    # -----------------------------------------------------------------------
    addr_lines_used = 0
    for al in (addr.address_lines or []):
        if addr_lines_used >= 2:
            errors.append(f"{ErrorCode.T56} — Subfield 2 (Address) may appear at most 2 times")
            break
        text = _sanitise(al, 33)
        if text:
            lines.append(f"2/{text}")
            addr_lines_used += 1

    # -----------------------------------------------------------------------
    # SUBFIELD 3 — Country and Town  (mandatory, up to 2 occurrences)
    # First occurrence:  "3/"<CC>["/"30x]
    # Further occurrence: "3/"33x
    # ICM rule: subfield after CC must not exceed 35 chars (here ≤30 enforced)
    # -----------------------------------------------------------------------
    if not addr.country_code:
        errors.append(f"{ErrorCode.T56} — A line with subfield 3 (Country/Town) is mandatory")
    else:
        cc3 = addr.country_code.upper().strip()
        if not _validate_cc(cc3):
            errors.append(f"{ErrorCode.T73} — '{cc3}' is not a valid ISO 3166-1 country code "
                          "(subfield 3)")

        if addr.town:
            town_text = _sanitise(addr.town, 62)  # sanitise first, then split

            # Line 1 structure: "3/" + CC(2) + "/" + town  → overhead = 4 chars
            # Generic line limit = 33x  → town portion on first line ≤ 29 chars
            # ICM rule: town portion after CC must not exceed 35 chars — the
            # 29-char first-line limit is the binding constraint (29 < 35).
            TOWN_LINE1_MAX = 29   # chars available for town on first "3/" line
            TOWN_LINE2_MAX = 33   # second "3/" line is plain 33x (no CC prefix)

            if len(town_text) <= TOWN_LINE1_MAX:
                # Fits on one line
                lines.append(f"3/{cc3}/{town_text}")
            else:
                # Split: first 29 chars on line 1, remainder (≤33) on line 2
                part1 = town_text[:TOWN_LINE1_MAX]
                part2 = town_text[TOWN_LINE1_MAX:TOWN_LINE1_MAX + TOWN_LINE2_MAX]
                lines.append(f"3/{cc3}/{part1}")
                lines.append(f"3/{part2}")
                overflow = town_text[TOWN_LINE1_MAX + TOWN_LINE2_MAX:]
                if overflow:
                    # Town name exceeds 29+33=62 chars — cannot fit in two lines
                    errors.append(
                        f"{ErrorCode.T56} — Town name is too long to fit across two '3/' lines "
                        f"(max 62 chars combined); {len(overflow)} character(s) were dropped"
                    )
        else:
            lines.append(f"3/{cc3}")

    # -----------------------------------------------------------------------
    # SUBFIELD 4 — Date of Birth  (optional)
    # Format: "4/"YYYYMMDD
    # -----------------------------------------------------------------------
    if addr.date_of_birth:
        dob = addr.date_of_birth.strip()
        if not _validate_date(dob):
            errors.append(f"{ErrorCode.T50} — '{dob}' is not a valid date (YYYYMMDD, "
                          "not in the future)")
        else:
            lines.append(f"4/{dob}")

    # -----------------------------------------------------------------------
    # SUBFIELD 5 — Place of Birth  (optional)
    # Format: "5/"<CC>"/"30x
    # Rule:  4 must not be used without 5, and vice versa
    # -----------------------------------------------------------------------
    has_4 = addr.date_of_birth is not None
    has_5 = addr.birth_country_code is not None and addr.birth_place is not None

    if has_4 != has_5:
        errors.append(f"{ErrorCode.T56} — Subfields 4 (Date of Birth) and 5 (Place of Birth) "
                      "must both be present or both absent")

    if has_5:
        cc5 = addr.birth_country_code.upper().strip()
        if not _validate_cc(cc5):
            errors.append(f"{ErrorCode.T73} — '{cc5}' is not a valid ISO country code "
                          "(subfield 5)")
        place = _sanitise(addr.birth_place, 30)
        lines.append(f"5/{cc5}/{place}")

    # -----------------------------------------------------------------------
    # SUBFIELD 6 — Customer Identification Number  (optional)
    # Format: "6/"<CC>"/"30x
    # -----------------------------------------------------------------------
    if addr.cust_id_country and addr.cust_id_number:
        cc6 = addr.cust_id_country.upper().strip()
        if not _validate_cc(cc6):
            errors.append(f"{ErrorCode.T73} — '{cc6}' is not a valid ISO country code "
                          "(subfield 6)")
        cid = _sanitise(addr.cust_id_number, 30)
        lines.append(f"6/{cc6}/{cid}")

    # -----------------------------------------------------------------------
    # SUBFIELD 7 — National Identity Number  (optional)
    # Format: "7/"<CC>"/"30x
    # -----------------------------------------------------------------------
    if addr.nat_id_country and addr.nat_id_number:
        cc7 = addr.nat_id_country.upper().strip()
        if not _validate_cc(cc7):
            errors.append(f"{ErrorCode.T73} — '{cc7}' is not a valid ISO country code "
                          "(subfield 7)")
        nid = _sanitise(addr.nat_id_number, 30)
        lines.append(f"7/{cc7}/{nid}")

    # -----------------------------------------------------------------------
    # SUBFIELD 8 — Additional Information  (optional, max 2 occurrences)
    # Format: "8/"33x
    # Rule: may only appear if Line 1 is <4!a>/<CC>/<identifier>,
    #       OR subfield 6 is present, OR subfield 7 is present
    # -----------------------------------------------------------------------
    has_8_qualifier = (
        bool(addr.id_code)           # Line 1 format B
        or bool(addr.cust_id_number) # subfield 6 present
        or bool(addr.nat_id_number)  # subfield 7 present
    )

    info_used = 0
    for info in (addr.additional_info or []):
        if info_used >= 2:
            errors.append(f"{ErrorCode.T56} — Subfield 8 (Additional Info) may appear "
                          "at most 2 times")
            break
        if not has_8_qualifier:
            errors.append(f"{ErrorCode.T56} — Subfield 8 requires Line 1 in 4!a/CC/id format, "
                          "or subfield 6, or subfield 7 to be present")
            break
        text = _sanitise(info, 33)
        if text:
            lines.append(f"8/{text}")
            info_used += 1

    # -----------------------------------------------------------------------
    # Final ordering check — numbers must appear in ascending numeric order
    # -----------------------------------------------------------------------
    subfield_nums = []
    for ln in lines[1:]:   # skip Line 1
        m = re.match(r"^([1-8])/", ln)
        if m:
            subfield_nums.append(int(m.group(1)))

    if subfield_nums != sorted(subfield_nums):
        errors.append(f"{ErrorCode.T56} — Subfield numbers must appear in ascending order, "
                      f"got: {subfield_nums}")

    return result


# ---------------------------------------------------------------------------
# Convenience wrapper: build from a plain dict
# ---------------------------------------------------------------------------
def address_dict_to_50f(data: dict) -> FormattedField50F:
    """
    Build a Field 50F from a plain dictionary.  Keys mirror AddressInput fields.

    Example
    -------
    result = address_dict_to_50f({
        "account_number": "12345678901234",
        "name": "John Smith",
        "address_lines": ["10 Downing Street"],
        "country_code": "GB",
        "town": "London SW1A 2AA",
    })
    """
    addr = AddressInput(
        account_number   = data.get("account_number"),
        id_code          = data.get("id_code"),
        id_country_code  = data.get("id_country_code"),
        identifier       = data.get("identifier"),
        name             = data.get("name", ""),
        address_lines    = data.get("address_lines", []),
        country_code     = data.get("country_code"),
        town             = data.get("town"),
        date_of_birth    = data.get("date_of_birth"),
        birth_country_code = data.get("birth_country_code"),
        birth_place      = data.get("birth_place"),
        cust_id_country  = data.get("cust_id_country"),
        cust_id_number   = data.get("cust_id_number"),
        nat_id_country   = data.get("nat_id_country"),
        nat_id_number    = data.get("nat_id_number"),
        additional_info  = data.get("additional_info", []),
    )
    return format_field_50f(addr)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    print("=" * 60)
    print("Example 1 — Line 1 format A (account number)")
    print("=" * 60)
    r1 = address_dict_to_50f({
        "account_number": "12345678901234",
        "name": "John Smith",
        "address_lines": ["10 Downing Street"],
        "country_code": "GB",
        "town": "London SW1A 2AA",
    })
    print(str(r1))
    print("Valid:", r1.is_valid)
    if r1.errors:
        print("Errors:", r1.errors)

    print()
    print("=" * 60)
    print("Example 2 — Line 1 format B (passport / CCPT)")
    print("=" * 60)
    r2 = address_dict_to_50f({
        "id_code": "CCPT",
        "id_country_code": "US",
        "identifier": "123456789",
        "name": "Jane Doe",
        "address_lines": ["742 Evergreen Terrace", "Springfield"],
        "country_code": "US",
        "town": "IL 62701",
        "date_of_birth": "19800315",
        "birth_country_code": "US",
        "birth_place": "Chicago IL",
        "additional_info": ["For compliance reference only"],
    })
    print(str(r2))
    print("Valid:", r2.is_valid)
    if r2.errors:
        print("Errors:", r2.errors)

    print()
    print("=" * 60)
    print("Example 4 — Long town name (40 chars), auto-split across two 3/ lines")
    print("=" * 60)
    r4 = address_dict_to_50f({
        "account_number": "DE89370400440532013000",
        "name": "Mustermann GmbH",
        "address_lines": ["Hauptstrasse 100"],
        "country_code": "DE",
        # 40-char town: "Frankfurt am Main Sachsenhausen 60594" — auto-split at char 29
        "town": "Frankfurt am Main Sachsenhausen 60594 DE",
    })
    print(str(r4))
    print("Valid:", r4.is_valid)
    if r4.errors:
        print("Errors:", r4.errors)
    # Show exactly where the split happened
    for ln in r4.lines:
        if ln.startswith("3/"):
            print(f"  → '{ln}'  ({len(ln)-2} chars after '3/')")
    print("=" * 60)
    r3 = address_dict_to_50f({
        "account_number": "ACC999",
        "name": "Bad Address Co",
        "country_code": "XX",          # invalid country code
        "town": "Nowhere",
        "date_of_birth": "20991231",   # future date
        "birth_country_code": "US",
        "birth_place": "New York",
    })
    print(str(r3))
    print("Valid:", r3.is_valid)
    print("Errors:")
    for e in r3.errors:
        print("  -", e)
