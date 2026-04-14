import csv
import io
from typing import Any


def leads_to_csv(leads: list[dict[str, Any]]) -> bytes:
    """Convert a list of lead dicts to UTF-8 CSV bytes (with BOM for Excel)."""
    output = io.StringIO()
    fieldnames = [
        "username",
        "full_name",
        "email",
        "email_source",
        "followers_count",
        "is_business",
        "bio_url",
        "profile_url",
        "source_type",
        "phone",
        "business_category",
        "scraped_at",
    ]
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for lead in leads:
        writer.writerow(lead)

    # UTF-8 BOM so Excel opens it correctly
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")
