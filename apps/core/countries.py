"""The countries offered when someone gives their address (ISO 3166 codes, which tax rules and billing details use)."""
COUNTRIES = (
    ("AE", "United Arab Emirates"), ("AF", "Afghanistan"), ("AT", "Austria"), ("AU", "Australia"), ("BD", "Bangladesh"),
    ("BE", "Belgium"), ("BH", "Bahrain"), ("BR", "Brazil"), ("CA", "Canada"), ("CH", "Switzerland"), ("CN", "China"),
    ("CZ", "Czechia"), ("DE", "Germany"), ("DK", "Denmark"), ("EG", "Egypt"), ("ES", "Spain"), ("FI", "Finland"),
    ("FR", "France"), ("GB", "United Kingdom"), ("GR", "Greece"), ("HK", "Hong Kong"), ("ID", "Indonesia"),
    ("IE", "Ireland"), ("IN", "India"), ("IQ", "Iraq"), ("IT", "Italy"), ("JO", "Jordan"), ("JP", "Japan"),
    ("KE", "Kenya"), ("KR", "South Korea"), ("KW", "Kuwait"), ("LB", "Lebanon"), ("LK", "Sri Lanka"), ("MA", "Morocco"),
    ("MX", "Mexico"), ("MY", "Malaysia"), ("NG", "Nigeria"), ("NL", "Netherlands"), ("NO", "Norway"), ("NP", "Nepal"),
    ("NZ", "New Zealand"), ("OM", "Oman"), ("PH", "Philippines"), ("PK", "Pakistan"), ("PL", "Poland"), ("PT", "Portugal"),
    ("QA", "Qatar"), ("RO", "Romania"), ("RU", "Russia"), ("SA", "Saudi Arabia"), ("SE", "Sweden"), ("SG", "Singapore"),
    ("TH", "Thailand"), ("TR", "Türkiye"), ("TZ", "Tanzania"), ("UA", "Ukraine"), ("US", "United States"),
    ("VN", "Vietnam"), ("ZA", "South Africa"),
)
CODES = {code for code, _ in COUNTRIES}
