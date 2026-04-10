"""
Image Library — Auto-Processing Pipeline

When Raymond uploads images, this script:
1. Reads the image visually (via Claude Code's Read tool)
2. Identifies the subject
3. Writes a title, description, narrative, and tags
4. Assigns a theme
5. Generates APA citation
6. Updates the database

Usage: Called by Claude Code after images are uploaded.
    python process_image.py <image_id>
    python process_image.py --scan-new  (processes all images without titles)
"""
import sqlite3
import os
import sys
from database import get_db, generate_apa_citation

DB_PATH = os.path.join(os.path.dirname(__file__), 'image_library.db')
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')

# Course themes and their relevance
COURSE_THEMES = {
    'BFS142': 'Race, Resistance, and the Black Athlete — Negro Leagues, sports as resistance, athletic activism, racial barriers in sport',
    'BFS211': 'Black Music and Social Liberation — blues, jazz, gospel, hip-hop, music as protest, cultural expression, Smithsonian Folkways',
    'BFS218': 'Techno-Racism — algorithmic bias, surveillance, digital divide, AI ethics, facial recognition, coded gaze',
    'BFS220': 'Black Popular Culture — media representation, stereotypes, controlling images, misogynoir, Blaxploitation, film, television',
    'SOC122': 'Introduction to Social Sciences — sociological imagination, Indigenous knowledge, Two-Eyed Seeing, social structures',
}


def get_unprocessed_images():
    """Find images that need processing (no description or narrative)."""
    db = get_db()
    images = db.execute("""SELECT id, filename, title FROM images
                           WHERE (description IS NULL OR description = '')
                           AND filename IS NOT NULL""").fetchall()
    db.close()
    return images


def update_image_metadata(image_id, title=None, creator=None, date=None, description=None,
                          narrative=None, source_url=None, rights=None, medium=None,
                          theme_id=None, tags=None):
    """Update image metadata in the database."""
    db = get_db()

    updates = []
    params = []

    if title is not None:
        updates.append("title=?")
        params.append(title)
    if creator is not None:
        updates.append("creator=?")
        params.append(creator)
    if date is not None:
        updates.append("date=?")
        params.append(date)
    if description is not None:
        updates.append("description=?")
        params.append(description)
    if narrative is not None:
        updates.append("narrative=?")
        params.append(narrative)
    if source_url is not None:
        updates.append("source_url=?")
        params.append(source_url)
    if rights is not None:
        updates.append("rights=?")
        params.append(rights)
    if medium is not None:
        updates.append("medium=?")
        params.append(medium)
    if theme_id is not None:
        updates.append("theme_id=?")
        params.append(theme_id)

    if updates:
        updates.append("modified=datetime('now')")
        query = f"UPDATE images SET {', '.join(updates)} WHERE id=?"
        params.append(image_id)
        db.execute(query, params)

    # Tags
    if tags:
        db.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        for tag_name in tags:
            row = db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
            if row:
                tid = row['id']
            else:
                db.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,))
                tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)", (image_id, tid))

    # Generate APA citation
    image = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if image:
        apa = generate_apa_citation(image)
        db.execute("UPDATE images SET apa_citation=? WHERE id=?", (apa, image_id))

    db.commit()
    db.close()


def get_theme_id_by_name(name):
    """Get theme ID by name."""
    db = get_db()
    row = db.execute("SELECT id FROM themes WHERE name=?", (name,)).fetchone()
    db.close()
    return row['id'] if row else None


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python process_image.py <image_id> | --scan-new")
        sys.exit(1)

    if sys.argv[1] == '--scan-new':
        images = get_unprocessed_images()
        print(f"Found {len(images)} unprocessed images:")
        for img in images:
            print(f"  ID={img['id']}: {img['filename']}")
    else:
        image_id = int(sys.argv[1])
        db = get_db()
        image = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        db.close()
        if image:
            print(f"Image {image_id}: {image['filename']}")
            print(f"  Path: {os.path.join(UPLOAD_DIR, image['filename'])}")
            print(f"  Title: {image['title']}")
            print(f"  Has description: {bool(image['description'])}")
            print(f"  Has narrative: {bool(image['narrative'])}")
        else:
            print(f"Image {image_id} not found")
