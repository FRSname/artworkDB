from sqlmodel import SQLModel, Field, create_engine, Session
from typing import Optional
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/app/data/catalog.sqlite")
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

class Artwork(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    artwork_id: str = Field(index=True, unique=True)
    title: str
    artist_name: str = ""
    year: str = ""
    medium: str = ""
    surface: str = ""
    width_mm: int = 0
    height_mm: int = 0
    depth_mm: int = 0
    framed_width_mm: int = 0
    framed_height_mm: int = 0
    framed_depth_mm: int = 0
    edition: str = "Unique"
    series: str = ""
    style: str = ""
    subject_keywords: str = ""
    provenance: str = ""
    location: str = ""
    inventory_code: str = ""
    primary_image: str = ""  # relative path under /media
    web_slug: str = Field(index=True, default="")
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Image(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    artwork_id: str = Field(index=True, foreign_key="artwork.artwork_id")
    path: str = ""
    thumb: str = ""
    view: str = ""
    order_index: int = 0

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)

def get_session():
    return Session(engine)
