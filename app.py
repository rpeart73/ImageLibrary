"""Image Library — Flask application."""
import os
import hashlib
import requests
import re
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, flash
from werkzeug.utils import secure_filename
from PIL import Image as PILImage

from database import get_db, init_db, generate_apa_citation, DB_PATH

app = Flask(__name__)
app.secret_key = os.urandom(24)

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'tiff', 'bmp'}
PROTON_IMG_BASE = '/mnt/c/Users/rpeart/Proton Drive/raymondpeart/My files/EXT/Image_Library'
TRESORIT_IMG_BASE = '/mnt/c/Users/rpeart/Tresorit/Tresorit Ecosystem/Back_up_Filing Cabinet/EXT/Image_Library'

os.makedirs(UPLOAD_DIR, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


@app.route('/')
def index():
    db = get_db()
    theme_filter = request.args.get('theme')
    tag_filter = request.args.get('tag')
    search = request.args.get('q')

    query = "SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE 1=1"
    params = []

    if theme_filter:
        query += " AND t.name = ?"
        params.append(theme_filter)
    if tag_filter:
        query += " AND i.id IN (SELECT image_id FROM image_tags it JOIN tags tg ON it.tag_id = tg.id WHERE tg.name = ?)"
        params.append(tag_filter)
    if search:
        query += " AND (i.title LIKE ? OR i.description LIKE ? OR i.narrative LIKE ? OR i.creator LIKE ?)"
        params.extend([f'%{search}%'] * 4)

    # Count total for pagination
    count_query = query.replace("SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id", "SELECT COUNT(*) FROM images i LEFT JOIN themes t ON i.theme_id = t.id")
    total_count = db.execute(count_query, params).fetchone()[0]

    # Pagination
    page = request.args.get('page', 1, type=int)
    per_page = 24
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    query += " ORDER BY i.modified DESC LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    images = db.execute(query, params).fetchall()

    themes = db.execute("SELECT * FROM themes ORDER BY name").fetchall()
    tags = db.execute("SELECT DISTINCT tg.name FROM tags tg JOIN image_tags it ON tg.id = it.tag_id ORDER BY tg.name").fetchall()

    stats = {
        'total_images': total_count,
        'total_themes': db.execute("SELECT COUNT(*) FROM themes").fetchone()[0],
        'total_tags': db.execute("SELECT COUNT(DISTINCT tag_id) FROM image_tags").fetchone()[0],
    }

    db.close()
    return render_template('index.html', images=images, themes=themes, tags=tags, stats=stats,
                           current_theme=theme_filter, current_tag=tag_filter, search=search,
                           page=page, total_pages=total_pages)


@app.route('/image/<int:image_id>')
def image_detail(image_id):
    db = get_db()
    image = db.execute("SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE i.id = ?", (image_id,)).fetchone()
    if not image:
        return "Not found", 404

    tags = db.execute("SELECT tg.name FROM tags tg JOIN image_tags it ON tg.id = it.tag_id WHERE it.image_id = ?", (image_id,)).fetchall()
    apa = generate_apa_citation(image)

    # Course relevance
    course_relevance = db.execute("""SELECT cr.relevance, cr.fit, c.code, c.name
                                     FROM image_course_relevance cr
                                     JOIN courses c ON cr.course_id = c.id
                                     WHERE cr.image_id = ?
                                     ORDER BY CASE cr.fit WHEN 'strong' THEN 1 WHEN 'moderate' THEN 2 ELSE 3 END""",
                                  (image_id,)).fetchall()

    db.close()
    return render_template('detail.html', image=image, tags=tags, apa=apa, course_relevance=course_relevance)


@app.route('/image/<int:image_id>/edit', methods=['GET', 'POST'])
def image_edit(image_id):
    db = get_db()
    if request.method == 'POST':
        db.execute("""UPDATE images SET title=?, creator=?, date=?, description=?, narrative=?,
                      source_url=?, rights=?, medium=?, theme_id=?, modified=datetime('now')
                      WHERE id=?""",
                   (request.form['title'], request.form['creator'], request.form['date'],
                    request.form['description'], request.form['narrative'],
                    request.form['source_url'], request.form['rights'], request.form['medium'],
                    request.form.get('theme_id') or None, image_id))

        # Update tags
        db.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        tag_str = request.form.get('tags', '')
        for tag_name in [t.strip() for t in tag_str.split(',') if t.strip()]:
            row = db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
            if row:
                tid = row['id']
            else:
                db.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,))
                tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)", (image_id, tid))

        db.commit()
        db.close()
        return redirect(url_for('image_detail', image_id=image_id))

    image = db.execute("SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE i.id = ?", (image_id,)).fetchone()
    tags = db.execute("SELECT tg.name FROM tags tg JOIN image_tags it ON tg.id = it.tag_id WHERE it.image_id = ?", (image_id,)).fetchall()
    themes = db.execute("SELECT * FROM themes ORDER BY name").fetchall()
    tag_str = ', '.join(t['name'] for t in tags)
    db.close()
    return render_template('edit.html', image=image, themes=themes, tag_str=tag_str)


