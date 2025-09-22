from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional, List
from io import BytesIO
import os, base64, requests

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image as PILImage

from sqlmodel import select
from .db import init_db, get_session, Artwork, Image
from .utils import (
    ensure_artwork_id, next_artwork_number, save_image_and_thumb,
    mk_slug, next_image_index
)

# -----------------------------------------------------------------------------
# App & static/media
# -----------------------------------------------------------------------------
app = FastAPI(title="Art Catalog (Simple)")
BASE = Path(__file__).parent
MEDIA_ROOT = BASE / "media"
STATIC_ROOT = BASE / "static"

app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
templates = Jinja2Templates(directory=str(BASE / "templates"))

@app.on_event("startup")
def on_startup():
    init_db()

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _check_api_key(request: Request) -> bool:
    api_key_env = os.getenv("API_KEY", "").strip()
    if not api_key_env:
        return True  # open if not configured
    supplied = request.headers.get("X-API-Key", "")
    return supplied == api_key_env

def _bytes_from_payload(image_base64: Optional[str], image_url: Optional[str]) -> Optional[bytes]:
    data = None
    if image_base64:
        try:
            # guard against data: header
            if image_base64.startswith("data:"):
                image_base64 = image_base64.split(",", 1)[-1]
            data = base64.b64decode(image_base64)
        except Exception:
            data = None
    elif image_url:
        try:
            r = requests.get(image_url, timeout=20)
            if r.ok:
                data = r.content
        except Exception:
            data = None
    return data

def _onepager_path(artwork_id: str) -> Path:
    out_dir = Path("/app/data/onepagers")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{artwork_id}.pdf"

# -----------------------------------------------------------------------------
# UI routes
# -----------------------------------------------------------------------------
@app.get("/")
def index(request: Request, q: str | None = None, year_from: str | None = None, year_to: str | None = None):
    from sqlmodel import and_, or_
    with get_session() as s:
        stmt = select(Artwork)
        conds = []
        if q:
            like = f"%{q.strip()}%"
            conds.append(or_(
                Artwork.title.like(like),
                Artwork.medium.like(like),
                Artwork.surface.like(like),
                Artwork.description.like(like),
                Artwork.keywords.like(like),
            ))
        if year_from:
            conds.append(Artwork.year >= year_from)
        if year_to:
            conds.append(Artwork.year <= year_to)
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.order_by(Artwork.id.desc())
        artworks = s.exec(stmt).all()
    params = {"q": q or "", "year_from": year_from or "", "year_to": year_to or ""}
    return templates.TemplateResponse("artworks/list.html", {"request": request, "artworks": artworks, "filters": params})

@app.get("/artworks/new")
def new_artwork(request: Request):
    return templates.TemplateResponse("artworks/new.html", {"request": request})

@app.post("/artworks")
async def create_artwork(
    request: Request,
    artwork_id: str = Form(""),
    title: str = Form(...),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_cm: float = Form(0.0),
    height_cm: float = Form(0.0),
    depth_cm: float = Form(0.0),
    description: str = Form(""),
    keywords: str = Form(""),
    image: UploadFile = File(None),
):
    if not artwork_id.strip():
        n = next_artwork_number(MEDIA_ROOT)
        artwork_id = ensure_artwork_id(n)

    primary_image_rel = ""
    if image and image.filename:
        content = await image.read()
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        base_name = f"{artwork_id}_front"
        primary_image_rel, _ = save_image_and_thumb(content, dest_dir, base_name)

    artist_name = "Vladislav Raszyk"
    slug = mk_slug(title, artist_name)

    a = Artwork(
        artwork_id=artwork_id,
        title=title.strip(),
        artist_name=artist_name,
        year=year.strip(),
        medium=medium.strip(),
        surface=surface.strip(),
        width_cm=width_cm,
        height_cm=height_cm,
        depth_cm=depth_cm,
        description=description.strip(),
        keywords=keywords.strip(),
        primary_image=primary_image_rel,
        web_slug=slug,
    )
    with get_session() as s:
        s.add(a)
        s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.get("/artworks/{artwork_id}")
def show_artwork(artwork_id: str, request: Request):
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return RedirectResponse(url="/", status_code=302)
        images = s.exec(select(Image).where(Image.artwork_id == artwork_id).order_by(Image.order_index)).all()
    return templates.TemplateResponse("artworks/show.html", {"request": request, "artwork": artwork, "images": images})

