import csv
import io

_CSV_COLUMNS = [
    "username",
    "nickname",
    "email",
    "email_source",
    "followers_count",
    "verified",
    "bio_link",
    "profile_url",
    "bio_text",
    "scraped_at",
]


def leads_to_csv(leads: list[dict]) -> bytes:
    """
    Convierte una lista de leads a CSV con BOM UTF-8 para compatibilidad con Excel.
    """
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=_CSV_COLUMNS,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(leads)
    return ("\ufeff" + output.getvalue()).encode("utf-8")
