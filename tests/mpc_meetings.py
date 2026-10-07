"""The archived MPC meetings (final day -> press-release ids), from Annualpolicy.aspx FY26/FY27."""
from datetime import date

MEETINGS = {
    date(2025, 8, 6): {"resolution": 60957, "governor": 60958},
    date(2025, 10, 1): {"resolution": 61332, "governor": 61333},
    date(2025, 12, 5): {"resolution": 61749, "governor": 61750},
    date(2026, 2, 6): {"resolution": 62169, "governor": 62170},
    date(2026, 4, 8): {"resolution": 62514, "governor": 62515},
    date(2026, 6, 5): {"resolution": 62863, "governor": 62864},
    date(2026, 8, 5): {"resolution": 63287, "governor": 63288},
    date(2026, 10, 7): {"resolution": 63742, "governor": 63744},   # first live meeting (rate hike)
}
