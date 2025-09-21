from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from sqlmodel import select
from .db import init_db, get_session, Artwork, Image
from .utils import ensure_artwork_id, next_artwork_number, save_image_and_thumb, mk_slug, next_image_index

app = FastAPI(title="Art Catalog")
BASE = Path(__file__).parent
MEDIA_ROOT = BASE / "media"
STATIC_ROOT = BASE / "static"

app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")
templates = Jinja2Templates(directory=str(BASE / "templates"))

@app.on_event("startup")
def on_startup():
    init_db()

@app.get("/")
def index(request: Request, q: str | None = None, artist: str | None = None, year_from: str | None = None, year_to: str | None = None, style: str | None = None, min_width_mm: int | None = None, max_width_mm: int | None = None):
    from sqlmodel import and_, or_
    with get_session() as s:
        stmt = select(Artwork)
        conds = []
        if q:
            like = f"%{q.strip()}%"
            conds.append(or_(Artwork.title.like(like), Artwork.artist_name.like(like), Artwork.medium.like(like), Artwork.subject_keywords.like(like), Artwork.series.like(like), Artwork.style.like(like)))
        if artist:
            conds.append(Artwork.artist_name.like(f"%{artist.strip()}%"))
        if style:
            conds.append(Artwork.style.like(f"%{style.strip()}%"))
        if year_from:
            conds.append(Artwork.year >= year_from)
        if year_to:
            conds.append(Artwork.year <= year_to)
        if min_width_mm is not None and str(min_width_mm) != "":
            conds.append(Artwork.width_mm >= int(min_width_mm))
        if max_width_mm is not None and str(max_width_mm) != "":
            conds.append(Artwork.width_mm <= int(max_width_mm))
        if conds:
            stmt = stmt.where(and_(*conds))
        stmt = stmt.order_by(Artwork.id.desc())
        artworks = s.exec(stmt).all()
    params = {"q": q or "", "artist": artist or "", "year_from": year_from or "", "year_to": year_to or "", "style": style or "", "min_width_mm": min_width_mm or "", "max_width_mm": max_width_mm or ""}
    return templates.TemplateResponse("artworks/list.html", {"request": request, "artworks": artworks, "filters": params})

@app.get("/artworks/new")
def new_artwork(request: Request):
    return templates.TemplateResponse("artworks/new.html", {"request": request})

@app.post("/artworks")
async def create_artwork(
    request: Request,
    artwork_id: str = Form(""),
    title: str = Form(...),
    artist_name: str = Form(""),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_mm: int = Form(0),
    height_mm: int = Form(0),
    depth_mm: int = Form(0),
    framed_width_mm: int = Form(0),
    framed_height_mm: int = Form(0),
    framed_depth_mm: int = Form(0),
    edition: str = Form("Unique"),
    series: str = Form(""),
    style: str = Form(""),
    subject_keywords: str = Form(""),
    provenance: str = Form(""),
    location: str = Form(""),
    inventory_code: str = Form(""),
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

    slug = mk_slug(title, artist_name)

    a = Artwork(
        artwork_id=artwork_id,
        title=title.strip(),
        artist_name=artist_name.strip(),
        year=year.strip(),
        medium=medium.strip(),
        surface=surface.strip(),
        width_mm=width_mm,
        height_mm=height_mm,
        depth_mm=depth_mm,
        framed_width_mm=framed_width_mm,
        framed_height_mm=framed_height_mm,
        framed_depth_mm=framed_depth_mm,
        edition=edition.strip(),
        series=series.strip(),
        style=style.strip(),
        subject_keywords=subject_keywords.strip(),
        provenance=provenance.strip(),
        location=location.strip(),
        inventory_code=inventory_code.strip(),
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
    artist_name: str = Form(""),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_mm: int = Form(0),
    height_mm: int = Form(0),
    depth_mm: int = Form(0),
    framed_width_mm: int = Form(0),
    framed_height_mm: int = Form(0),
    framed_depth_mm: int = Form(0),
    edition: str = Form("Unique"),
    series: str = Form(""),
    style: str = Form(""),
    subject_keywords: str = Form(""),
    provenance: str = Form(""),
    location: str = Form(""),
    inventory_code: str = Form(""),
):
    with get_session() as s:
        a = s.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not a:
            return RedirectResponse(url="/", status_code=302)
        a.title = title.strip()
        a.artist_name = artist_name.strip()
        a.year = year.strip()
        a.medium = medium.strip()
        a.surface = surface.strip()
        a.width_mm = width_mm
        a.height_mm = height_mm
        a.depth_mm = depth_mm
        a.framed_width_mm = framed_width_mm
        a.framed_height_mm = framed_height_mm
        a.framed_depth_mm = framed_depth_mm
        a.edition = edition.strip()
        a.series = series.strip()
        a.style = style.strip()
        a.subject_keywords = subject_keywords.strip()
        a.provenance = provenance.strip()
        a.location = location.strip()
        a.inventory_code = inventory_code.strip()
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

    c.setFont("Helvetica-Bold", 18)
    c.drawString(x, y, a.title)
    y -= 8*mm
    c.setFont("Helvetica", 12)
    c.drawString(x, y, f"{a.artist_name} · {a.year}")
    y -= 10*mm

    if a.primary_image:
        img_path = Path(a.primary_image.replace("/media", str(MEDIA_ROOT)))
        if img_path.exists():
            try:
                img = ImageReader(str(img_path))
                box_w, box_h = 90*mm, 90*mm
                iw, ih = img.getSize()
                scale = min(box_w/iw, box_h/ih)
                w, h = iw*scale, ih*scale
                c.drawImage(img, x, y - h, width=w, height=h, preserveAspectRatio=True, mask='auto')
            except Exception:
                pass

    meta_x = x + 100*mm
    meta_y = H - 38*mm
    c.setFont("Helvetica", 11)
    def row(label, value):
        nonlocal meta_y
        c.setFont("Helvetica-Bold", 11); c.drawString(meta_x, meta_y, f"{label}:")
        c.setFont("Helvetica", 11); c.drawString(meta_x + 35*mm, meta_y, str(value) if value is not None else "")
        meta_y -= 6*mm
    row("Artwork ID", a.artwork_id)
    row("Inventory", a.inventory_code)
    row("Medium", a.medium)
    row("Surface", a.surface)
    row("Size (mm)", f"{a.width_mm} × {a.height_mm} × {a.depth_mm}")
    row("Framed (mm)", f"{a.framed_width_mm} × {a.framed_height_mm} × {a.framed_depth_mm}")
    row("Edition", a.edition)
    row("Series", a.series)
    row("Style", a.style)
    row("Keywords", a.subject_keywords)
    row("Provenance", a.provenance)
    row("Location", a.location)

    c.setFont("Helvetica", 9)
    c.drawRightString(W - 20*mm, 15*mm, f"Generated {a.created_at} · {a.web_slug}")
    c.showPage(); c.save()
    return FileResponse(str(p), media_type="application/pdf", filename=f"{artwork_id}.pdf")
