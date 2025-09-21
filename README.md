# Art Catalog – Docker web app (FastAPI + SQLite)

## Quick start
```bash
docker compose up --build -d
# open http://localhost:8000
```
- New artwork form: `/artworks/new`
- List + search: `/`
- Detail: `/artworks/{artwork_id}`
- One-pager PDF: `/artworks/{artwork_id}/onepager.pdf`
- JSON API: `/api/artworks`, `/api/artworks/{artwork_id}`

### Volumes
- `./data/` holds the SQLite database (persisted)
- `./app/media/` holds uploaded images

### Features
- Auto IDs (leave blank → `A0001`, `A0002`, …)
- Upload primary image + multiple additional images (gallery)
- Set any image as primary, delete images
- Edit artwork metadata
- Search & filters (query, artist, style, year range, width range)
- One-pager PDF generator for printing

