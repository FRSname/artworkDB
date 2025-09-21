# Art Catalog (Simple)

## Run
```bash
docker compose up --build -d
# open http://localhost:8000
```

## API endpoints
- GET /api/artworks
- GET /api/artworks/{id}
- POST /api/artworks
- POST /api/artworks/{id}/images-json
- DELETE /api/artworks/{id}

Auth: optional header `X-API-Key` if `API_KEY` is set in compose.