@app.get("/artworks/{artwork_id}/edit")
def edit_artwork(artwork_id: str, request: Request):
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("artworks/edit.html", {"request": request, "artwork": artwork})

@app.post("/artworks/{artwork_id}/edit")
async def update_artwork(
    artwork_id: str,
    title: Optional[str] = Form(None),
    year: Optional[str] = Form(None),
    medium: Optional[str] = Form(None),
    surface: Optional[str] = Form(None),
    width_cm: Optional[float] = Form(None),
    height_cm: Optional[float] = Form(None),
    depth_cm: Optional[float] = Form(None),
    description: Optional[str] = Form(None),
    keywords: Optional[str] = Form(None),
    request: Request = None,
):
    # If a client posts JSON instead of form, accept that too
    if title is None and request and request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        title = payload.get("title")
        year = payload.get("year")
        medium = payload.get("medium")
        surface = payload.get("surface")
        width_cm = payload.get("width_cm")
        height_cm = payload.get("height_cm")
        depth_cm = payload.get("depth_cm")
        description = payload.get("description")
        keywords = payload.get("keywords")

    if not title:
        return RedirectResponse(url=f"/artworks/{artwork_id}/edit", status_code=303)

    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return RedirectResponse(url="/", status_code=302)

        artwork.title = title.strip()
        artwork.year = (year or "").strip()
        artwork.medium = (medium or "").strip()
        artwork.surface = (surface or "").strip()
        artwork.width_cm = float(width_cm or 0)
        artwork.height_cm = float(height_cm or 0)
        artwork.depth_cm = float(depth_cm or 0)
        artwork.description = (description or "").strip()
        artwork.keywords = (keywords or "").strip()

        s.add(artwork)
        s.commit()

    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/images")
async def upload_images(artwork_id: str, files: List[UploadFile] = File(...), view: str = Form("detail")):
    dest_dir = MEDIA_ROOT / "artworks" / artwork_id
    idx = next_image_index(dest_dir, artwork_id)
    with get_session() as s:
        for uf in files:
            content = await uf.read()
            base_name = f"{artwork_id}_detail{idx}"
            rel, rel_thumb = save_image_and_thumb(content, dest_dir, base_name)
            img = Image(artwork_id=artwork_id, path=rel, thumb=rel_thumb, view=view, order_index=idx)
            s.add(img)
            idx += 1
        s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/images/{image_id}/delete")
def delete_image(artwork_id: str, image_id: int):
    with get_session() as s:
        img = s.exec(select(Image).where(Image.id == image_id, Image.artwork_id == artwork_id)).first()
        if img:
            p = Path(img.path.replace("/media", str(MEDIA_ROOT)))
            t = Path(img.thumb.replace("/media", str(MEDIA_ROOT)))
            try:
                if p.exists(): p.unlink()
                if t.exists(): t.unlink()
            except Exception:
                pass
            s.delete(img)
            s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/images/{image_id}/make-primary")
def make_primary_image(artwork_id: str, image_id: int):
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        img = s.exec(select(Image).where(Image.id == image_id, Image.artwork_id == artwork_id)).first()
        if artwork and img:
            artwork.primary_image = img.path
            s.add(artwork)
            s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/delete")
def delete_artwork(artwork_id: str):
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if artwork:
            imgs = s.exec(select(Image).where(Image.artwork_id == artwork_id)).all()
            for img in imgs:
                p = Path(img.path.replace("/media", str(MEDIA_ROOT)))
                t = Path(img.thumb.replace("/media", str(MEDIA_ROOT)))
                try:
                    if p.exists(): p.unlink()
                    if t.exists(): t.unlink()
                except Exception:
                    pass
                s.delete(img)
            folder = MEDIA_ROOT / "artworks" / artwork_id
            if folder.exists():
                import shutil as _shutil
                _shutil.rmtree(folder, ignore_errors=True)
            s.delete(artwork)
            s.commit()
    return RedirectResponse(url="/", status_code=303)

