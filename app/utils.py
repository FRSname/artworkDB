from pathlib import Path
from PIL import Image
from slugify import slugify

def ensure_artwork_id(next_number: int) -> str:
    return f"A{next_number:04d}"

def next_artwork_number(media_root: Path) -> int:
    existing = []
    for p in media_root.glob("artworks/*"):
        name = p.name
        if name.startswith("A") and name[1:5].isdigit():
            existing.append(int(name[1:5]))
    return (max(existing) + 1) if existing else 1

def next_image_index(dest_dir: Path, artwork_id: str) -> int:
    idx = 0
    for p in dest_dir.glob(f"{artwork_id}_detail*.jpg"):
        part = p.stem.split("_detail")
        if len(part) == 2 and part[1].isdigit():
            idx = max(idx, int(part[1]))
    return idx + 1

def save_image_and_thumb(image_bytes: bytes, dest_dir: Path, base_name: str) -> tuple[str, str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    img_path = dest_dir / f"{base_name}.jpg"
    with open(img_path, "wb") as f:
        f.write(image_bytes)
    thumb_dir = dest_dir / "thumbs"
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / f"{base_name}_thumb.jpg"
    try:
        im = Image.open(img_path).convert("RGB")
        im.thumbnail((1600, 1600))
        im.save(img_path, quality=90, optimize=True)
        im2 = Image.open(img_path)
        im2.thumbnail((400, 400))
        im2.save(thumb_path, quality=85, optimize=True)
    except Exception:
        pass
    rel = f"/media/artworks/{dest_dir.name}/{img_path.name}"
    rel_thumb = f"/media/artworks/{dest_dir.name}/thumbs/{thumb_path.name}"
    return rel, rel_thumb

def mk_slug(title: str, artist: str) -> str:
    return slugify(f"{title}-{artist}")
