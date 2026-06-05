from backend.storage import database as db


async def run_discovery(
    client,
    mode: str,
    target: str,
    max_results: int,
    job_id: str,
    exclude_handles: set[str] | None = None,
) -> list[dict]:
    items = await client.discover(
        mode=mode,
        target=target,
        max_results=max_results,
        exclude_handles=exclude_handles,
    )
    clean_items: list[dict] = []
    for item in items:
        if not item.get("author_id") or not item.get("author_handle"):
            await db.add_job_event(job_id, "normalize", "warning", "Registro discovery descartado por datos incompletos")
            continue
        item["author_handle"] = str(item["author_handle"]).strip().lstrip("@")
        if not item["author_handle"]:
            continue
        await db.insert_discovery_item(job_id, item)
        await db.upsert_video(item)
        clean_items.append(item)
    await db.add_job_event(job_id, "discovery", "info", f"Descubiertos {len(clean_items)} autores válidos")
    return clean_items