# -----------------------------------------------------------------------------
# One-pager PDF (robust image embedding)
# -----------------------------------------------------------------------------
@app.get("/artworks/{artwork_id}/onepager.pdf")
def onepager_pdf(artwork_id: str):
    pdf_path = _onepager_path(artwork_id)

    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "not found"}, status_code=404)
        gallery = s.exec(
            select(Image).where(Image.artwork_id == artwork_id).order_by(Image.order_index)
        ).all()

    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    W, H = A4
    margin = 20 * mm
    x, y = margin, H - margin

    # --- Header ---
    c.setFont("Helvetica-Bold", 18); c.drawString(x, y, artwork.title or "")
    y -= 8 * mm
    c.setFont("Helvetica", 12); c.drawString(x, y, f"{artwork.artist_name} · {artwork.year or ''}")
    y -= 10 * mm

    # --- Primary image (large) ---
    from io import BytesIO
    from PIL import Image as PILImage
    from reportlab.lib.utils import ImageReader

    def draw_image_box(img_path_fs: Path, box_left, box_top, box_w, box_h):
        """Draws an image fit into the box (left, top), returns drawn height."""
        try:
            with PILImage.open(img_path_fs) as im:
                im = im.convert("RGB")
                iw, ih = im.size
                scale = min(box_w / iw, box_h / ih)
                draw_w, draw_h = iw * scale, ih * scale
                buf = BytesIO()
                im.save(buf, format="JPEG", quality=90)
                buf.seek(0)
                c.drawImage(ImageReader(buf), box_left, box_top - draw_h,
                            width=draw_w, height=draw_h, preserveAspectRatio=True, mask='auto')
                return draw_h
        except Exception:
            return 0

    # Resolve primary image FS path
    primary_fs = None
    if artwork.primary_image:
        # map "/media/..." -> "/app/app/media/..."
        primary_fs = (MEDIA_ROOT / artwork.primary_image.replace("/media/", "")).resolve()
        if not primary_fs.exists():
            primary_fs = None

    # Draw primary (left); meta block on right
    if primary_fs:
        drawn_h = draw_image_box(primary_fs, x, y, 95 * mm, 95 * mm)
    else:
        drawn_h = 0

    # Meta on the right
    meta_x = x + 105 * mm
    meta_y = H - 38 * mm
    c.setFont("Helvetica", 11)
    def row(label, value):
        nonlocal meta_y
        c.setFont("Helvetica-Bold", 11); c.drawString(meta_x, meta_y, f"{label}:")
        c.setFont("Helvetica", 11); c.drawString(meta_x + 35 * mm, meta_y, str(value) if value is not None else "")
        meta_y -= 6 * mm
    row("Year", artwork.year)
    row("Medium", artwork.medium)
    row("Surface", artwork.surface)
    row("Size (cm)", f"{artwork.width_cm} × {artwork.height_cm} × {artwork.depth_cm}")
    row("Keywords", artwork.keywords)

    # Description
    c.setFont("Helvetica-Bold", 12); c.drawString(x, 40 * mm, "Description")
    c.setFont("Helvetica", 11)
    text = c.beginText(x, 34 * mm)
    for line in (artwork.description or "").splitlines() or [""]:
        text.textLine(line)
    c.drawText(text)

    # Footer on first page
    c.setFont("Helvetica", 9)
    c.drawRightString(W - margin, 15 * mm, f"Generated {artwork.created_at} · {artwork.web_slug}")
    c.showPage()

    # --- Gallery pages (all images, including primary if you want) ---
    # Build a list of paths for gallery images (skip primary path to avoid duplicate large image)
    image_paths = []
    def to_fs(rel_or_url):
        if rel_or_url and rel_or_url.startswith("/media/"):
            p = (MEDIA_ROOT / rel_or_url.replace("/media/", "")).resolve()
            if p.exists():
                return p
        return None

    # Put primary as the first thumb if you want it included; comment next two lines to skip.
    if primary_fs:
        image_paths.append(primary_fs)

    for img in gallery:
        p = to_fs(img.path)
        if p and p not in image_paths:
            image_paths.append(p)

    if image_paths:
        cols, rows = 3, 3
        gap = 6 * mm
        cell_w = (W - 2 * margin - (cols - 1) * gap) / cols
        cell_h = (H - 2 * margin - (rows - 1) * gap) / rows

        def draw_contact_sheet(start_index: int):
            idx = start_index
            for r in range(rows):
                for col in range(cols):
                    if idx >= len(image_paths):
                        return idx
                    left = margin + col * (cell_w + gap)
                    top = H - margin - r * (cell_h + gap)
                    draw_image_box(image_paths[idx], left, top, cell_w, cell_h)
                    idx += 1
            # Footer per page
            c.setFont("Helvetica", 9)
            c.drawRightString(W - margin, 12 * mm, f"Gallery page {(start_index // (cols*rows)) + 1}")
            c.showPage()
            return idx

        i = 0
        while i < len(image_paths):
            i = draw_contact_sheet(i)

    # Finalize
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=f"{artwork_id}.pdf")