@app.route('/upload', methods=['GET', 'POST'])
def upload():
    db = get_db()
    uploaded = []

    if request.method == 'POST':
        files = request.files.getlist('files')

        for file in files:
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                filepath = os.path.join(UPLOAD_DIR, filename)

                base, ext = os.path.splitext(filename)
                counter = 1
                while os.path.exists(filepath):
                    filename = f"{base}_{counter}{ext}"
                    filepath = os.path.join(UPLOAD_DIR, filename)
                    counter += 1

                file.save(filepath)

                try:
                    with PILImage.open(filepath) as img:
                        w, h = img.size
                except:
                    w, h = 0, 0

                fsize = os.path.getsize(filepath)
                mimetype = file.content_type

                db.execute("""INSERT INTO images (filename, original_filename, title, file_size, width, height, mimetype)
                              VALUES (?, ?, ?, ?, ?, ?, ?)""",
                           (filename, file.filename, base, fsize, w, h, mimetype))

                uploaded.append({'filename': filename, 'file_size': fsize, 'width': w, 'height': h})

        db.commit()

        if len(uploaded) == 1:
            # Single file — go to detail
            image = db.execute("SELECT id FROM images WHERE filename=?", (uploaded[0]['filename'],)).fetchone()
            db.close()
            return redirect(url_for('image_detail', image_id=image['id']))

    db.close()
    return render_template('upload.html', uploaded=uploaded if uploaded else None)


