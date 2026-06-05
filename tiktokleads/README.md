# TikTokLeads

Backend FastAPI para extraer leads desde TikTok en local, con pipeline asíncrono:

- Discovery (keyword / hashtag)
- Enrichment (perfil + señales de contacto)
- Persistencia SQLite
- Export CSV

## Ejecutar en local

```bash
cd tiktokleads
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8004
```

## Tests

```bash
cd tiktokleads
pytest
```

## Operación y diagnóstico

- Runbook técnico: `docs/tiktokleads-runbook.md`
- Endpoints clave:
  - `/api/tiktok/health`
  - `/api/tiktok/proxy-stats`
  - `/api/tiktok/debug/{job_id}`
