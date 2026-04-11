"""Image Library Database — SQLite schema and operations."""
import sqlite3
import hashlib
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'image_library.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created TEXT DEFAULT (datetime('now')),
            modified TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_filename TEXT,
            title TEXT NOT NULL,
            creator TEXT,
            date TEXT,
            description TEXT,
            narrative TEXT,
            source_url TEXT,
            rights TEXT,
            medium TEXT DEFAULT 'Photograph',
            theme_id INTEGER REFERENCES themes(id),
            apa_citation TEXT,
            file_size INTEGER,
            width INTEGER,
            height INTEGER,
            mimetype TEXT,
            created TEXT DEFAULT (datetime('now')),
            modified TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER REFERENCES images(id) ON DELETE CASCADE,
            tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (image_id, tag_id)
        );

        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            creator TEXT,
            date TEXT,
            description TEXT,
            url TEXT NOT NULL,
            media_type TEXT DEFAULT 'video',
            source TEXT,
            rights TEXT,
            apa_citation TEXT,
            theme_id INTEGER REFERENCES themes(id),
            created TEXT DEFAULT (datetime('now')),
            modified TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS media_tags (
            media_id INTEGER REFERENCES media(id) ON DELETE CASCADE,
            tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (media_id, tag_id)
        );
    """)

    # Seed default themes
    default_themes = [
        ('Black Archives and Collections', 'Archival materials, digital collections, and preservation efforts'),
        ('Black Art and Museums', 'Visual art, installations, museum exhibitions, and cultural institutions'),
        ('Black Feminism and Intersectionality', 'Feminist theory, misogynoir, controlling images, intersectional analysis'),
        ('Black Film', 'Film and television: posters, stills, and production images'),
        ('Black Music', 'Musical traditions, album covers, performance photography, instruments'),
        ('Black Representation and Stereotypes', 'Media stereotypes, controlling images, and their evolution'),
        ('Civil Rights and Activism', 'Protest, organizing, marches, legal battles, and movement photography'),
        ('Negro Leagues', 'Negro Leagues baseball: teams, players, stadiums, and cultural impact'),
        ('Portraits and People', 'Individual and group portraits with historical or cultural significance'),
    ]
    for name, desc in default_themes:
        c.execute("INSERT OR IGNORE INTO themes (name, description) VALUES (?, ?)", (name, desc))

    # Migration: add content_hash column for duplicate detection
    existing = [r[1] for r in c.execute("PRAGMA table_info(images)").fetchall()]
    if 'content_hash' not in existing:
        c.execute("ALTER TABLE images ADD COLUMN content_hash TEXT")

    conn.commit()

    # Backfill hashes for existing images missing them
    upload_dir = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
    unhashed = conn.execute("SELECT id, filename FROM images WHERE content_hash IS NULL").fetchall()
    for row in unhashed:
        filepath = os.path.join(upload_dir, row['filename'])
        if os.path.exists(filepath):
            h = compute_file_hash(filepath)
            conn.execute("UPDATE images SET content_hash=? WHERE id=?", (h, row['id']))
    if unhashed:
        conn.commit()

    conn.close()

def compute_file_hash(filepath):
    """Compute SHA256 hash of a file for duplicate detection."""
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def generate_apa_citation(image):
    """Generate APA 7th edition citation for an image."""
    creator = image['creator'] or 'Unknown'
    date = image['date'] or 'n.d.'
    title = image['title'] or 'Untitled'
    medium = image['medium'] or 'Photograph'
    source = image['source_url'] or ''

    # APA 7th for artwork/image:
    # Creator, A. A. (Year). Title of work [Medium]. Source. URL
    if '(' in date:
        year = date
    elif len(date) == 4 and date.isdigit():
        year = f"({date})"
    elif date == 'n.d.':
        year = '(n.d.)'
    else:
        year = f"({date})"

    citation = f"{creator}. {year}. {title} [{medium}]."
    if source:
        citation += f" {source}"

    return citation
