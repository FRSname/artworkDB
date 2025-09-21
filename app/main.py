from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
import os, base64, requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from sqlmodel import select
from .db import init_db, get_session, Artwork, Image
from .utils import ensure_artwork_id, next_artwork_number, save_image_and_thumb, mk_slug, next_image_index

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

# -------- helpers --------
def _check_api_key(request: Request) -> bool:
    api_key_env = os.getenv("API_KEY", "").strip()
    if not api_key_env:
        return True  # open if not configured
    supplied = request.headers.get("X-API-Key", "")
    return supplied == api_key_env

def _bytes_from_payload(image_base64: str | None, image_url: str | None) -> bytes | None:
    data = None
    if image_base64:
        try:
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

# -------- UI routes --------
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
                Artwork.keywords.like(like)
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
        web_slug=slug
    )
    with get_session() as s:
        s.add(a)
        s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.get("/artworks/{artwork_id}")
def show_artwork(artwork_id: str, request: Request):
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return RedirectResponse(url="/", status_code=302)
        images = s.exec(select(Image).where(Image.artwork_id == artwork_id).order_by(Image.order_index)).all()
    return templates.TemplateResponse("artworks/show.html", {"request": request, "a": a, "images": images})

@app.get("/artworks/{artwork_id}/edit")
def edit_artwork(artwork_id: str, request: Request):
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("artworks/edit.html", {"request": request, "a": a})

@app.post("/artworks/{artwork_id}/edit")
async def update_artwork(
    artwork_id: str,
    request: Request,
    title: str = Form(...),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_cm: float = Form(0.0),
    height_cm: float = Form(0.0),
    depth_cm: float = Form(0.0),
    description: str = Form(""),
    keywords: str = Form(""),
):
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return RedirectResponse(url="/", status_code=302)
        a.title = title.strip()
        a.year = year.strip()
        a.medium = medium.strip()
        a.surface = surface.strip()
        a.width_cm = width_cm
        a.height_cm = height_cm
        a.depth_cm = depth_cm
        a.description = description.strip()
        a.keywords = keywords.strip()
        s.add(a)
        s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/images")
async def upload_images(artwork_id: str, files: list[UploadFile] = File(...), view: str = Form("detail")):
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
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        img = s.exec(select(Image).where(Image.id == image_id, Image.artwork_id == artwork_id)).first()
        if a and img:
            a.primary_image = img.path
            s.add(a)
            s.commit()
    return RedirectResponse(url=f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/delete")
def delete_artwork(artwork_id: str):
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if a:
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
            s.delete(a)
            s.commit()
    return RedirectResponse(url="/", status_code=303)

def _onepager_path(artwork_id: str) -> Path:
    out_dir = Path("/app/data/onepagers")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{artwork_id}.pdf"

@app.get("/artworks/{artwork_id}/onepager.pdf")
def onepager_pdf(artwork_id: str):
    p = _onepager_path(artwork_id)
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return JSONResponse({"error":"not found"}, status_code=404)

    c = canvas.Canvas(str(p), pagesize=A4)
    W, H = A4
    x, y = 20*mm, H - 20*mm

    c.setFont("Helvetica-Bold", 18); c.drawString(x, y, a.title); y -= 8*mm
    c.setFont("Helvetica", 12); c.drawString(x, y, f"{a.artist_name} · {a.year}"); y -= 10*mm

    if a.primary_image:
        img_path = Path(a.primary_image.replace("/media", str(MEDIA_ROOT)))
        if img_path.exists():
            try:
                img = ImageReader(str(img_path))
                box_w, box_h = 95*mm, 95*mm
                iw, ih = img.getSize()
                scale = min(box_w/iw, box_h/ih)
                w, h = iw*scale, ih*scale
                c.drawImage(img, x, y - h, width=w, height=h, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

    meta_x = x + 105*mm
    meta_y = H - 38*mm
    c.setFont("Helvetica", 11)
    def row(label, value):
        nonlocal meta_y
        c.setFont("Helvetica-Bold", 11); c.drawString(meta_x, meta_y, f"{label}:")
        c.setFont("Helvetica", 11); c.drawString(meta_x + 35*mm, meta_y, str(value) if value is not None else "")
        meta_y -= 6*mm
    row("Year", a.year)
    row("Medium", a.medium)
    row("Surface", a.surface)
    row("Size (cm)", f"{a.width_cm} × {a.height_cm} × {a.depth_cm}")
    row("Keywords", a.keywords)

    c.setFont("Helvetica-Bold", 12); c.drawString(x, 40*mm, "Description")
    c.setFont("Helvetica", 11); text = c.beginText(x, 34*mm)
    for line in (a.description or "").splitlines() or [""]:
        text.textLine(line)
    c.drawText(text)

    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20*mm, 15*mm, f"Generated {a.created_at} · {a.web_slug}")
    c.showPage(); c.save()
    return FileResponse(str(p), media_type="application/pdf", filename=f"{artwork_id}.pdf")

# -------- JSON API for n8n --------
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
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return JSONResponse({"error":"not found"}, status_code=404)
        return a

@app.post("/api/artworks")
async def api_create_artwork(request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    payload = await request.json()
    title = (payload.get("title") or "").strip()
    if not title:
        return JSONResponse({"error": "title is required"}, status_code=400)
    artwork_id = (payload.get("artwork_id") or "").strip()
    year = (payload.get("year") or "").strip()
    medium = (payload.get("medium") or "").strip()
    surface = (payload.get("surface") or "").strip()
    width_cm = float(payload.get("width_cm") or 0)
    height_cm = float(payload.get("height_cm") or 0)
    depth_cm = float(payload.get("depth_cm") or 0)
    description = (payload.get("description") or "").strip()
    keywords = (payload.get("keywords") or "").strip()
    image_b64 = payload.get("primary_image_base64")
    image_url = payload.get("primary_image_url")

    if not artwork_id:
        n = next_artwork_number(MEDIA_ROOT)
        artwork_id = ensure_artwork_id(n)

    primary_image_rel = ""
    img_bytes = _bytes_from_payload(image_b64, image_url)
    if img_bytes:
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        base_name = f"{artwork_id}_front"
        primary_image_rel, _ = save_image_and_thumb(img_bytes, dest_dir, base_name)

    slug = mk_slug(title, "Vladislav Raszyk")

    a = Artwork(
        artwork_id=artwork_id,
        title=title,
        artist_name="Vladislav Raszyk",
        year=year,
        medium=medium,
        surface=surface,
        width_cm=width_cm, height_cm=height_cm, depth_cm=depth_cm,
        description=description, keywords=keywords,
        primary_image=primary_image_rel, web_slug=slug
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
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return JSONResponse({"error":"not found"}, status_code=404)
        img = Image(artwork_id=artwork_id, path=rel, thumb=rel_thumb, view=view, order_index=idx)
        s.add(img); s.commit()
    return JSONResponse({"ok": True, "path": rel}, status_code=201)

@app.delete("/api/artworks/{artwork_id}")
def api_delete_artwork(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return JSONResponse({"error":"not found"}, status_code=404)
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
        s.delete(a); s.commit()
    return JSONResponse({"ok": True})
