"""Rough callsign -> DXCC country, by prefix (longest match first).

Not a full cty.dat — a pragmatic map of the entities that actually show up on
the air, enough for a "countries per day" count. US territories are split out;
everything K/W/N/A otherwise collapses to one 'United States'.
"""

# Longest, most specific prefixes first in the lookup (we try 4,3,2,1 chars).
PREFIX = {
    # --- US territories (checked before the plain K/W/N/A -> USA) ---
    "KH6": "Hawaii", "NH6": "Hawaii", "AH6": "Hawaii", "WH6": "Hawaii", "KH7": "Hawaii",
    "KH2": "Guam", "NH2": "Guam", "AH2": "Guam", "WH2": "Guam",
    "KH8": "American Samoa", "KH0": "Mariana Is", "KH1": "Baker Howland",
    "KH9": "Wake I", "KH3": "Johnston I", "KH4": "Midway I",
    "KL": "Alaska", "NL": "Alaska", "AL": "Alaska", "WL": "Alaska",
    "KP4": "Puerto Rico", "NP4": "Puerto Rico", "WP4": "Puerto Rico", "KP3": "Puerto Rico",
    "KP2": "US Virgin Is", "NP2": "US Virgin Is", "WP2": "US Virgin Is",
    # --- North America ---
    "VE": "Canada", "VA": "Canada", "VO": "Canada", "VY": "Canada", "CY": "Canada",
    "XE": "Mexico", "XF": "Mexico", "4A": "Mexico", "6D": "Mexico",
    "CM": "Cuba", "CO": "Cuba", "HI": "Dominican Rep", "HH": "Haiti",
    "J6": "St Lucia", "J3": "Grenada", "J7": "Dominica", "J8": "St Vincent",
    "8P": "Barbados", "9Y": "Trinidad", "ZF": "Cayman Is", "6Y": "Jamaica",
    "VP2": "Leeward Is", "VP5": "Turks Caicos", "V4": "St Kitts", "FG": "Guadeloupe",
    "FM": "Martinique", "PJ": "Curacao", "HK": "Colombia", "HJ": "Colombia",
    "TI": "Costa Rica", "TG": "Guatemala", "YS": "El Salvador", "HR": "Honduras",
    "YN": "Nicaragua", "HP": "Panama", "V3": "Belize",
    # --- South America ---
    "LU": "Argentina", "LW": "Argentina", "L": "Argentina",
    "PY": "Brazil", "PP": "Brazil", "PR": "Brazil", "PT": "Brazil", "PU": "Brazil", "ZZ": "Brazil", "ZV": "Brazil", "ZW": "Brazil",
    "CE": "Chile", "CA": "Chile", "CB": "Chile", "XQ": "Chile", "3G": "Chile",
    "HC": "Ecuador", "HD": "Ecuador", "OA": "Peru", "CP": "Bolivia",
    "CX": "Uruguay", "ZP": "Paraguay", "YV": "Venezuela", "YW": "Venezuela",
    "8R": "Guyana", "PZ": "Suriname", "HK0": "San Andres",
    # --- Europe ---
    "G": "England", "M": "England", "2E": "England", "GW": "Wales", "MW": "Wales",
    "GM": "Scotland", "MM": "Scotland", "GI": "N Ireland", "GD": "Isle of Man", "GU": "Guernsey", "GJ": "Jersey",
    "EI": "Ireland", "EJ": "Ireland",
    "DL": "Germany", "DK": "Germany", "DJ": "Germany", "DB": "Germany", "DD": "Germany", "DF": "Germany", "DG": "Germany", "DH": "Germany", "DO": "Germany", "DA": "Germany",
    "F": "France", "TM": "France", "HB9": "Switzerland", "HB0": "Liechtenstein", "HB": "Switzerland",
    "OE": "Austria", "ON": "Belgium", "OO": "Belgium", "PA": "Netherlands", "PB": "Netherlands", "PD": "Netherlands", "PE": "Netherlands", "PI": "Netherlands",
    "LX": "Luxembourg", "I": "Italy", "IZ": "Italy", "IK": "Italy", "IW": "Italy", "IU": "Italy",
    "EA": "Spain", "EB": "Spain", "EC": "Spain", "ED": "Spain", "EA6": "Balearic Is", "EA8": "Canary Is", "EA9": "Ceuta Melilla",
    "CT": "Portugal", "CR": "Portugal", "CU": "Azores", "CT3": "Madeira",
    "OZ": "Denmark", "OU": "Denmark", "SM": "Sweden", "SA": "Sweden", "SK": "Sweden",
    "LA": "Norway", "LB": "Norway", "OH": "Finland", "OF": "Finland", "OG": "Finland", "OH0": "Aland Is",
    "SP": "Poland", "SQ": "Poland", "SO": "Poland", "OK": "Czechia", "OL": "Czechia", "OM": "Slovakia",
    "HA": "Hungary", "HG": "Hungary", "YO": "Romania", "YP": "Romania", "YR": "Romania",
    "LZ": "Bulgaria", "9A": "Croatia", "S5": "Slovenia", "S2": "Bangladesh",
    "E7": "Bosnia", "YU": "Serbia", "YT": "Serbia", "Z3": "N Macedonia", "ZA": "Albania",
    "SV": "Greece", "SW": "Greece", "SY": "Greece", "SZ": "Greece", "SX": "Greece", "9H": "Malta",
    "YL": "Latvia", "LY": "Lithuania", "ES": "Estonia", "UA": "Russia", "RA": "Russia", "R": "Russia", "UB": "Russia", "RK": "Russia", "RN": "Russia", "RW": "Russia", "RZ": "Russia", "RV": "Russia",
    "UR": "Ukraine", "US": "Ukraine", "UT": "Ukraine", "UU": "Ukraine", "UX": "Ukraine", "UY": "Ukraine", "EW": "Belarus", "EU": "Belarus",
    "ER": "Moldova", "4O": "Montenegro", "T7": "San Marino", "9A1": "Croatia",
    "TK": "Corsica", "IS0": "Sardinia", "IT9": "Sicily",
    # --- Africa / Middle East ---
    "CN": "Morocco", "SU": "Egypt", "ZS": "South Africa", "ZR": "South Africa",
    "5H": "Tanzania", "5Z": "Kenya", "5N": "Nigeria", "TR": "Gabon", "EL": "Liberia",
    "3V": "Tunisia", "7X": "Algeria", "6W": "Senegal", "C5": "Gambia", "D4": "Cape Verde",
    "4X": "Israel", "4Z": "Israel", "JY": "Jordan", "OD": "Lebanon", "YK": "Syria",
    "A4": "Oman", "A6": "UAE", "A7": "Qatar", "A9": "Bahrain", "9K": "Kuwait",
    "HZ": "Saudi Arabia", "EP": "Iran", "YI": "Iraq", "TA": "Turkey", "TC": "Turkey",
    "5B": "Cyprus", "4L": "Georgia", "EK": "Armenia", "4J": "Azerbaijan", "UK": "Uzbekistan", "EY": "Tajikistan", "UN": "Kazakhstan",
    # --- Asia / Pacific ---
    "JA": "Japan", "JE": "Japan", "JF": "Japan", "JG": "Japan", "JH": "Japan", "JI": "Japan", "JJ": "Japan", "JK": "Japan", "JL": "Japan", "JM": "Japan", "JN": "Japan", "JO": "Japan", "JP": "Japan", "JQ": "Japan", "JR": "Japan", "JS": "Japan", "7J": "Japan", "7K": "Japan", "7L": "Japan", "7M": "Japan", "7N": "Japan", "8J": "Japan",
    "BA": "China", "BD": "China", "BG": "China", "BH": "China", "BI": "China", "BY": "China", "B": "China",
    "BV": "Taiwan", "BX": "Taiwan", "HL": "South Korea", "DS": "South Korea", "6K": "South Korea", "6L": "South Korea", "6M": "South Korea",
    "VU": "India", "AT": "India", "4S": "Sri Lanka", "9V": "Singapore", "9M": "Malaysia", "HS": "Thailand", "E2": "Thailand",
    "YB": "Indonesia", "YC": "Indonesia", "YD": "Indonesia", "YE": "Indonesia", "DU": "Philippines", "DV": "Philippines", "DW": "Philippines", "4F": "Philippines",
    "XV": "Vietnam", "3W": "Vietnam", "XX9": "Macau", "VR2": "Hong Kong", "VR": "Hong Kong",
    "VK": "Australia", "AX": "Australia", "VI": "Australia", "VK9": "Australia Ext", "ZL": "New Zealand", "ZM": "New Zealand",
    "FK": "New Caledonia", "FO": "French Polynesia", "KH": "US Pacific", "T8": "Palau", "V6": "Micronesia", "V7": "Marshall Is", "T2": "Tuvalu", "3D2": "Fiji", "5W": "Samoa", "A3": "Tonga", "E5": "Cook Is",
}

# collapse remaining K/W/N/A one-char to USA (checked last)
_US1 = {"K": "United States", "W": "United States", "N": "United States", "A": "United States"}


def country(call):
    c = (call or "").upper().strip()
    if "/" in c:
        # a prefix portable like PA/N9TPB usually means the FIRST part is the DXCC
        head, tail = c.split("/", 1)
        c = head if (len(head) <= len(tail) or any(ch.isdigit() for ch in head)) else tail
    if not c:
        return ""
    for n in (4, 3, 2):
        if c[:n] in PREFIX:
            return PREFIX[c[:n]]
    if c[:1] in PREFIX:
        return PREFIX[c[:1]]
    if c[:1] in _US1:
        return _US1[c[:1]]
    return c[:2]  # unknown: group by the raw 2-char prefix so it still counts as "a country"
