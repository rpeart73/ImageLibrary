"""Loom Research — Flask application."""
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

from database import get_db, init_db, generate_apa_citation, compute_file_hash, DB_PATH
from metadata_search import search_metadata, classify_from_page
from query_parser import parse_query, augment_two_eyed_seeing
from research_search import search_all, SOURCE_NAMES
from citation_export import export_apa_list, export_ris, export_bibtex, format_apa

app = Flask(__name__)
app.secret_key = os.urandom(24)


@app.teardown_appcontext
def close_db(exception):
    """Ensure database connections are always closed after each request."""
    db = getattr(app, '_database', None)
    if db is not None:
        db.close()

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'tiff', 'bmp'}
PROTON_IMG_BASE = '/mnt/c/Users/rpeart/Proton Drive/raymondpeart/My files/EXT/Image_Library'
TRESORIT_IMG_BASE = '/mnt/c/Users/rpeart/Tresorit/Tresorit Ecosystem/Back_up_Filing Cabinet/EXT/Image_Library'

os.makedirs(UPLOAD_DIR, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


@app.route('/api/autocomplete')
def autocomplete():
    """Return search suggestions as JSON for autocomplete."""
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    db = get_db()
    pattern = f'%{q}%'
    suggestions = []
    # Titles
    for row in db.execute("SELECT title FROM images WHERE title LIKE ? LIMIT 5", (pattern,)).fetchall():
        suggestions.append({'text': row['title'], 'type': 'title'})
    # Creators
    for row in db.execute("SELECT DISTINCT creator FROM images WHERE creator LIKE ? AND creator IS NOT NULL LIMIT 3", (pattern,)).fetchall():
        suggestions.append({'text': row['creator'], 'type': 'creator'})
    # Media titles
    for row in db.execute("SELECT title FROM media WHERE title LIKE ? OR creator LIKE ? LIMIT 3", (pattern, pattern)).fetchall():
        suggestions.append({'text': row['title'], 'type': 'media'})
    # Tags
    for row in db.execute("SELECT name FROM tags WHERE name LIKE ? LIMIT 5", (pattern,)).fetchall():
        suggestions.append({'text': row['name'], 'type': 'tag'})
    # Themes
    for row in db.execute("SELECT name FROM themes WHERE name LIKE ? LIMIT 3", (pattern,)).fetchall():
        suggestions.append({'text': row['name'], 'type': 'theme'})
    db.close()
    # Deduplicate by text
    seen = set()
    unique = []
    for s in suggestions:
        if s['text'] not in seen:
            seen.add(s['text'])
            unique.append(s)
    return jsonify(unique[:10])


@app.route('/web-search')
def web_search_page():
    """Dedicated web search results page. Opens in a new window."""
    q = request.args.get('q', '').strip()
    course = request.args.get('course', '').strip()
    if not q:
        return redirect(url_for('library'))
    return render_template('web_search.html', query=q, course=course)


BRAIN_IDS = {
    'seneca': 'df72757b-0d10-469e-a671-713bfbe6b860',
    'york': '4b5d4af0-7a24-425d-829d-587d1951469c',
    'loom': '3a368b36-89ea-41f2-97de-4dcd67503fb1',
}

@app.route('/api/brain-assess', methods=['POST'])
def brain_assess():
    """Ask the Seneca Brain to assess course relevance of web search results.
    Accepts: {query, course, results: [{title, description, source}]}
    Returns: {assessments: [{title, relevance, fit, reasoning}]}
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    query = data.get('query', '')
    course = data.get('course', '')
    results_to_assess = data.get('results', [])[:8]

    if not results_to_assess:
        return jsonify({'assessments': []})

    # Build a prompt for the brain
    items_text = '\n'.join([
        f"- {r.get('title', 'Untitled')}: {r.get('description', '')[:100]}" for r in results_to_assess
    ])

    brain_prompt = (
        f"I found these images/media while searching for '{query}'"
        f"{' for course ' + course if course else ''}. "
        f"For each item: (1) Rate relevance as strong, moderate, or weak. "
        f"(2) Explain in 2 to 3 sentences WHY it is relevant or not, connecting it to specific "
        f"course themes, weekly topics, or learning outcomes. "
        f"(3) Suggest which weekly module or assignment it could support. "
        f"(4) Note any pedagogical value: does it illustrate a concept, provoke discussion, "
        f"or serve as a primary source?\n\n{items_text}"
    )

    try:
        import subprocess, json as _json
        # Sanitize prompt: remove newlines, limit length
        clean_prompt = brain_prompt.replace('\n', ' ').replace('"', "'")[:1500]

        result = subprocess.run(
            [os.path.expanduser('~/.local/bin/nlm'), 'notebook', 'query',
             '--json', BRAIN_IDS['seneca'], clean_prompt],
            capture_output=True, text=True, timeout=45
        )
        if result.returncode == 0:
            try:
                brain_data = _json.loads(result.stdout)
                answer = brain_data.get('value', {}).get('answer', result.stdout.strip())
            except (_json.JSONDecodeError, KeyError):
                answer = result.stdout.strip()
            return jsonify({'assessment': answer, 'query': query, 'course': course})
        else:
            return jsonify({'assessment': 'Brain query failed. Results shown without relevance scoring.',
                            'error': result.stderr[:300]})
    except subprocess.TimeoutExpired:
        return jsonify({'assessment': 'Brain query timed out (45s). The Seneca Brain may be processing. Try again.'})
    except Exception as e:
        return jsonify({'assessment': f'Brain unavailable: {str(e)[:200]}. Results shown without relevance scoring.'})


_web_search_last = {}

@app.route('/api/web-search')
def web_search():
    """Search Wikimedia Commons and Wikipedia for images related to a query.
    Rate limited: 1 request per 3 seconds per client IP.
    """
    client_ip = request.remote_addr or 'unknown'
    now_ts = datetime.now().timestamp()
    if client_ip in _web_search_last and now_ts - _web_search_last[client_ip] < 3:
        return jsonify({'results': [], 'error': 'Rate limited. Wait 3 seconds.', 'sources': []}), 429
    _web_search_last[client_ip] = now_ts

    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': [], 'source': 'none'})

    results = []

    # 1. Wikimedia Commons image search
    try:
        resp = requests.get('https://commons.wikimedia.org/w/api.php', params={
            'action': 'query',
            'generator': 'search',
            'gsrsearch': q,
            'gsrnamespace': 6,  # File namespace
            'gsrlimit': 12,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata|size|mime',
            'iiurlwidth': 300,
            'format': 'json',
        }, headers={'User-Agent': 'LoomResearch/2.0 (educational; Black Studies image catalog)'},
        timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            pages = data.get('query', {}).get('pages', {})
            for page_id, page in pages.items():
                info = page.get('imageinfo', [{}])[0]
                meta = info.get('extmetadata', {})
                title = page.get('title', '').replace('File:', '')
                desc = meta.get('ImageDescription', {}).get('value', '')
                # Strip HTML from description
                desc = re.sub(r'<[^>]+>', '', desc)[:300]
                artist = meta.get('Artist', {}).get('value', '')
                artist = re.sub(r'<[^>]+>', '', artist)
                license_name = meta.get('LicenseShortName', {}).get('value', 'Unknown')
                thumb_url = info.get('thumburl', '')
                full_url = info.get('url', '')
                width = info.get('width', 0)
                height = info.get('height', 0)

                if width >= 200 and height >= 200 and info.get('mime', '').startswith('image/'):
                    results.append({
                        'title': title,
                        'description': desc,
                        'creator': artist,
                        'license': license_name,
                        'thumb_url': thumb_url,
                        'full_url': full_url,
                        'source': 'Wikimedia Commons',
                        'source_page': f"https://commons.wikimedia.org/wiki/File:{requests.utils.quote(page.get('title', '').replace('File:', ''))}",
                        'width': width,
                        'height': height,
                    })
    except Exception:
        pass

    # 2. Wikipedia article images
    try:
        resp = requests.get('https://en.wikipedia.org/w/api.php', params={
            'action': 'query',
            'list': 'search',
            'srsearch': q,
            'format': 'json',
            'srlimit': 3,
        }, headers={'User-Agent': 'LoomResearch/2.0 (educational)'},
        timeout=5)

        if resp.status_code == 200:
            articles = resp.json().get('query', {}).get('search', [])
            for article in articles:
                article_title = article['title']
                # Get the main image for this article
                img_resp = requests.get('https://en.wikipedia.org/w/api.php', params={
                    'action': 'query',
                    'titles': article_title,
                    'prop': 'pageimages|extracts',
                    'piprop': 'original|thumbnail',
                    'pithumbsize': 300,
                    'exintro': True,
                    'explaintext': True,
                    'exsentences': 2,
                    'format': 'json',
                }, headers={'User-Agent': 'LoomResearch/2.0 (educational)'},
                timeout=5)

                if img_resp.status_code == 200:
                    pages = img_resp.json().get('query', {}).get('pages', {})
                    for pid, pg in pages.items():
                        original = pg.get('original', {})
                        thumb = pg.get('thumbnail', {})
                        if original.get('source'):
                            results.append({
                                'title': article_title,
                                'description': pg.get('extract', '')[:300],
                                'creator': 'Wikipedia',
                                'license': 'Varies',
                                'thumb_url': thumb.get('source', original['source']),
                                'full_url': original['source'],
                                'source': 'Wikipedia',
                                'source_page': f"https://en.wikipedia.org/wiki/{requests.utils.quote(article_title)}",
                                'width': original.get('width', 0),
                                'height': original.get('height', 0),
                            })
    except Exception:
        pass

    # 3. Smithsonian National Museum of African American History and Culture
    try:
        resp = requests.get('https://api.si.edu/openaccess/api/v1.0/search', params={
            'q': q,
            'rows': 8,
            'online_media_type': 'Images',
        }, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            rows = data.get('response', {}).get('rows', [])
            for row in rows:
                content = row.get('content', {})
                desc_data = content.get('descriptiveNonRepeating', {})
                freetext = content.get('freetext', {})

                title = desc_data.get('title', {}).get('content', '')
                online_media = desc_data.get('online_media', {}).get('media', [])
                thumb = ''
                full = ''
                for m in online_media:
                    if m.get('type', '').startswith('Images'):
                        thumb = m.get('thumbnail', '')
                        full = m.get('content', thumb)
                        break

                notes = freetext.get('notes', [])
                desc = ''
                for n in notes:
                    if isinstance(n, dict):
                        desc = n.get('content', '')[:300]
                        break

                if title and (thumb or full):
                    results.append({
                        'title': title,
                        'description': desc,
                        'creator': 'Smithsonian NMAAHC',
                        'license': 'Open Access',
                        'thumb_url': thumb or full,
                        'full_url': full or thumb,
                        'source': 'Smithsonian NMAAHC',
                        'source_page': desc_data.get('record_link', ''),
                        'width': 0,
                        'height': 0,
                    })
    except Exception:
        pass

    # 4. Library of Congress (Black history, civil rights photography)
    try:
        resp = requests.get('https://www.loc.gov/search/', params={
            'q': q,
            'fa': '',
            'fo': 'json',
            'c': 8,
            'at': 'results',
        }, headers={'User-Agent': 'LoomResearch/2.0 (educational)'},
        timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('results', []):
                thumb = ''
                if item.get('image_url'):
                    thumbs = item['image_url']
                    thumb = thumbs[0] if isinstance(thumbs, list) else thumbs
                title = item.get('title', '')
                desc = item.get('description', [''])[0] if isinstance(item.get('description'), list) else item.get('description', '')

                if title and thumb:
                    results.append({
                        'title': title,
                        'description': (desc or '')[:300],
                        'creator': 'Library of Congress',
                        'license': 'Public Domain',
                        'thumb_url': thumb,
                        'full_url': thumb.replace('/thumb/', '/full/') if '/thumb/' in thumb else thumb,
                        'source': 'Library of Congress',
                        'source_page': item.get('url', ''),
                        'width': 0,
                        'height': 0,
                    })
    except Exception:
        pass

    # 5. Semantic Scholar (academic articles)
    articles = []
    try:
        resp = requests.get('https://api.semanticscholar.org/graph/v1/paper/search', params={
            'query': q,
            'limit': 8,
            'fields': 'title,authors,year,abstract,url,externalIds,citationCount,journal',
        }, headers={'User-Agent': 'LoomResearch/2.0 (educational)'},
        timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            for paper in data.get('data', []):
                authors = ', '.join([a.get('name', '') for a in paper.get('authors', [])[:3]])
                if len(paper.get('authors', [])) > 3:
                    authors += ' et al.'
                doi = paper.get('externalIds', {}).get('DOI', '')
                year = paper.get('year', 'n.d.')
                title = paper.get('title', '')
                journal_info = paper.get('journal', {})
                journal_name = journal_info.get('name', '') if journal_info else ''

                apa = f"{authors}. ({year}). {title}."
                if journal_name:
                    apa += f" *{journal_name}*."
                if doi:
                    apa += f" https://doi.org/{doi}"

                articles.append({
                    'title': title,
                    'authors': authors,
                    'year': year,
                    'abstract': (paper.get('abstract') or '')[:300],
                    'url': paper.get('url', ''),
                    'doi': doi,
                    'journal': journal_name,
                    'citations': paper.get('citationCount', 0),
                    'apa_citation': apa,
                    'source': 'Semantic Scholar',
                })
    except Exception:
        pass

    # 6. ERIC (education research)
    try:
        resp = requests.get('https://api.ies.ed.gov/eric/', params={
            'search': q,
            'rows': 6,
            'format': 'json',
        }, timeout=10)

        if resp.status_code == 200:
            data = resp.json()
            for doc in data.get('response', {}).get('docs', []):
                authors = doc.get('author', ['Unknown'])
                if isinstance(authors, list):
                    authors = ', '.join(authors[:3])
                year = doc.get('publicationdateyear', 'n.d.')
                title = doc.get('title', '')
                desc = doc.get('description', '')
                eric_id = doc.get('id', '')
                journal_name = doc.get('source', '')

                apa = f"{authors}. ({year}). {title}."
                if journal_name:
                    apa += f" *{journal_name}*."
                if eric_id:
                    apa += f" https://eric.ed.gov/?id={eric_id}"

                articles.append({
                    'title': title,
                    'authors': authors,
                    'year': str(year),
                    'abstract': (desc or '')[:300],
                    'url': f"https://eric.ed.gov/?id={eric_id}" if eric_id else '',
                    'doi': '',
                    'journal': journal_name,
                    'citations': 0,
                    'apa_citation': apa,
                    'source': 'ERIC',
                })
    except Exception:
        pass

    return jsonify({
        'results': results[:20],
        'articles': articles[:12],
        'query': q,
        'sources': ['Wikimedia Commons', 'Wikipedia', 'Smithsonian NMAAHC', 'Library of Congress', 'Semantic Scholar', 'ERIC'],
    })


@app.route('/api/export-citations', methods=['POST'])
def export_selected_citations():
    """Export APA 7th citations for selected items as formatted text."""
    data = request.get_json()
    if not data or 'items' not in data:
        return jsonify({'error': 'No items provided'}), 400

    db = get_db()
    citations = []

    for item in data['items']:
        item_id = item.get('id')
        item_type = item.get('type', 'image')

        if item_type == 'image':
            row = db.execute("SELECT title, creator, date, apa_citation, source_url FROM images WHERE id = ?", (item_id,)).fetchone()
        else:
            row = db.execute("SELECT title, creator, date, apa_citation, url as source_url FROM media WHERE id = ?", (item_id,)).fetchone()

        if row and row['apa_citation']:
            citations.append(row['apa_citation'])
        elif row:
            # Generate citation on the fly if missing
            creator = row['creator'] or 'Unknown'
            date = row['date'] or 'n.d.'
            title = row['title'] or 'Untitled'
            url = row['source_url'] or ''
            citation = f"{creator}. ({date}). {title}"
            if item_type == 'media':
                citation += " [Video]"
            else:
                citation += " [Image]"
            if url:
                citation += f". {url}"
            citations.append(citation)

    db.close()

    # Sort alphabetically by author (APA 7th standard)
    citations.sort(key=lambda c: c.lower())

    # Format as proper APA reference list
    output_lines = ["References", ""]
    for c in citations:
        output_lines.append(c)
        output_lines.append("")

    return jsonify({
        'citations': citations,
        'formatted': '\n'.join(output_lines),
        'count': len(citations),
    })


def get_suggestions(db, q):
    """Return 'did you mean' suggestions for zero-result searches."""
    suggestions = []
    # Find closest tags
    for row in db.execute("SELECT name FROM tags ORDER BY name").fetchall():
        if q.lower() in row['name'].lower() or row['name'].lower() in q.lower():
            suggestions.append({'text': row['name'], 'type': 'tag'})
    # Find closest themes
    for row in db.execute("SELECT name FROM themes ORDER BY name").fetchall():
        if q.lower() in row['name'].lower():
            suggestions.append({'text': row['name'], 'type': 'theme'})
    # Find closest creators
    for row in db.execute("SELECT DISTINCT creator FROM images WHERE creator IS NOT NULL UNION SELECT DISTINCT creator FROM media WHERE creator IS NOT NULL").fetchall():
        if row['creator'] and q.lower() in row['creator'].lower():
            suggestions.append({'text': row['creator'], 'type': 'creator'})
    return suggestions[:5]


def get_facet_counts(db, where_clause='', params=None):
    """Return theme, course, medium, and tag counts for faceted search."""
    if params is None:
        params = []
    base = f"FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE 1=1 {where_clause}"

    theme_counts = db.execute(
        f"SELECT t.name, COUNT(*) as cnt {base} AND t.name IS NOT NULL GROUP BY t.name ORDER BY cnt DESC",
        params).fetchall()

    course_counts = db.execute(
        f"SELECT c.code, c.name, COUNT(*) as cnt FROM image_course_relevance icr "
        f"JOIN courses c ON icr.course_id = c.id "
        f"WHERE icr.image_id IN (SELECT i.id {base}) GROUP BY c.code ORDER BY cnt DESC",
        params).fetchall()

    medium_counts = db.execute(
        f"SELECT i.medium, COUNT(*) as cnt {base} AND i.medium IS NOT NULL GROUP BY i.medium ORDER BY cnt DESC",
        params).fetchall()

    tag_counts = db.execute(
        f"SELECT tg.name, COUNT(*) as cnt FROM image_tags it "
        f"JOIN tags tg ON it.tag_id = tg.id "
        f"WHERE it.image_id IN (SELECT i.id {base}) GROUP BY tg.name ORDER BY cnt DESC LIMIT 30",
        params).fetchall()

    return {
        'themes': theme_counts,
        'courses': course_counts,
        'mediums': medium_counts,
        'tags': tag_counts,
    }


@app.route('/library')
def library():
    db = get_db()
    q = request.args.get('q', '').strip()
    field = request.args.get('field', 'all')
    theme_filter = request.args.get('theme', '')
    course_filter = request.args.get('course', '')
    tag_filter = request.args.get('tag', '')
    sort = request.args.get('sort', 'newest')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    has_search = bool(q or theme_filter or course_filter or tag_filter)

    query = "SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE 1=1"
    where_extra = ""
    params = []

    if theme_filter:
        where_extra += " AND t.name = ?"
        params.append(theme_filter)
    if tag_filter:
        where_extra += " AND i.id IN (SELECT image_id FROM image_tags it JOIN tags tg ON it.tag_id = tg.id WHERE tg.name = ?)"
        params.append(tag_filter)
    if course_filter:
        where_extra += " AND i.id IN (SELECT image_id FROM image_course_relevance icr JOIN courses c ON icr.course_id = c.id WHERE c.code = ?)"
        params.append(course_filter)
    if q:
        field_map = {
            'title': ['i.title'],
            'creator': ['i.creator'],
            'narrative': ['i.narrative'],
            'tags': [],
        }
        if field == 'tags':
            where_extra += " AND i.id IN (SELECT image_id FROM image_tags it JOIN tags tg ON it.tag_id = tg.id WHERE tg.name LIKE ?)"
            params.append(f'%{q}%')
        elif field in field_map and field_map[field]:
            cols = field_map[field]
            clauses = [f"{col} LIKE ?" for col in cols]
            where_extra += f" AND ({' OR '.join(clauses)})"
            params.extend([f'%{q}%'] * len(cols))
        else:
            where_extra += " AND (i.title LIKE ? OR i.description LIKE ? OR i.narrative LIKE ? OR i.creator LIKE ?)"
            params.extend([f'%{q}%'] * 4)

    query += where_extra

    # Count
    count_query = query.replace("SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id",
                                "SELECT COUNT(*) FROM images i LEFT JOIN themes t ON i.theme_id = t.id")
    total_count = db.execute(count_query, params).fetchone()[0]

    # Sort
    sort_map = {
        'newest': 'i.modified DESC',
        'title': 'i.title ASC',
        'creator': 'i.creator ASC',
        'relevance': 'i.modified DESC',
    }
    order = sort_map.get(sort, 'i.modified DESC')
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    query += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([per_page, offset])
    images = db.execute(query, params).fetchall()

    # Get tags and course relevance for each image result
    results = []
    for img in images:
        img_tags = db.execute(
            "SELECT tg.name FROM tags tg JOIN image_tags it ON tg.id = it.tag_id WHERE it.image_id = ? ORDER BY tg.name",
            (img['id'],)).fetchall()
        img_courses = db.execute(
            "SELECT c.code, icr.fit FROM image_course_relevance icr JOIN courses c ON icr.course_id = c.id WHERE icr.image_id = ?",
            (img['id'],)).fetchall()
        results.append({**dict(img), 'tags': [t['name'] for t in img_tags], 'courses': img_courses, 'result_type': 'image'})

    # Also search media table
    if q:
        media_where = "WHERE (m.title LIKE ? OR m.description LIKE ? OR m.creator LIKE ?)"
        media_params = [f'%{q}%'] * 3
        if theme_filter:
            media_where += " AND t.name = ?"
            media_params.append(theme_filter)
        media_results = db.execute(
            f"SELECT m.*, t.name as theme_name FROM media m LEFT JOIN themes t ON m.theme_id = t.id {media_where} ORDER BY m.modified DESC",
            media_params).fetchall()
        for m in media_results:
            m_tags = db.execute(
                "SELECT tg.name FROM tags tg JOIN media_tags mt ON tg.id = mt.tag_id WHERE mt.media_id = ? ORDER BY tg.name",
                (m['id'],)).fetchall()
            m_courses = db.execute(
                "SELECT c.code, mcr.fit FROM media_course_relevance mcr JOIN courses c ON mcr.course_id = c.id WHERE mcr.media_id = ?",
                (m['id'],)).fetchall()
            media_dict = {**dict(m), 'tags': [t['name'] for t in m_tags], 'courses': m_courses, 'result_type': 'media'}
            media_dict['thumbnail'] = m['thumbnail'] or None
            results.append(media_dict)
        total_count += len(media_results)

    # Facets
    facets = get_facet_counts(db, where_extra, params[:-2]) if has_search else get_facet_counts(db)

    # Collection data (themes with counts and representative thumbnails)
    collections = db.execute(
        "SELECT t.name, t.description, COUNT(i.id) as count, "
        "(SELECT i2.filename FROM images i2 WHERE i2.theme_id = t.id ORDER BY i2.modified DESC LIMIT 1) as thumb "
        "FROM themes t LEFT JOIN images i ON i.theme_id = t.id GROUP BY t.id HAVING count > 0 ORDER BY count DESC"
    ).fetchall()

    total_images = db.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    total_media = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    stats = {
        'total_images': total_images,
        'total_media': total_media,
        'total_items': total_images + total_media,
        'total_themes': db.execute("SELECT COUNT(*) FROM themes WHERE id IN (SELECT DISTINCT theme_id FROM images WHERE theme_id IS NOT NULL)").fetchone()[0],
        'total_courses': db.execute("SELECT COUNT(*) FROM courses").fetchone()[0],
    }

    # Recent additions (last 5 images + media combined, sorted by date)
    recent_images = db.execute(
        "SELECT i.id, i.title, i.creator, i.filename, i.modified, t.name as theme_name, 'image' as result_type "
        "FROM images i LEFT JOIN themes t ON i.theme_id = t.id ORDER BY i.modified DESC LIMIT 5").fetchall()
    recent_media = db.execute(
        "SELECT m.id, m.title, m.creator, NULL as filename, m.modified, t.name as theme_name, 'media' as result_type "
        "FROM media m LEFT JOIN themes t ON m.theme_id = t.id ORDER BY m.modified DESC LIMIT 5").fetchall()
    recent = sorted([dict(r) for r in recent_images] + [dict(r) for r in recent_media],
                    key=lambda x: x.get('modified', ''), reverse=True)[:5]

    # Suggestions for zero results
    suggestions = get_suggestions(db, q) if has_search and total_count == 0 and q else []

    db.close()
    return render_template('library.html', results=results, collections=collections, facets=facets,
                           stats=stats, has_search=has_search, search=q, field=field,
                           current_theme=theme_filter, current_course=course_filter, current_tag=tag_filter,
                           sort=sort, page=page, total_pages=total_pages, total_count=total_count,
                           recent=recent, suggestions=suggestions)


@app.route('/browse')
def browse():
    db = get_db()
    theme_filter = request.args.get('theme')
    tag_filter = request.args.get('tag')
    course_filter = request.args.get('course')
    search = request.args.get('q')

    query = "SELECT i.*, t.name as theme_name FROM images i LEFT JOIN themes t ON i.theme_id = t.id WHERE 1=1"
    params = []

    if theme_filter:
        query += " AND t.name = ?"
        params.append(theme_filter)
    if tag_filter:
        query += " AND i.id IN (SELECT image_id FROM image_tags it JOIN tags tg ON it.tag_id = tg.id WHERE tg.name = ?)"
        params.append(tag_filter)
    if course_filter:
        query += " AND i.id IN (SELECT image_id FROM image_course_relevance icr JOIN courses c ON icr.course_id = c.id WHERE c.code = ?)"
        params.append(course_filter)
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
        'total_media': db.execute("SELECT COUNT(*) FROM media").fetchone()[0],
        'total_themes': db.execute("SELECT COUNT(*) FROM themes").fetchone()[0],
        'total_tags': db.execute("SELECT COUNT(DISTINCT tag_id) FROM image_tags").fetchone()[0],
    }

    courses = db.execute("SELECT code, name FROM courses ORDER BY code").fetchall()

    db.close()
    return render_template('index.html', images=images, themes=themes, tags=tags, stats=stats,
                           courses=courses, current_theme=theme_filter, current_tag=tag_filter,
                           current_course=course_filter, search=search,
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

    # Related images (same theme or shared tags, exclude self)
    tag_names = [t['name'] for t in tags]
    related = []
    if tag_names:
        placeholders = ','.join('?' * len(tag_names))
        related = db.execute(f"""SELECT DISTINCT i.id, i.filename, i.title, i.creator
                                 FROM images i
                                 JOIN image_tags it ON i.id = it.image_id
                                 JOIN tags tg ON it.tag_id = tg.id
                                 WHERE tg.name IN ({placeholders}) AND i.id != ?
                                 LIMIT 6""", tag_names + [image_id]).fetchall()

    db.close()
    return render_template('detail.html', image=image, tags=tags, apa=apa,
                           course_relevance=course_relevance, related=related)


@app.route('/export/citations')
def export_citations():
    """Export APA citations filtered by course or theme."""
    db = get_db()
    course = request.args.get('course')
    theme = request.args.get('theme')

    citations = []

    # Image citations
    if course:
        images = db.execute("""SELECT i.* FROM images i
                               JOIN image_course_relevance icr ON i.id = icr.image_id
                               JOIN courses c ON icr.course_id = c.id
                               WHERE c.code = ? ORDER BY i.creator""", (course,)).fetchall()
    elif theme:
        images = db.execute("""SELECT i.* FROM images i
                               JOIN themes t ON i.theme_id = t.id
                               WHERE t.name = ? ORDER BY i.creator""", (theme,)).fetchall()
    else:
        images = db.execute("SELECT * FROM images ORDER BY creator").fetchall()

    for img in images:
        citations.append(generate_apa_citation(img))

    # Media citations
    if course:
        media = db.execute("""SELECT m.apa_citation FROM media m
                              JOIN media_course_relevance mcr ON m.id = mcr.media_id
                              JOIN courses c ON mcr.course_id = c.id
                              WHERE c.code = ? AND m.apa_citation IS NOT NULL ORDER BY m.creator""", (course,)).fetchall()
    elif theme:
        media = db.execute("""SELECT m.apa_citation FROM media m
                              JOIN themes t ON m.theme_id = t.id
                              WHERE t.name = ? AND m.apa_citation IS NOT NULL ORDER BY m.creator""", (theme,)).fetchall()
    else:
        media = db.execute("SELECT apa_citation FROM media WHERE apa_citation IS NOT NULL ORDER BY creator").fetchall()

    for m in media:
        citations.append(m['apa_citation'])

    db.close()

    label = course or theme or 'All'
    text = f"References ({label})\n\n" + "\n\n".join(citations)
    return text, 200, {'Content-Type': 'text/plain; charset=utf-8'}


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


def auto_process_image(image_id, filename=None, page_meta=None):
    """Auto-process an image: populate metadata from Wikipedia or page context.

    Args:
        image_id: Database ID of the image
        filename: Image filename (for Wikipedia search)
        page_meta: Optional dict with 'title' and 'description' from source page.
                   When provided, uses page context instead of Wikipedia.

    Returns the metadata dict if found, None otherwise.
    """
    from process_image import update_image_metadata, get_theme_id_by_name

    # If we have page metadata (from article URL import), use that directly
    if page_meta and page_meta.get('title'):
        result = classify_from_page(page_meta['title'], page_meta.get('description', ''))
        result['narrative'] = page_meta.get('description')
        result['medium'] = 'Digital image'
    else:
        # Fall back to Wikipedia search based on filename
        if filename is None:
            db = get_db()
            image = db.execute("SELECT filename FROM images WHERE id=?", (image_id,)).fetchone()
            db.close()
            if not image:
                return None
            filename = image['filename']

        result = search_metadata(filename)

    if not result:
        return None

    theme_id = get_theme_id_by_name(result['theme']) if result.get('theme') else None

    update_image_metadata(
        image_id,
        title=result.get('title'),
        description=result.get('description'),
        narrative=result.get('narrative'),
        medium=result.get('medium'),
        theme_id=theme_id,
        tags=result.get('tags'),
    )

    # Set course relevance
    if result.get('courses'):
        db = get_db()
        db.execute("DELETE FROM image_course_relevance WHERE image_id=?", (image_id,))
        for entry in result['courses']:
            course = db.execute("SELECT id FROM courses WHERE code=?", (entry['code'],)).fetchone()
            if course:
                db.execute(
                    "INSERT INTO image_course_relevance (image_id, course_id, relevance, fit) VALUES (?, ?, ?, ?)",
                    (image_id, course['id'], entry['relevance'], entry.get('fit', 'moderate')),
                )
        db.commit()
        db.close()

    app.logger.info("Auto-processed image %d: %s", image_id, result.get('title', ''))
    return result


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

                # Duplicate detection by content hash
                content_hash = compute_file_hash(filepath)
                existing = db.execute("SELECT id, filename FROM images WHERE content_hash=?", (content_hash,)).fetchone()
                if existing:
                    os.remove(filepath)
                    flash(f'Duplicate: this image already exists as "{existing["filename"]}"', 'info')
                    if len(files) == 1:
                        db.close()
                        return redirect(url_for('image_detail', image_id=existing['id']))
                    continue

                try:
                    with PILImage.open(filepath) as img:
                        w, h = img.size
                except:
                    w, h = 0, 0

                fsize = os.path.getsize(filepath)
                mimetype = file.content_type

                db.execute("""INSERT INTO images (filename, original_filename, title, file_size, width, height, mimetype, content_hash)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                           (filename, file.filename, base, fsize, w, h, mimetype, content_hash))

                uploaded.append({'filename': filename, 'file_size': fsize, 'width': w, 'height': h})

        db.commit()

        # Collect image IDs and close DB before auto-processing
        for img_info in uploaded:
            row = db.execute("SELECT id FROM images WHERE filename=?", (img_info['filename'],)).fetchone()
            if row:
                img_info['id'] = row['id']
        db.close()

        # Auto-process: search metadata for each uploaded image
        for img_info in uploaded:
            if 'id' in img_info:
                result = auto_process_image(img_info['id'], img_info['filename'])
                img_info['processed'] = result is not None
                if result:
                    img_info['found_title'] = result.get('title', '')

        if len(uploaded) == 1 and 'id' in uploaded[0]:
            return redirect(url_for('image_detail', image_id=uploaded[0]['id']))

        return render_template('upload.html', uploaded=uploaded)

    db.close()
    return render_template('upload.html', uploaded=None)


