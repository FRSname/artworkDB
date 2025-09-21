import os
import json
import base64
import requests
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from io import BytesIO

from fastapi import FastAPI, Depends, HTTPException, Form, UploadFile, File, Request
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from pydantic import BaseModel
from typing import Annotated

from .db import Artwork, Image, init_db, get_session
from .utils import (
    ensure_artwork_id, next_artwork_number, next_image_index,
    save_image_and_thumb, mk_slug
)

# Pydantic models for API
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

app = FastAPI(title="Art Catalog API", version="1.0.0")

# Initialize database
init_db()

# Setup paths
MEDIA_ROOT = Path("/app/app/media")
TEMPLATES_DIR = Path("/app/app/templates")
STATIC_DIR = Path("/app/app/static")

# Create required directories
MEDIA_ROOT.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)

# Mount static files
app.mount("/media", StaticFiles(directory=str(MEDIA_ROOT)), name="media")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# API Key protection (optional)
API_KEY = os.getenv("API_KEY")

def _check_api_key(request: Request) -> bool:
    """Check API key for JSON responses"""
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    return key == API_KEY

def check_api_key(request: Request):
    """Check API key for FastAPI dependency"""
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if key != API_KEY:
        raise HTTPException(401, "Invalid or missing API key")
    return True

def _bytes_from_payload(image_b64: Optional[str], image_url: Optional[str]) -> Optional[bytes]:
    """Get image bytes from base64 or URL"""
    if image_b64:
        try:
            return base64.b64decode(image_b64)
        except Exception:
            return None
    elif image_url:
        try:
            response = requests.get(image_url, timeout=10)
            response.raise_for_status()
            return response.content
        except Exception:
            return None
    return None

# Web UI Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request, session: Session = Depends(get_session)):
    artworks = session.exec(select(Artwork).order_by(Artwork.created_at.desc())).all()
    return templates.TemplateResponse("artworks/list.html", {
        "request": request,
        "artworks": artworks,
        "page_title": "Art Catalog"
    })

@app.get("/artworks", response_class=HTMLResponse)
async def list_artworks(request: Request, session: Session = Depends(get_session)):
    artworks = session.exec(select(Artwork).order_by(Artwork.created_at.desc())).all()
    return templates.TemplateResponse("artworks/list.html", {
        "request": request,
        "artworks": artworks,
        "page_title": "All Artworks"
    })

@app.get("/artworks/new", response_class=HTMLResponse)
async def new_artwork_form(request: Request):
    next_num = next_artwork_number(MEDIA_ROOT)
    next_id = ensure_artwork_id(next_num)
    return templates.TemplateResponse("artworks/new.html", {
        "request": request,
        "next_artwork_id": next_id,
        "page_title": "Add New Artwork"
    })

@app.post("/artworks/new")
async def create_artwork(
    artwork_id: str = Form(),
    title: str = Form(),
    artist_name: str = Form("Vladislav Raszyk"),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_cm: float = Form(0.0),
    height_cm: float = Form(0.0),
    depth_cm: float = Form(0.0),
    description: str = Form(""),
    keywords: str = Form(""),
    images: List[UploadFile] = File(default=[]),
    session: Session = Depends(get_session)
):
    # Check if artwork_id already exists
    existing = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if existing:
        raise HTTPException(400, f"Artwork ID {artwork_id} already exists")
    
    # Create artwork
    slug = mk_slug(title, artist_name)
    artwork = Artwork(
        artwork_id=artwork_id,
        title=title,
        artist_name=artist_name,
        year=year,
        medium=medium,
        surface=surface,
        width_cm=width_cm,
        height_cm=height_cm,
        depth_cm=depth_cm,
        description=description,
        keywords=keywords,
        web_slug=slug
    )
    
    session.add(artwork)
    session.commit()
    session.refresh(artwork)
    
    # Handle image uploads
    if images and images[0].filename:
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        for idx, img in enumerate(images):
            if img.filename:
                content = await img.read()
                if idx == 0:
                    # Primary image
                    rel_path, rel_thumb = save_image_and_thumb(
                        content, dest_dir, f"{artwork_id}_primary"
                    )
                    artwork.primary_image = rel_path
                    
                    image_record = Image(
                        artwork_id=artwork_id,
                        path=rel_path,
                        thumb=rel_thumb,
                        view="primary",
                        order_index=0
                    )
                else:
                    # Detail images
                    detail_idx = next_image_index(dest_dir, artwork_id)
                    rel_path, rel_thumb = save_image_and_thumb(
                        content, dest_dir, f"{artwork_id}_detail{detail_idx}"
                    )
                    
                    image_record = Image(
                        artwork_id=artwork_id,
                        path=rel_path,
                        thumb=rel_thumb,
                        view="detail",
                        order_index=detail_idx
                    )
                
                session.add(image_record)
        
        session.commit()
        session.refresh(artwork)
    
    return RedirectResponse(f"/artworks/{artwork_id}", status_code=303)

