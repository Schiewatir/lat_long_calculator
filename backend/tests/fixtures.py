"""Small real-coordinate gazetteer covering the scenarios in the specs."""

from app.gazetteer.base import MemoryGazetteer, PostalRec, name_records_for_place, postal_name_records

POSTAL = [
    # country, postcode, place, admin1, lat, lon
    ("DE", "80331", "München", "Bayern", 48.1372, 11.5755),
    ("DE", "80333", "München", "Bayern", 48.1458, 11.5677),
    ("DE", "10115", "Berlin", "Berlin", 52.5323, 13.3846),
    ("DE", "01067", "Dresden", "Sachsen", 51.0554, 13.7347),
    ("DE", "60311", "Frankfurt am Main", "Hessen", 50.1127, 8.6831),
    ("DE", "15230", "Frankfurt (Oder)", "Brandenburg", 52.3471, 14.5506),
    ("DE", "75015", "Bretten", "Baden-Württemberg", 49.0364, 8.7069),
    ("DE", "20095", "Hamburg", "Hamburg", 53.5507, 10.0014),
    ("FR", "75001", "Paris 01", "Île-de-France", 48.8592, 2.3417),
    ("FR", "06000", "Nice", "Provence-Alpes-Côte d'Azur", 43.7031, 7.2661),
    ("US", "75001", "Addison", "Texas", 32.9618, -96.8292),
    ("US", "02134", "Allston", "Massachusetts", 42.3539, -71.1337),
    ("US", "10001", "New York", "New York", 40.7484, -73.9967),
    ("GB", "SW1A", "London", "England", 51.5010, -0.1416),
    ("NL", "1011", "Amsterdam", "Noord-Holland", 52.3746, 4.8986),
    ("PL", "00-001", "Warszawa", "Mazowieckie", 52.2297, 21.0122),
]

PLACES = [
    # country, geonameid, name, alternates, lat, lon, population
    ("DE", 2867714, "Munich", ["München", "Muenchen", "Monaco di Baviera", "Munique"], 48.13743, 11.57549, 1260391),
    ("DE", 2950159, "Berlin", ["Berlin", "Berlino"], 52.52437, 13.41053, 3426354),
    ("DE", 2935022, "Dresden", ["Dresde"], 51.05089, 13.73832, 486854),
    ("DE", 2925533, "Frankfurt am Main", ["Frankfurt", "Francfort-sur-le-Main"], 50.11552, 8.68417, 650000),
    ("DE", 2925535, "Frankfurt (Oder)", ["Frankfurt an der Oder"], 52.34714, 14.55062, 57015),
    ("DE", 2944388, "Bretten", [], 49.03685, 8.70745, 28000),
    ("DE", 2911298, "Hamburg", ["Hambourg", "Amburgo"], 53.57532, 10.01534, 1739117),
    ("DE", 2842647, "Sankt Augustin", ["St. Augustin"], 50.77538, 7.197, 55000),
    ("FR", 2988507, "Paris", ["Parigi", "París"], 48.85341, 2.3488, 2138551),
    ("FR", 2990440, "Nice", ["Nizza"], 43.70313, 7.26608, 338620),
    ("US", 4717560, "Paris", [], 33.66094, -95.55551, 25171),
    ("US", 5128581, "New York City", ["New York", "NYC"], 40.71427, -74.00597, 8175133),
    ("US", 4670249, "Addison", [], 32.96179, -96.82917, 13056),
    ("US", 4930956, "Boston", [], 42.35843, -71.05977, 617594),
    ("GB", 2643743, "London", ["Londres", "Londra"], 51.50853, -0.12574, 7556900),
    ("NL", 2759794, "Amsterdam", [], 52.37403, 4.88969, 741636),
    ("GE", 611717, "Tbilisi", ["Tiflis", "T'bilisi"], 41.69411, 44.83368, 1049498),
    ("PL", 756135, "Warsaw", ["Warszawa", "Varsovie"], 52.22977, 21.01178, 1702139),
]


def postal_recs() -> list[PostalRec]:
    return [PostalRec(*row) for row in POSTAL]


def fixture_gazetteer() -> MemoryGazetteer:
    names = []
    for country, gid, name, alts, lat, lon, pop in PLACES:
        names += name_records_for_place(country, gid, name, alts, lat, lon, pop)
    postal = postal_recs()
    names += postal_name_records(postal)
    return MemoryGazetteer(postal, names)