@app.route('/import-url', methods=['GET', 'POST'])
def import_url():
    db = get_db()
    if request.method == 'POST':
        url = request.form.get('url', '').strip()
        title = ''
        theme_id = None
        tag_str = ''

        if url:
            # Detect YouTube URLs
            is_youtube = 'youtube.com/watch' in url or 'youtu.be/' in url
            if is_youtube:
                try:
                    oembed = requests.get(f'https://www.youtube.com/oembed?url={url}&format=json', timeout=10).json()
                    vid_title = oembed.get('title', 'Untitled Video')
                    vid_author = oembed.get('author_name', 'Unknown')
                    thumb_url = oembed.get('thumbnail_url', '')

                    # Download thumbnail
                    thumb_filename = None
                    if thumb_url:
                        safe = re.sub(r'[^\w\s\-]', '', vid_title).replace(' ', '_')[:60]
                        thumb_filename = f"{safe}_thumb.jpg"
                        thumb_path = os.path.join(UPLOAD_DIR, thumb_filename)
                        counter = 1
                        while os.path.exists(thumb_path):
                            thumb_filename = f"{safe}_thumb_{counter}.jpg"
                            thumb_path = os.path.join(UPLOAD_DIR, thumb_filename)
                            counter += 1
                        tresp = requests.get(thumb_url, timeout=10)
                        with open(thumb_path, 'wb') as f:
                            f.write(tresp.content)

                    db.execute("""INSERT INTO media (title, creator, url, media_type, source)
                                  VALUES (?, ?, ?, 'video', 'YouTube')""",
                               (vid_title, vid_author, url))
                    media_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

                    db.commit()
                    db.close()
                    return redirect(url_for('media_detail', media_id=media_id))
                except Exception as e:
                    flash(f'YouTube import failed: {str(e)}', 'error')
                    db.close()
                    return render_template('import_url.html')

            try:
                from bs4 import BeautifulSoup

                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
                    'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8'
                }
                page_meta = None  # Metadata extracted from article pages
                source_url = url  # Preserved for attribution

                resp = requests.get(url, headers=headers, timeout=15, stream=True)
                resp.raise_for_status()

                content_type = resp.headers.get('content-type', '')
                if 'image' not in content_type:
                    # Not a direct image — scrape the page for its main image + metadata
                    page_resp = requests.get(url, headers={
                        'User-Agent': headers['User-Agent'],
                        'Accept': 'text/html,*/*',
                    }, timeout=15)
                    soup = BeautifulSoup(page_resp.text, 'html.parser')

                    # Extract page metadata for auto-processing
                    og_title = soup.find('meta', property='og:title')
                    og_desc = soup.find('meta', property='og:description')
                    page_meta = {
                        'title': og_title['content'].strip() if og_title and og_title.get('content') else None,
                        'description': og_desc['content'].strip() if og_desc and og_desc.get('content') else None,
                    }

                    # Find the main image: og:image first, then content images
                    img_url = None
                    og_img = soup.find('meta', property='og:image')
                    if og_img and og_img.get('content'):
                        img_url = og_img['content']
                    else:
                        for img in soup.find_all('img', src=True):
                            src = img['src']
                            if any(x in src.lower() for x in ('.jpg', '.jpeg', '.png', '.webp', '.gif')):
                                if 'logo' not in src.lower() and 'icon' not in src.lower():
                                    img_url = src
                                    break

                    if not img_url:
                        flash('No images found on this page.', 'error')
                        db.close()
                        return render_template('import_url.html')

                    # Make absolute if relative
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        parsed_base = urlparse(url)
                        img_url = f"{parsed_base.scheme}://{parsed_base.netloc}{img_url}"

                    # Re-fetch the actual image
                    url = img_url
                    resp = requests.get(url, headers=headers, timeout=15, stream=True)
                    resp.raise_for_status()
                    content_type = resp.headers.get('content-type', '')

                    if 'image' not in content_type:
                        flash('Could not retrieve an image from this page.', 'error')
                        db.close()
                        return render_template('import_url.html')

                # Determine filename and extension
                ext = 'jpg'
                if 'png' in content_type:
                    ext = 'png'
                elif 'webp' in content_type:
                    ext = 'webp'
                elif 'gif' in content_type:
                    ext = 'gif'

                if title:
                    safe_title = re.sub(r'[^\w\s\-]', '', title).replace(' ', '_')
                elif page_meta and page_meta.get('title'):
                    safe_title = re.sub(r'[^\w\s\-]', '', page_meta['title']).replace(' ', '_')[:80]
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

                # Duplicate detection by content hash
                content_hash = compute_file_hash(filepath)
                existing = db.execute("SELECT id, filename FROM images WHERE content_hash=?", (content_hash,)).fetchone()
                if existing:
                    os.remove(filepath)
                    flash(f'Duplicate: this image already exists as "{existing["filename"]}"', 'info')
                    db.close()
                    return redirect(url_for('image_detail', image_id=existing['id']))

                # Get dimensions
                try:
                    with PILImage.open(filepath) as img:
                        w, h = img.size
                except:
                    w, h = 0, 0

                fsize = os.path.getsize(filepath)
                display_title = title or (page_meta['title'] if page_meta and page_meta.get('title') else safe_title)

                db.execute("""INSERT INTO images (filename, original_filename, title, source_url, theme_id,
                              file_size, width, height, mimetype, content_hash)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (filename, os.path.basename(url), display_title, source_url, theme_id,
                            fsize, w, h, content_type, content_hash))
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

                # Auto-process: use page metadata if scraped from article, else filename
                auto_process_image(image_id, filename, page_meta=page_meta)

                return redirect(url_for('image_detail', image_id=image_id))

            except Exception as e:
                flash(f'Import failed: {str(e)}', 'error')

    db.close()
    return render_template('import_url.html')


@app.route('/media/<int:media_id>')
def media_detail(media_id):
    db = get_db()
    media = db.execute("SELECT m.*, t.name as theme_name FROM media m LEFT JOIN themes t ON m.theme_id = t.id WHERE m.id = ?", (media_id,)).fetchone()
    if not media:
        return "Not found", 404
    tags = db.execute("SELECT tg.name FROM tags tg JOIN media_tags mt ON tg.id = mt.tag_id WHERE mt.media_id = ?", (media_id,)).fetchall()
    course_relevance = db.execute("""SELECT mcr.relevance, mcr.fit, c.code, c.name
                                     FROM media_course_relevance mcr
                                     JOIN courses c ON mcr.course_id = c.id
                                     WHERE mcr.media_id = ?
                                     ORDER BY CASE mcr.fit WHEN 'strong' THEN 1 WHEN 'moderate' THEN 2 ELSE 3 END""",
                                  (media_id,)).fetchall()
    db.close()
    return render_template('media_detail.html', media=media, tags=tags, course_relevance=course_relevance)


@app.route('/media')
def media_list():
    db = get_db()
    media = db.execute("SELECT m.*, t.name as theme_name FROM media m LEFT JOIN themes t ON m.theme_id = t.id ORDER BY m.created DESC").fetchall()
    db.close()
    return render_template('media_list.html', media=media)


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


@app.route('/api/image/<int:image_id>/auto-process', methods=['POST'])
def api_auto_process(image_id):
    """Trigger automatic metadata search for an image."""
    result = auto_process_image(image_id)
    if result:
        return jsonify({'ok': True, 'image_id': image_id, 'title': result.get('title', '')})
    return jsonify({'ok': False, 'message': 'No metadata found for this filename'}), 404


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


# ════════════════════════��══════════════════════════════
# Research Portal Routes
# ═══════════════════════════════════════════════════════


@app.route('/api/preview')
def api_preview():
    """Proxy-fetch a URL and return readable text for the document viewer.
    Anti-hallucination: returns ONLY content found at the URL. Never generates."""
    url = request.args.get('url', '').strip()
    if not url or not url.startswith('http'):
        return jsonify({'error': 'Invalid URL'}), 400

    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0',
            'Accept': 'text/html,*/*',
        }, timeout=15)

        content_type = resp.headers.get('content-type', '')

        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            return jsonify({'type': 'pdf', 'url': url})

        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        title = ''
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        elif soup.title:
            title = soup.title.string or ''

        article = soup.find('article') or soup.find('main') or soup.find(class_=re.compile(r'article|content|body|entry'))
        if article:
            paragraphs = [p.get_text(strip=True) for p in article.find_all('p') if len(p.get_text(strip=True)) > 30]
        else:
            paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True)) > 30]

        text = '\n\n'.join(paragraphs[:50])
        if len(text) < 100:
            return jsonify({'type': 'minimal', 'title': title,
                            'text': 'Preview unavailable. Open the full text link to read this source.', 'url': url})

        return jsonify({'type': 'article', 'title': title, 'text': text,
                        'paragraphs': len(paragraphs), 'url': url, 'source_verified': True})
    except Exception as e:
        return jsonify({'type': 'error', 'error': str(e)[:200], 'url': url})

@app.route('/')
@app.route('/research')
def research():
    """Research portal page. Replaces the basic web search."""
    q = request.args.get('q', '').strip()
    course = request.args.get('course', '').strip()
    return render_template('research.html', query=q, course=course)


@app.route('/api/research-search')
def api_research_search():
    """Search 14 databases concurrently. Returns deduplicated, quality-scored results.

    Query params:
        q:          Search query (supports Boolean, phrases, fields)
        course:     Optional course filter
        sources:    Comma-separated source keys (default: all)
        two_eyed:   '1' to enable Two-Eyed Seeing augmentation
        limit:      Results per source (default 8, max 20)
    """
    q = request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': [], 'articles': [], 'images': [],
                        'source_counts': {}, 'total': 0})

    course = request.args.get('course', '').strip()
    sources_param = request.args.get('sources', '').strip()
    two_eyed = request.args.get('two_eyed', '0') == '1'
    limit = min(int(request.args.get('limit', 8)), 20)

    # Parse the query
    parsed = parse_query(q)

    # Select sources
    sources = sources_param.split(',') if sources_param else None

    # Main search
    data = search_all(parsed, sources=sources, limit_per_source=limit)

    # Generate APA citations for results that don't have them
    for r in data['results']:
        if not r.get('apa_citation'):
            r['apa_citation'] = format_apa(r)

    # Two-Eyed Seeing augmentation (additional Indigenous-perspective results)
    two_eyed_results = []
    if two_eyed:
        augmented_terms = augment_two_eyed_seeing(parsed)
        if augmented_terms:
            # Search a subset of academic sources for Indigenous perspectives
            indigenous_sources = ['openalex', 'core', 'eric', 'wikimedia', 'loc']
            for term in augmented_terms[:4]:  # limit to avoid too many extra queries
                aug_parsed = parse_query(term)
                aug_data = search_all(aug_parsed, sources=indigenous_sources, limit_per_source=3)
                for r in aug_data['results']:
                    r['two_eyed_seeing'] = True
                    r['indigenous_term'] = term
                    if not r.get('apa_citation'):
                        r['apa_citation'] = format_apa(r)
                    two_eyed_results.append(r)

    # Log search to history
    try:
        db = get_db()
        db.execute("INSERT INTO search_history (query, filters, result_count, course) VALUES (?, ?, ?, ?)",
                   (q, sources_param or 'all', data['total'], course or None))
        db.commit()
        db.close()
    except Exception:
        pass

    return jsonify({
        'results': data['results'],
        'articles': data['articles'],
        'images': data['images'],
        'two_eyed_results': two_eyed_results,
        'source_counts': data['source_counts'],
        'sources_searched': [SOURCE_NAMES.get(s, s) for s in data['sources_searched']],
        'total': data['total'],
        'query': q,
        'parsed': {
            'terms': parsed.terms,
            'phrases': parsed.phrases,
            'excluded': parsed.excluded,
            'fields': parsed.fields,
            'operator': parsed.operator,
        },
    })


@app.route('/api/research-export', methods=['POST'])
def api_research_export():
    """Export selected results in APA 7th, RIS, or BibTeX format.

    Body: {format: 'apa'|'ris'|'bibtex', results: [...]}
    """
    data = request.get_json()
    if not data or 'results' not in data:
        return jsonify({'error': 'No results provided'}), 400

    results = data['results']
    fmt = data.get('format', 'apa')

    if fmt == 'ris':
        output = export_ris(results)
        return output, 200, {
            'Content-Type': 'application/x-research-info-systems',
            'Content-Disposition': 'attachment; filename=iris_research.ris',
        }
    elif fmt == 'bibtex':
        output = export_bibtex(results)
        return output, 200, {
            'Content-Type': 'application/x-bibtex',
            'Content-Disposition': 'attachment; filename=iris_research.bib',
        }
    else:
        output = export_apa_list(results)
        return output, 200, {
            'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': 'attachment; filename=iris_references_apa7.txt',
        }


@app.route('/api/reading-list', methods=['GET', 'POST', 'DELETE'])
def api_reading_list():
    """Reading list CRUD.

    GET:    Return all reading list items, optionally filtered by course.
    POST:   Add an item to the reading list.
    DELETE: Remove an item by id.
    """
    db = get_db()

    if request.method == 'GET':
        course = request.args.get('course', '').strip()
        collection = request.args.get('collection', '').strip()

        query = "SELECT * FROM reading_list WHERE 1=1"
        params = []
        if course:
            query += " AND course = ?"
            params.append(course)
        if collection:
            query += " AND collection = ?"
            params.append(collection)
        query += " ORDER BY created DESC"

        items = db.execute(query, params).fetchall()
        db.close()
        return jsonify([dict(r) for r in items])

    if request.method == 'POST':
        data = request.get_json()
        if not data:
            db.close()
            return jsonify({'error': 'No data'}), 400

        # Check for duplicate by DOI or URL
        doi = data.get('doi', '')
        url = data.get('url', '')
        if doi:
            existing = db.execute("SELECT id FROM reading_list WHERE doi = ?", (doi,)).fetchone()
            if existing:
                db.close()
                return jsonify({'error': 'Already in reading list', 'id': existing['id']}), 409
        elif url:
            existing = db.execute("SELECT id FROM reading_list WHERE url = ?", (url,)).fetchone()
            if existing:
                db.close()
                return jsonify({'error': 'Already in reading list', 'id': existing['id']}), 409

        db.execute("""INSERT INTO reading_list
                      (title, authors, year, abstract, url, doi, source, content_type,
                       citation_apa, citation_ris, is_open_access, pdf_url,
                       citation_count, quality_score, thumb_url, notes, course, collection)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (data.get('title', 'Untitled'), data.get('authors', ''),
                    data.get('year', ''), data.get('abstract', ''),
                    url, doi,
                    data.get('source', ''), data.get('content_type', ''),
                    data.get('citation_apa', ''), data.get('citation_ris', ''),
                    1 if data.get('is_open_access') else 0,
                    data.get('pdf_url', ''),
                    data.get('citation_count', 0), data.get('quality_score', 0),
                    data.get('thumb_url', ''), data.get('notes', ''),
                    data.get('course', ''), data.get('collection', 'Unsorted')))
        db.commit()
        item_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        return jsonify({'ok': True, 'id': item_id}), 201

    if request.method == 'DELETE':
        item_id = request.args.get('id', type=int)
        if not item_id:
            db.close()
            return jsonify({'error': 'No id'}), 400
        db.execute("DELETE FROM reading_list WHERE id = ?", (item_id,))
        db.commit()
        db.close()
        return jsonify({'ok': True})