@app.get("/artworks/{artwork_id}", response_class=HTMLResponse)
async def show_artwork(artwork_id: str, request: Request, session: Session = Depends(get_session)):
    artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if not artwork:
        raise HTTPException(404, "Artwork not found")
    
    images = session.exec(
        select(Image)
        .where(Image.artwork_id == artwork_id)
        .order_by(Image.order_index)
    ).all()
    
    return templates.TemplateResponse("artworks/show.html", {
        "request": request,
        "artwork": artwork,
        "images": images,
        "page_title": f"{artwork.title} - {artwork.artist_name}"
    })

@app.get("/artworks/{artwork_id}/edit", response_class=HTMLResponse)
async def edit_artwork_form(artwork_id: str, request: Request, session: Session = Depends(get_session)):
    artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if not artwork:
        raise HTTPException(404, "Artwork not found")
    
    images = session.exec(
        select(Image)
        .where(Image.artwork_id == artwork_id)
        .order_by(Image.order_index)
    ).all()
    
    return templates.TemplateResponse("artworks/edit.html", {
        "request": request,
        "artwork": artwork,
        "images": images,
        "page_title": f"Edit {artwork.title}"
    })

@app.post("/artworks/{artwork_id}/edit")
async def update_artwork(
    artwork_id: str,
    title: str = Form(),
    artist_name: str = Form(),
    year: str = Form(""),
    medium: str = Form(""),
    surface: str = Form(""),
    width_cm: float = Form(0.0),
    height_cm: float = Form(0.0),
    depth_cm: float = Form(0.0),
    description: str = Form(""),
    keywords: str = Form(""),
    new_images: List[UploadFile] = File(default=[]),
    session: Session = Depends(get_session)
):
    artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if not artwork:
        raise HTTPException(404, "Artwork not found")
    
    # Update artwork fields
    artwork.title = title
    artwork.artist_name = artist_name
    artwork.year = year
    artwork.medium = medium
    artwork.surface = surface
    artwork.width_cm = width_cm
    artwork.height_cm = height_cm
    artwork.depth_cm = depth_cm
    artwork.description = description
    artwork.keywords = keywords
    artwork.web_slug = mk_slug(title, artist_name)
    
    # Handle new image uploads
    if new_images and new_images[0].filename:
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        for img in new_images:
            if img.filename:
                content = await img.read()
                detail_idx = next_image_index(dest_dir, artwork_id)
                rel_path, rel_thumb = save_image_and_thumb(
                    content, dest_dir, f"{artwork_id}_detail{detail_idx}"
                )
                
                image_record = Image(
                    artwork_id=artwork_id,
                    path=rel_path,
                    thumb=rel_thumb,
                    view="detail",
                    order_index=detail_idx
                )
                session.add(image_record)
    
    session.commit()
    return RedirectResponse(f"/artworks/{artwork_id}", status_code=303)

@app.post("/artworks/{artwork_id}/delete")
async def delete_artwork(artwork_id: str, session: Session = Depends(get_session)):
    artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if not artwork:
        raise HTTPException(404, "Artwork not found")
    
    # Delete associated images from database
    images = session.exec(select(Image).where(Image.artwork_id == artwork_id)).all()
    for image in images:
        session.delete(image)
    
    # Delete artwork
    session.delete(artwork)
    session.commit()
    
    # Delete media files
    artwork_dir = MEDIA_ROOT / "artworks" / artwork_id
    if artwork_dir.exists():
        import shutil
        shutil.rmtree(artwork_dir)
    
    return RedirectResponse("/artworks", status_code=303)