# -----------------------------------------------------------------------------
# JSON API (for n8n, etc.)
# -----------------------------------------------------------------------------
from pydantic import BaseModel

class CreateArtwork(BaseModel):
    artwork_id: Optional[str] = None
    title: str
    year: str = ""
    medium: str = ""
    surface: str = ""
    width_cm: float = 0.0
    height_cm: float = 0.0
    depth_cm: float = 0.0
    description: str = ""
    keywords: str = ""
    primary_image_base64: Optional[str] = None
    primary_image_url: Optional[str] = None

@app.get("/api/artworks")
def api_list_artworks(request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        items = s.exec(select(Artwork).order_by(Artwork.id.desc())).all()
        return items

@app.get("/api/artworks/{artwork_id}")
def api_get_artwork(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "not found"}, status_code=404)
        return artwork

@app.post("/api/artworks")
async def api_create_artwork(payload: CreateArtwork, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    title = (payload.title or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)

    artwork_id = (payload.artwork_id or "").strip()
    if not artwork_id:
        n = next_artwork_number(MEDIA_ROOT)
        artwork_id = ensure_artwork_id(n)

    img_bytes = _bytes_from_payload(payload.primary_image_base64, payload.primary_image_url)
    primary_image_rel = ""
    if img_bytes:
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        base_name = f"{artwork_id}_front"
        primary_image_rel, _ = save_image_and_thumb(img_bytes, dest_dir, base_name)

    a = Artwork(
        artwork_id=artwork_id,
        title=title,
        artist_name="Vladislav Raszyk",
        year=(payload.year or "").strip(),
        medium=(payload.medium or "").strip(),
        surface=(payload.surface or "").strip(),
        width_cm=float(payload.width_cm or 0),
        height_cm=float(payload.height_cm or 0),
        depth_cm=float(payload.depth_cm or 0),
        description=(payload.description or "").strip(),
        keywords=(payload.keywords or "").strip(),
        primary_image=primary_image_rel,
        web_slug=mk_slug(title, "Vladislav Raszyk"),
    )
    with get_session() as s:
        s.add(a)
        s.commit()
    return JSONResponse({"ok": True, "artwork_id": artwork_id}, status_code=201)

@app.post("/api/artworks/{artwork_id}/images-json")
async def api_add_image(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    payload = await request.json()
    view = (payload.get("view") or "detail").strip()
    image_b64 = payload.get("image_base64")
    image_url = payload.get("image_url")
    img_bytes = _bytes_from_payload(image_b64, image_url)
    if not img_bytes:
        return JSONResponse({"error": "image_base64 or image_url required"}, status_code=400)

    dest_dir = MEDIA_ROOT / "artworks" / artwork_id
    idx = next_image_index(dest_dir, artwork_id)
    base_name = f"{artwork_id}_detail{idx}"
    rel, rel_thumb = save_image_and_thumb(img_bytes, dest_dir, base_name)

    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "not found"}, status_code=404)
        img = Image(artwork_id=artwork_id, path=rel, thumb=rel_thumb, view=view, order_index=idx)
        s.add(img)
        s.commit()
    return JSONResponse({"ok": True, "path": rel}, status_code=201)

@app.delete("/api/artworks/{artwork_id}")
def api_delete_artwork(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        artwork = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "not found"}, status_code=404)
        imgs = s.exec(select(Image).where(Image.artwork_id == artwork_id)).all()
        for img in imgs:
            p = Path(img.path.replace("/media", str(MEDIA_ROOT)))
            t = Path(img.thumb.replace("/media", str(MEDIA_ROOT)))
            try:
                if p.exists(): p.unlink()
                if t.exists(): t.unlink()
            except Exception:
                pass
            s.delete(img)
        folder = MEDIA_ROOT / "artworks" / artwork_id
        if folder.exists():
            import shutil as _shutil
            _shutil.rmtree(folder, ignore_errors=True)
        s.delete(artwork)
        s.commit()
    return JSONResponse({"ok": True})