@app.route('/api/saved-searches', methods=['GET', 'POST', 'DELETE'])
def api_saved_searches():
    """Saved searches CRUD."""
    db = get_db()

    if request.method == 'GET':
        items = db.execute("SELECT * FROM saved_searches ORDER BY last_run DESC, created DESC").fetchall()
        db.close()
        return jsonify([dict(r) for r in items])

    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('query'):
            db.close()
            return jsonify({'error': 'No query'}), 400
        db.execute("INSERT INTO saved_searches (name, query, filters, course, notes) VALUES (?, ?, ?, ?, ?)",
                   (data.get('name', data['query'][:50]),
                    data['query'], data.get('filters', ''),
                    data.get('course', ''), data.get('notes', '')))
        db.commit()
        item_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.close()
        return jsonify({'ok': True, 'id': item_id}), 201

    if request.method == 'DELETE':
        item_id = request.args.get('id', type=int)
        if not item_id:
            db.close()
            return jsonify({'error': 'No id'}), 400
        db.execute("DELETE FROM saved_searches WHERE id = ?", (item_id,))
        db.commit()
        db.close()
        return jsonify({'ok': True})


@app.route('/api/search-history')
def api_search_history():
    """Return the last 30 searches."""
    db = get_db()
    items = db.execute("SELECT * FROM search_history ORDER BY created DESC LIMIT 30").fetchall()
    db.close()
    return jsonify([dict(r) for r in items])


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5050, debug=True)