@app.post("/images/{image_id}/delete")
async def delete_image(image_id: int, session: Session = Depends(get_session)):
    image = session.exec(select(Image).where(Image.id == image_id)).first()
    if not image:
        raise HTTPException(404, "Image not found")
    
    artwork_id = image.artwork_id
    
    # Delete files
    if image.path:
        img_path = MEDIA_ROOT / image.path.lstrip("/media/")
        if img_path.exists():
            img_path.unlink()
    
    if image.thumb:
        thumb_path = MEDIA_ROOT / image.thumb.lstrip("/media/")
        if thumb_path.exists():
            thumb_path.unlink()
    
    # Remove from database
    session.delete(image)
    session.commit()
    
    return RedirectResponse(f"/artworks/{artwork_id}/edit", status_code=303)

@app.get("/artworks/{artwork_id}/pdf")
async def artwork_pdf(artwork_id: str, session: Session = Depends(get_session)):
    artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
    if not artwork:
        raise HTTPException(404, "Artwork not found")
    
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Title and basic info
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, height - 50, f"{artwork.title}")
    
    c.setFont("Helvetica", 12)
    y_pos = height - 80
    
    info_lines = [
        f"Artist: {artwork.artist_name}",
        f"ID: {artwork.artwork_id}",
        f"Year: {artwork.year}" if artwork.year else "",
        f"Medium: {artwork.medium}" if artwork.medium else "",
        f"Surface: {artwork.surface}" if artwork.surface else "",
        f"Dimensions: {artwork.width_cm} x {artwork.height_cm}" + (f" x {artwork.depth_cm}" if artwork.depth_cm > 0 else "") + " cm" if artwork.width_cm > 0 or artwork.height_cm > 0 else "",
    ]
    
    for line in info_lines:
        if line:
            c.drawString(50, y_pos, line)
            y_pos -= 20
    
    # Description
    if artwork.description:
        y_pos -= 10
        c.drawString(50, y_pos, "Description:")
        y_pos -= 15
        # Simple text wrapping
        words = artwork.description.split()
        line = ""
        for word in words:
            if len(line + word) < 80:
                line += word + " "
            else:
                c.drawString(70, y_pos, line)
                y_pos -= 15
                line = word + " "
        if line:
            c.drawString(70, y_pos, line)
            y_pos -= 20
    
    # Primary image
    if artwork.primary_image:
        img_path = MEDIA_ROOT / artwork.primary_image.lstrip("/media/")
        if img_path.exists():
            try:
                # Add image to PDF
                img_reader = ImageReader(str(img_path))
                img_width, img_height = img_reader.getSize()
                
                # Calculate size to fit on page
                max_width = 400
                max_height = y_pos - 100
                
                if img_width > max_width or img_height > max_height:
                    scale = min(max_width / img_width, max_height / img_height)
                    img_width *= scale
                    img_height *= scale
                
                c.drawImage(str(img_path), 50, y_pos - img_height, img_width, img_height)
            except Exception:
                pass
    
    c.save()
    buffer.seek(0)
    
    return Response(
        buffer.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={artwork.artwork_id}_{artwork.web_slug}.pdf"}
    )

