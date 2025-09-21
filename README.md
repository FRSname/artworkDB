# Art Catalog (Simple) – Docker web app

Fields: **Title**, **Artist (fixed: Vladislav Raszyk)**, **Year**, **Medium**, **Surface**, **Dimensions (cm)**, **Description**, **Keywords**.

## Quick start
```bash
docker compose up --build -d
# open http://localhost:8000
```
- New artwork form: `/artworks/new`
- List + search: `/`
- Detail: `/artworks/{artwork_id}`
- One-pager PDF: `/artworks/{artwork_id}/onepager.pdf`

### Volumes
- `./data/` holds the SQLite database (persisted)
- `./app/media/` holds uploaded images
