import csv
import io


_CSV_COLUMNS = [
    "business_name",
    "address",
    "phone",
    "website",
    "email",
    "email_status",
    "category",
    "rating",
    "maps_url",
]


def export_to_csv(leads: list[dict]) -> str:
    """
    Convert a list of lead dicts to a CSV string.

    Returns CSV content as a string (UTF-8 with BOM for Excel compatibility).
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
    return output.getvalue()