@app.route('/import-url', methods=['GET', 'POST'])
def import_url():
    db = get_db()
    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        title = ''
        theme_id = None
        tag_str = ''

        if url:
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
                }
                resp = requests.get(url, headers=headers, timeout=15, stream=True)
                resp.raise_for_status()

                content_type = resp.headers.get('content-type', '')
                if 'image' not in content_type:
                    flash('URL did not return an image.', 'error')
                    db.close()
                    return render_template('import_url.html')

                # Determine filename
                ext = 'jpg'
                if 'png' in content_type:
                    ext = 'png'
                elif 'webp' in content_type:
                    ext = 'webp'
                elif 'gif' in content_type:
                    ext = 'gif'

                if title:
                    safe_title = re.sub(r'[^\w\s\-]', '', title).replace(' ', '_')
                else:
                    parsed = urlparse(url)
                    safe_title = os.path.splitext(os.path.basename(parsed.path))[0] or 'imported'
                    safe_title = re.sub(r'[^\w\s\-]', '', safe_title)

                filename = f"{safe_title}.{ext}"
                filepath = os.path.join(UPLOAD_DIR, filename)

                counter = 1
                base = safe_title
                while os.path.exists(filepath):
                    filename = f"{base}_{counter}.{ext}"
                    filepath = os.path.join(UPLOAD_DIR, filename)
                    counter += 1

                with open(filepath, 'wb') as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)

                # Get dimensions
                try:
                    with PILImage.open(filepath) as img:
                        w, h = img.size
                except:
                    w, h = 0, 0

                fsize = os.path.getsize(filepath)

                db.execute("""INSERT INTO images (filename, original_filename, title, source_url, theme_id,
                              file_size, width, height, mimetype)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (filename, os.path.basename(url), title or safe_title, url, theme_id,
                            fsize, w, h, content_type))
                image_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

                for tag_name in [t.strip() for t in tag_str.split(',') if t.strip()]:
                    row = db.execute("SELECT id FROM tags WHERE name=?", (tag_name,)).fetchone()
                    if row:
                        tid = row['id']
                    else:
                        db.execute("INSERT INTO tags (name) VALUES (?)", (tag_name,))
                        tid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                    db.execute("INSERT OR IGNORE INTO image_tags (image_id, tag_id) VALUES (?, ?)", (image_id, tid))

                db.commit()
                db.close()
                return redirect(url_for('image_detail', image_id=image_id))

            except Exception as e:
                flash(f'Import failed: {str(e)}', 'error')

    db.close()
    return render_template('import_url.html')


@app.route('/image/<int:image_id>/delete', methods=['POST'])
def image_delete(image_id):
    db = get_db()
    image = db.execute("SELECT filename FROM images WHERE id=?", (image_id,)).fetchone()
    if image:
        filepath = os.path.join(UPLOAD_DIR, image['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)
        db.execute("DELETE FROM image_tags WHERE image_id=?", (image_id,))
        db.execute("DELETE FROM images WHERE id=?", (image_id,))
        db.commit()
    db.close()
    return redirect(url_for('index'))


@app.route('/image/<int:image_id>/citation')
def image_citation(image_id):
    db = get_db()
    image = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
    if not image:
        return "Not found", 404
    apa = generate_apa_citation(image)
    db.close()
    return jsonify({'apa': apa})


@app.route('/themes')
def themes():
    db = get_db()
    themes = db.execute("""SELECT t.*, COUNT(i.id) as image_count
                           FROM themes t LEFT JOIN images i ON t.id = i.theme_id
                           GROUP BY t.id ORDER BY t.name""").fetchall()
    db.close()
    return render_template('themes.html', themes=themes)


@app.route('/api/image/<int:image_id>/courses', methods=['GET', 'PUT'])
def api_image_courses(image_id):
    db = get_db()
    if request.method == 'GET':
        rows = db.execute("""SELECT cr.relevance, cr.fit, c.code, c.name
                             FROM image_course_relevance cr
                             JOIN courses c ON cr.course_id = c.id
                             WHERE cr.image_id = ?""", (image_id,)).fetchall()
        db.close()
        return jsonify([dict(r) for r in rows])

    # PUT — batch set course relevance
    data = request.get_json()
    if not data or 'courses' not in data:
        return jsonify({'error': 'expected {courses: [{code, relevance, fit}]}'}), 400

    db.execute("DELETE FROM image_course_relevance WHERE image_id=?", (image_id,))
    for entry in data['courses']:
        course = db.execute("SELECT id FROM courses WHERE code=?", (entry['code'],)).fetchone()
        if course:
            db.execute("INSERT INTO image_course_relevance (image_id, course_id, relevance, fit) VALUES (?, ?, ?, ?)",
                       (image_id, course['id'], entry['relevance'], entry.get('fit', 'strong')))
    db.commit()
    db.close()
    return jsonify({'ok': True, 'image_id': image_id})


@app.route('/api/unprocessed')
def api_unprocessed():
    """Return images that need processing (no description)."""
    db = get_db()
    images = db.execute("""SELECT id, filename, title FROM images
                           WHERE (description IS NULL OR description = '')
                           ORDER BY id DESC""").fetchall()
    db.close()
    return jsonify([{'id': r['id'], 'filename': r['filename'], 'title': r['title']} for r in images])


@app.route('/api/image/<int:image_id>', methods=['GET', 'PUT'])
def api_image(image_id):
    db = get_db()
    if request.method == 'GET':
        image = db.execute("SELECT * FROM images WHERE id=?", (image_id,)).fetchone()
        if not image:
            return jsonify({'error': 'not found'}), 404
        db.close()
        return jsonify(dict(image))

    # PUT — update metadata (used by Claude Code after scanning)
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    from process_image import update_image_metadata, get_theme_id_by_name

    theme_id = None
    if 'theme' in data:
        theme_id = get_theme_id_by_name(data['theme'])

    update_image_metadata(
        image_id,
        title=data.get('title'),
        creator=data.get('creator'),
        date=data.get('date'),
        description=data.get('description'),
        narrative=data.get('narrative'),
        source_url=data.get('source_url'),
        rights=data.get('rights'),
        medium=data.get('medium'),
        theme_id=theme_id,
        tags=data.get('tags')
    )
    db.close()
    return jsonify({'ok': True, 'image_id': image_id})


@app.route('/api/stats')
def api_stats():
    db = get_db()
    stats = {
        'images': db.execute("SELECT COUNT(*) FROM images").fetchone()[0],
        'themes': db.execute("SELECT COUNT(*) FROM themes").fetchone()[0],
        'tags': db.execute("SELECT COUNT(DISTINCT tag_id) FROM image_tags").fetchone()[0],
    }
    theme_counts = db.execute("""SELECT t.name, COUNT(i.id) as count
                                 FROM themes t LEFT JOIN images i ON t.id = i.theme_id
                                 GROUP BY t.id ORDER BY count DESC""").fetchall()
    stats['by_theme'] = [{'name': r['name'], 'count': r['count']} for r in theme_counts]
    db.close()
    return jsonify(stats)


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5050, debug=True)
