"""Location → ISO country inference (lightweight, offline)."""

from __future__ import annotations

import re
from functools import lru_cache

from dcr.pipeline.normalize.text import norm_key

COUNTRY_NAMES: dict[str, str] = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
    "u s": "US",
    "u s a": "US",
    "america": "US",
    "india": "IN",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "great britain": "GB",
    "canada": "CA",
    "china": "CN",
    "prc": "CN",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "switzerland": "CH",
    "ireland": "IE",
    "netherlands": "NL",
    "poland": "PL",
    "spain": "ES",
    "italy": "IT",
    "sweden": "SE",
    "denmark": "DK",
    "norway": "NO",
    "finland": "FI",
    "israel": "IL",
    "singapore": "SG",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "taiwan": "TW",
    "hong kong": "HK",
    "australia": "AU",
    "new zealand": "NZ",
    "brazil": "BR",
    "mexico": "MX",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "nigeria": "NG",
    "kenya": "KE",
    "egypt": "EG",
    "south africa": "ZA",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "vietnam": "VN",
    "viet nam": "VN",
    "indonesia": "ID",
    "philippines": "PH",
    "malaysia": "MY",
    "thailand": "TH",
    "turkey": "TR",
    "turkiye": "TR",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "portugal": "PT",
    "belgium": "BE",
    "austria": "AT",
    "czech republic": "CZ",
    "czechia": "CZ",
    "romania": "RO",
    "ukraine": "UA",
    "russia": "RU",
    "iran": "IR",
    "nepal": "NP",
}
US_STATES = {
    "al": "alabama",
    "ak": "alaska",
    "az": "arizona",
    "ar": "arkansas",
    "ca": "california",
    "co": "colorado",
    "ct": "connecticut",
    "de": "delaware",
    "fl": "florida",
    "ga": "georgia",
    "hi": "hawaii",
    "id": "idaho",
    "il": "illinois",
    "in": "indiana",
    "ia": "iowa",
    "ks": "kansas",
    "ky": "kentucky",
    "la": "louisiana",
    "me": "maine",
    "md": "maryland",
    "ma": "massachusetts",
    "mi": "michigan",
    "mn": "minnesota",
    "ms": "mississippi",
    "mo": "missouri",
    "mt": "montana",
    "ne": "nebraska",
    "nv": "nevada",
    "nh": "new hampshire",
    "nj": "new jersey",
    "nm": "new mexico",
    "ny": "new york",
    "nc": "north carolina",
    "nd": "north dakota",
    "oh": "ohio",
    "ok": "oklahoma",
    "or": "oregon",
    "pa": "pennsylvania",
    "ri": "rhode island",
    "sc": "south carolina",
    "sd": "south dakota",
    "tn": "tennessee",
    "tx": "texas",
    "ut": "utah",
    "vt": "vermont",
    "va": "virginia",
    "wa": "washington",
    "wv": "west virginia",
    "wi": "wisconsin",
    "wy": "wyoming",
    "dc": "district of columbia",
}
CITIES: dict[str, str] = {
    "san francisco": "US",
    "bay area": "US",
    "san francisco bay area": "US",
    "new york city": "US",
    "nyc": "US",
    "seattle": "US",
    "mountain view": "US",
    "sunnyvale": "US",
    "palo alto": "US",
    "austin": "US",
    "boston": "US",
    "chicago": "US",
    "los angeles": "US",
    "pittsburgh": "US",
    "kirkland": "US",
    "boulder": "US",
    "atlanta": "US",
    "san jose": "US",
    "menlo park": "US",
    "cupertino": "US",
    "redmond": "US",
    "bengaluru": "IN",
    "bangalore": "IN",
    "hyderabad": "IN",
    "pune": "IN",
    "gurugram": "IN",
    "gurgaon": "IN",
    "mumbai": "IN",
    "chennai": "IN",
    "noida": "IN",
    "delhi": "IN",
    "new delhi": "IN",
    "london": "GB",
    "cambridge uk": "GB",
    "toronto": "CA",
    "vancouver": "CA",
    "montreal": "CA",
    "waterloo": "CA",
    "zurich": "CH",
    "dublin": "IE",
    "warsaw": "PL",
    "berlin": "DE",
    "munich": "DE",
    "paris": "FR",
    "amsterdam": "NL",
    "tel aviv": "IL",
    "beijing": "CN",
    "shanghai": "CN",
    "shenzhen": "CN",
    "tokyo": "JP",
    "seoul": "KR",
    "sydney": "AU",
    "sao paulo": "BR",
    "singapore": "SG",
}
_US_STATE_NAMES = set(US_STATES.values())


@lru_cache(maxsize=16384)
def infer_country(location: str | None) -> str | None:
    """Best-effort ISO-3166 alpha-2 from a free-text location ("Mountain View, CA" → "US")."""
    if not location:
        return None
    parts = [norm_key(p) for p in re.split(r"[,;|]", location) if p.strip()]
    # Most specific signal is usually the last component.
    for part in reversed(parts):
        if part in COUNTRY_NAMES:
            return COUNTRY_NAMES[part]
        if len(part) == 2 and part.upper() in set(COUNTRY_NAMES.values()) and part not in US_STATES:
            return part.upper()
    for part in reversed(parts):
        if part in US_STATES or part in _US_STATE_NAMES:
            return "US"
        if part in CITIES:
            return CITIES[part]
    whole = norm_key(location)
    for name, iso in CITIES.items():
        if name in whole:
            return iso
    return None


def region_matches(location: str | None, regions: list[str]) -> bool:
    """True if any requested region/city appears in the location (state abbreviations expanded)."""
    if not regions:
        return True
    if not location:
        return False
    loc = f" {norm_key(location)} "
    for r in regions:
        rk = norm_key(r)
        if f" {rk} " in loc:
            return True
        if rk in US_STATES and f" {US_STATES[rk]} " in loc:
            return True
        inv = {v: k for k, v in US_STATES.items()}
        if rk in inv and f" {inv[rk]} " in loc:
            return True
    return False