# REST API Routes
@app.get("/api/artworks")
async def api_list_artworks(request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    with get_session() as session:
        artworks = session.exec(select(Artwork).order_by(Artwork.created_at.desc())).all()
        return {"artworks": [artwork.dict() for artwork in artworks]}

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

    # Check if artwork_id already exists
    with get_session() as session:
        existing = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if existing:
            return JSONResponse({"error": f"Artwork ID {artwork_id} already exists"}, status_code=400)

    # image bytes (either base64 or remote URL)
    image_b64 = payload.primary_image_base64
    image_url = payload.primary_image_url
    img_bytes = _bytes_from_payload(image_b64, image_url)

    primary_image_rel = ""
    if img_bytes:
        dest_dir = MEDIA_ROOT / "artworks" / artwork_id
        base_name = f"{artwork_id}_front"
        primary_image_rel, _ = save_image_and_thumb(img_bytes, dest_dir, base_name)

    a = Artwork(
        artwork_id=artwork_id,
        title=title,
        artist_name="Vladislav Raszyk",         # fixed
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
    
    with get_session() as session:
        session.add(a)
        session.commit()
        session.refresh(a)

        # Create image record if we have an image
        if primary_image_rel:
            image_record = Image(
                artwork_id=artwork_id,
                path=primary_image_rel,
                thumb=primary_image_rel.replace(".jpg", "_thumb.jpg"),
                view="primary",
                order_index=0
            )
            session.add(image_record)
            session.commit()

    return JSONResponse({"ok": True, "artwork_id": artwork_id}, status_code=201)

@app.post("/api/artworks-multipart")
async def api_create_artwork_multipart(
    request: Request,
    title: Annotated[str, Form()],
    artwork_id: Annotated[str, Form()] = None,
    year: Annotated[str, Form()] = "",
    medium: Annotated[str, Form()] = "",
    surface: Annotated[str, Form()] = "",
    width_cm: Annotated[float, Form()] = 0.0,
    height_cm: Annotated[float, Form()] = 0.0,
    depth_cm: Annotated[float, Form()] = 0.0,
    description: Annotated[str, Form()] = "",
    keywords: Annotated[str, Form()] = "",
    primary_image_base64: Annotated[str, Form()] = None,
    primary_image_url: Annotated[str, Form()] = None,
):
    # internally call the same logic as the JSON route
    payload = CreateArtwork(
        artwork_id=artwork_id, title=title, year=year, medium=medium, surface=surface,
        width_cm=width_cm, height_cm=height_cm, depth_cm=depth_cm,
        description=description, keywords=keywords,
        primary_image_base64=primary_image_base64, primary_image_url=primary_image_url
    )
    return await api_create_artwork(payload, request)

@app.get("/api/artworks/{artwork_id}")
async def api_get_artwork(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    
    with get_session() as session:
        artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "Artwork not found"}, status_code=404)
        
        images = session.exec(
            select(Image)
            .where(Image.artwork_id == artwork_id)
            .order_by(Image.order_index)
        ).all()
        
        return {
            "artwork": artwork.dict(),
            "images": [img.dict() for img in images]
        }

@app.put("/api/artworks/{artwork_id}")
async def api_update_artwork(
    artwork_id: str,
    artwork_data: dict,
    request: Request
):
    """Update artwork via JSON payload"""
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
        
    with get_session() as session:
        artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "Artwork not found"}, status_code=404)
        
        # Update fields if provided
        if "title" in artwork_data:
            artwork.title = artwork_data["title"]
        if "year" in artwork_data:
            artwork.year = artwork_data["year"]
        if "medium" in artwork_data:
            artwork.medium = artwork_data["medium"]
        if "surface" in artwork_data:
            artwork.surface = artwork_data["surface"]
        if "width_cm" in artwork_data:
            artwork.width_cm = artwork_data["width_cm"]
        if "height_cm" in artwork_data:
            artwork.height_cm = artwork_data["height_cm"]
        if "depth_cm" in artwork_data:
            artwork.depth_cm = artwork_data["depth_cm"]
        if "description" in artwork_data:
            artwork.description = artwork_data["description"]
        if "keywords" in artwork_data:
            artwork.keywords = artwork_data["keywords"]
        
        # Update slug if title changed
        if "title" in artwork_data:
            artwork.web_slug = mk_slug(artwork.title, "Vladislav Raszyk")
        
        session.commit()
        session.refresh(artwork)
        
        return {
            "message": "Artwork updated successfully",
            "artwork": artwork.dict()
        }

@app.get("/api/artworks/{artwork_id}/images-json")
async def api_artwork_images(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
        
    with get_session() as session:
        images = session.exec(
            select(Image)
            .where(Image.artwork_id == artwork_id)
            .order_by(Image.order_index)
        ).all()
        
        return {"images": [img.dict() for img in images]}

@app.delete("/api/artworks/{artwork_id}")
async def api_delete_artwork(artwork_id: str, request: Request):
    if not _check_api_key(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
        
    with get_session() as session:
        artwork = session.exec(select(Artwork).where(Artwork.artwork_id == artwork_id)).first()
        if not artwork:
            return JSONResponse({"error": "Artwork not found"}, status_code=404)
        
        # Delete associated images
        images = session.exec(select(Image).where(Image.artwork_id == artwork_id)).all()
        for image in images:
            session.delete(image)
        
        session.delete(artwork)
        session.commit()
        
        # Delete media files
        artwork_dir = MEDIA_ROOT / "artworks" / artwork_id
        if artwork_dir.exists():
            import shutil
            shutil.rmtree(artwork_dir)
        
        return {"message": "Artwork deleted successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)