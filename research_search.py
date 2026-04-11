"""
Loom Research — Research Search Engine

14 API adapters searched concurrently via ThreadPoolExecutor.
Deduplication by DOI and URL. Quality scoring 0-100.

Academic:   OpenAlex, CORE, CrossRef, Semantic Scholar, ERIC, DOAJ
Images:     Wikimedia Commons, Wikipedia, Smithsonian NMAAHC, Library of Congress,
            DPLA, Internet Archive, Europeana
Canadian:   Library and Archives Canada
"""
import re
import hashlib
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from urllib.parse import quote as urlquote

from query_parser import ParsedQuery
from bs4 import BeautifulSoup

# Shared session for connection pooling
_session = requests.Session()
_session.headers.update({
    'User-Agent': 'LoomResearch/2.0 (educational; Black Studies curriculum research)'
})

TIMEOUT = 20  # per-source timeout in seconds
MAX_WORKERS = 6


# ───────────────────────────────────────────────────────
# Quality Scoring
# ───────────────────────────────────────────────────────

def compute_quality_score(result: dict) -> int:
    """Score a result 0-100 based on credibility signals."""
    score = 0
    ct = result.get('content_type', result.get('_type', ''))

    # Peer review / source credibility
    if result.get('is_peer_reviewed'):
        score += 20
    if result.get('doi'):
        score += 5
    if result.get('is_open_access'):
        score += 5
    if result.get('abstract'):
        score += 5

    # Citation impact
    citations = result.get('citation_count', 0) or 0
    if citations > 100:
        score += 15
    elif citations > 20:
        score += 10
    elif citations > 5:
        score += 5

    # Institutional sources
    institutional = {'Smithsonian NMAAHC', 'Library of Congress', 'DPLA',
                     'Library and Archives Canada', 'Europeana', 'Internet Archive'}
    if result.get('source') in institutional:
        score += 15
    elif result.get('source') == 'Wikimedia Commons':
        score += 10

    # Known creator (not generic)
    creator = result.get('creator') or result.get('authors') or ''
    if creator and creator.lower() not in ('unknown', 'unknown creator', ''):
        score += 5

    # High resolution for images
    width = result.get('width', 0) or 0
    if width >= 800 and ct == 'image':
        score += 5

    # Academic source bonus
    academic_sources = {'OpenAlex', 'CORE', 'CrossRef', 'Semantic Scholar', 'ERIC', 'DOAJ'}
    if result.get('source') in academic_sources:
        score += 10

    return min(100, score)


# ───────────────────────────────────────────────────────
# Deduplication
# ───────────────────────────────────────────────────────

def _dedup_key(result: dict) -> str:
    """Generate a deduplication key. Uses DOI for articles, URL for images."""
    doi = result.get('doi', '')
    if doi:
        return f"doi:{doi.lower().strip()}"
    url = result.get('url') or result.get('source_page') or result.get('full_url', '')
    if url:
        return f"url:{url.lower().strip()}"
    # Fallback: title hash
    title = (result.get('title') or '').lower().strip()
    return f"title:{hashlib.md5(title.encode()).hexdigest()}"


def deduplicate(results: List[dict]) -> List[dict]:
    """Remove duplicates, preferring higher-quality versions."""
    seen = {}
    for r in results:
        key = _dedup_key(r)
        if key in seen:
            # Keep the one with the higher quality score
            if r.get('quality_score', 0) > seen[key].get('quality_score', 0):
                seen[key] = r
        else:
            seen[key] = r
    return list(seen.values())


# ───────────────────────────────────────────────────────
# API Adapters
# ───────────────────────────────────────────────────────

def _search_openalex(query: ParsedQuery, limit: int = 10) -> List[dict]:
    """OpenAlex: 250M+ works with concepts, open access status, citation counts."""
    results = []
    try:
        search_str = query.to_boolean_query() if (query.phrases or query.excluded) else query.to_simple_query()
        params = {
            'search': search_str,
            'per_page': limit,
            'select': 'id,title,authorships,publication_year,doi,primary_location,'
                      'cited_by_count,open_access,type,abstract_inverted_index,concepts',
        }
        # Field-specific queries
        field_params = query.to_field_query({
            'author': 'filter',
            'title': 'search',
        })
        if 'filter' in field_params and query.fields.get('author'):
            params['filter'] = f"authorships.author.display_name.search:{query.fields['author']}"

        resp = _session.get('https://api.openalex.org/works', params=params, timeout=TIMEOUT)
        if resp.status_code != 200:
            return results

        data = resp.json()
        for work in data.get('results', []):
            # Reconstruct abstract from inverted index
            abstract = ''
            inv_idx = work.get('abstract_inverted_index')
            if inv_idx:
                word_positions = []
                for word, positions in inv_idx.items():
                    for pos in positions:
                        word_positions.append((pos, word))
                word_positions.sort()
                abstract = ' '.join(w for _, w in word_positions)[:500]

            # Authors
            authorships = work.get('authorships', [])
            author_names = [a.get('author', {}).get('display_name', '') for a in authorships[:4]]
            if len(authorships) > 4:
                author_names.append('et al.')
            authors = ', '.join(filter(None, author_names))

            # Journal / location
            loc = work.get('primary_location', {}) or {}
            source_info = loc.get('source', {}) or {}
            journal = source_info.get('display_name', '')

            # DOI
            doi_raw = work.get('doi', '') or ''
            doi = doi_raw.replace('https://doi.org/', '') if doi_raw else ''

            # Open access
            oa = work.get('open_access', {}) or {}

            # Concepts (for tags)
            concepts = work.get('concepts', []) or []
            tags = [c.get('display_name', '') for c in concepts[:5] if c.get('level', 99) <= 2]

            results.append({
                'title': work.get('title', 'Untitled') or 'Untitled',
                'authors': authors,
                'year': str(work.get('publication_year', 'n.d.') or 'n.d.'),
                'abstract': abstract,
                'url': doi_raw or (loc.get('landing_page_url', '') or ''),
                'doi': doi,
                'journal': journal,
                'volume': '',
                'issue': '',
                'pages': '',
                'citation_count': work.get('cited_by_count', 0),
                'is_open_access': oa.get('is_oa', False),
                'is_peer_reviewed': work.get('type', '') == 'journal-article',
                'content_type': work.get('type', 'article'),
                'source': 'OpenAlex',
                'tags': tags,
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_core(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """CORE: 200M+ open access full-text articles."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.core.ac.uk/v3/search/works', params={
            'q': search_str,
            'limit': limit,
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('results', []):
            authors_list = item.get('authors', [])
            if isinstance(authors_list, list):
                author_names = []
                for a in authors_list[:4]:
                    if isinstance(a, dict):
                        author_names.append(a.get('name', ''))
                    elif isinstance(a, str):
                        author_names.append(a)
                if len(authors_list) > 4:
                    author_names.append('et al.')
                authors = ', '.join(filter(None, author_names))
            else:
                authors = str(authors_list)

            doi = ''
            if item.get('doi'):
                doi = item['doi'].replace('https://doi.org/', '')

            results.append({
                'title': item.get('title', 'Untitled') or 'Untitled',
                'authors': authors,
                'year': str(item.get('yearPublished', 'n.d.') or 'n.d.'),
                'abstract': (item.get('abstract') or '')[:500],
                'url': item.get('downloadUrl') or item.get('sourceFulltextUrls', [''])[0] if item.get('sourceFulltextUrls') else '',
                'doi': doi,
                'journal': item.get('publisher', '') or '',
                'volume': '',
                'issue': '',
                'pages': '',
                'citation_count': item.get('citationCount', 0) or 0,
                'is_open_access': True,
                'is_peer_reviewed': False,
                'content_type': 'article',
                'source': 'CORE',
                'tags': [],
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_crossref(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """CrossRef: DOI metadata and citation data."""
    results = []
    try:
        search_str = query.to_simple_query()
        params = {
            'query': search_str,
            'rows': limit,
            'select': 'DOI,title,author,published-print,published-online,'
                      'container-title,volume,issue,page,is-referenced-by-count,'
                      'type,abstract,URL',
        }
        if query.fields.get('author'):
            params['query.author'] = query.fields['author']
        if query.fields.get('title'):
            params['query.title'] = query.fields['title']

        resp = _session.get('https://api.crossref.org/works', params=params, timeout=TIMEOUT)
        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('message', {}).get('items', []):
            # Authors
            author_list = item.get('author', [])
            author_names = []
            for a in author_list[:4]:
                given = a.get('given', '')
                family = a.get('family', '')
                if family:
                    author_names.append(f"{family}, {given[0]}." if given else family)
            if len(author_list) > 4:
                author_names.append('et al.')
            authors = ', '.join(author_names)

            # Year
            date_parts = (item.get('published-print', {}) or item.get('published-online', {}) or {}).get('date-parts', [[]])
            year = str(date_parts[0][0]) if date_parts and date_parts[0] else 'n.d.'

            # Title
            titles = item.get('title', [])
            title = titles[0] if titles else 'Untitled'

            # Journal
            containers = item.get('container-title', [])
            journal = containers[0] if containers else ''

            # Abstract (may contain HTML)
            abstract = re.sub(r'<[^>]+>', '', item.get('abstract', '') or '')[:500]

            doi = item.get('DOI', '')

            results.append({
                'title': title,
                'authors': authors,
                'year': year,
                'abstract': abstract,
                'url': f"https://doi.org/{doi}" if doi else item.get('URL', ''),
                'doi': doi,
                'journal': journal,
                'volume': item.get('volume', '') or '',
                'issue': item.get('issue', '') or '',
                'pages': item.get('page', '') or '',
                'citation_count': item.get('is-referenced-by-count', 0) or 0,
                'is_open_access': False,
                'is_peer_reviewed': item.get('type', '') in ('journal-article', 'book-chapter'),
                'content_type': item.get('type', 'article'),
                'source': 'CrossRef',
                'tags': [],
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_semantic_scholar(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """Semantic Scholar: AI-powered academic search."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.semanticscholar.org/graph/v1/paper/search', params={
            'query': search_str,
            'limit': limit,
            'fields': 'title,authors,year,abstract,url,externalIds,citationCount,journal,openAccessPdf',
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for paper in data.get('data', []):
            author_names = [a.get('name', '') for a in paper.get('authors', [])[:4]]
            if len(paper.get('authors', [])) > 4:
                author_names.append('et al.')
            authors = ', '.join(filter(None, author_names))

            doi = paper.get('externalIds', {}).get('DOI', '') or ''
            journal_info = paper.get('journal', {}) or {}
            journal = journal_info.get('name', '') if isinstance(journal_info, dict) else ''

            oa_pdf = paper.get('openAccessPdf', {}) or {}

            results.append({
                'title': paper.get('title', 'Untitled') or 'Untitled',
                'authors': authors,
                'year': str(paper.get('year', 'n.d.') or 'n.d.'),
                'abstract': (paper.get('abstract') or '')[:500],
                'url': paper.get('url', ''),
                'doi': doi,
                'journal': journal,
                'volume': journal_info.get('volume', '') or '' if isinstance(journal_info, dict) else '',
                'issue': '',
                'pages': journal_info.get('pages', '') or '' if isinstance(journal_info, dict) else '',
                'citation_count': paper.get('citationCount', 0) or 0,
                'is_open_access': bool(oa_pdf.get('url')),
                'pdf_url': oa_pdf.get('url', ''),
                'is_peer_reviewed': False,
                'content_type': 'article',
                'source': 'Semantic Scholar',
                'tags': [],
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_eric(query: ParsedQuery, limit: int = 6) -> List[dict]:
    """ERIC: Education research database."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.ies.ed.gov/eric/', params={
            'search': search_str,
            'rows': limit,
            'format': 'json',
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for doc in data.get('response', {}).get('docs', []):
            authors_raw = doc.get('author', ['Unknown'])
            if isinstance(authors_raw, list):
                authors = ', '.join(authors_raw[:4])
                if len(authors_raw) > 4:
                    authors += ', et al.'
            else:
                authors = str(authors_raw)

            eric_id = doc.get('id', '')
            url = f"https://eric.ed.gov/?id={eric_id}" if eric_id else ''

            results.append({
                'title': doc.get('title', 'Untitled') or 'Untitled',
                'authors': authors,
                'year': str(doc.get('publicationdateyear', 'n.d.') or 'n.d.'),
                'abstract': (doc.get('description') or '')[:500],
                'url': url,
                'doi': '',
                'journal': doc.get('source', '') or '',
                'volume': '',
                'issue': '',
                'pages': '',
                'citation_count': 0,
                'is_open_access': bool(doc.get('e_fulltext')),
                'is_peer_reviewed': doc.get('peerreviewed', '') == 'T',
                'content_type': 'article',
                'source': 'ERIC',
                'tags': doc.get('subject', [])[:5] if isinstance(doc.get('subject'), list) else [],
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_doaj(query: ParsedQuery, limit: int = 6) -> List[dict]:
    """DOAJ: Directory of Open Access Journals."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://doaj.org/api/search/articles/' + urlquote(search_str), params={
            'pageSize': limit,
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('results', []):
            bibjson = item.get('bibjson', {})

            author_list = bibjson.get('author', [])
            author_names = [a.get('name', '') for a in author_list[:4]]
            if len(author_list) > 4:
                author_names.append('et al.')
            authors = ', '.join(filter(None, author_names))

            journal_info = bibjson.get('journal', {}) or {}
            identifiers = bibjson.get('identifier', [])
            doi = ''
            for ident in identifiers:
                if ident.get('type') == 'doi':
                    doi = ident.get('id', '')
                    break

            links = bibjson.get('link', [])
            url = ''
            for link in links:
                if link.get('type') == 'fulltext':
                    url = link.get('url', '')
                    break

            year = bibjson.get('year', 'n.d.') or 'n.d.'
            abstract = bibjson.get('abstract', '') or ''

            results.append({
                'title': bibjson.get('title', 'Untitled') or 'Untitled',
                'authors': authors,
                'year': str(year),
                'abstract': abstract[:500],
                'url': url or (f"https://doi.org/{doi}" if doi else ''),
                'doi': doi,
                'journal': journal_info.get('title', '') or '',
                'volume': journal_info.get('volume', '') or '',
                'issue': journal_info.get('number', '') or '',
                'pages': bibjson.get('start_page', '') or '',
                'citation_count': 0,
                'is_open_access': True,
                'is_peer_reviewed': True,
                'content_type': 'journal-article',
                'source': 'DOAJ',
                'tags': [kw.get('keyword', '') for kw in bibjson.get('keywords', [])[:5]] if isinstance(bibjson.get('keywords'), list) else [],
                '_type': 'article',
            })
    except Exception:
        pass
    return results


def _search_wikimedia(query: ParsedQuery, limit: int = 12) -> List[dict]:
    """Wikimedia Commons: free-use images and media."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://commons.wikimedia.org/w/api.php', params={
            'action': 'query',
            'generator': 'search',
            'gsrsearch': search_str,
            'gsrnamespace': 6,
            'gsrlimit': limit,
            'prop': 'imageinfo',
            'iiprop': 'url|extmetadata|size|mime',
            'iiurlwidth': 300,
            'format': 'json',
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        pages = data.get('query', {}).get('pages', {})
        for page_id, page in pages.items():
            info = page.get('imageinfo', [{}])[0]
            meta = info.get('extmetadata', {})
            title = page.get('title', '').replace('File:', '')
            desc = re.sub(r'<[^>]+>', '', meta.get('ImageDescription', {}).get('value', ''))[:300]
            artist = re.sub(r'<[^>]+>', '', meta.get('Artist', {}).get('value', ''))
            license_name = meta.get('LicenseShortName', {}).get('value', 'Unknown')
            width = info.get('width', 0)
            height = info.get('height', 0)

            if width >= 200 and height >= 200 and info.get('mime', '').startswith('image/'):
                file_title = page.get('title', '').replace('File:', '')
                results.append({
                    'title': title,
                    'description': desc,
                    'creator': artist or 'Unknown',
                    'license': license_name,
                    'thumb_url': info.get('thumburl', ''),
                    'full_url': info.get('url', ''),
                    'source': 'Wikimedia Commons',
                    'source_page': f"https://commons.wikimedia.org/wiki/File:{urlquote(file_title)}",
                    'width': width,
                    'height': height,
                    'content_type': 'image',
                    '_type': 'image',
                })
    except Exception:
        pass
    return results


def _search_wikipedia(query: ParsedQuery, limit: int = 3) -> List[dict]:
    """Wikipedia: article images with context."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://en.wikipedia.org/w/api.php', params={
            'action': 'query',
            'list': 'search',
            'srsearch': search_str,
            'format': 'json',
            'srlimit': limit,
        }, timeout=10)

        if resp.status_code != 200:
            return results

        articles = resp.json().get('query', {}).get('search', [])
        for article in articles:
            article_title = article['title']
            img_resp = _session.get('https://en.wikipedia.org/w/api.php', params={
                'action': 'query',
                'titles': article_title,
                'prop': 'pageimages|extracts',
                'piprop': 'original|thumbnail',
                'pithumbsize': 300,
                'exintro': True,
                'explaintext': True,
                'exsentences': 2,
                'format': 'json',
            }, timeout=10)

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
                            'source_page': f"https://en.wikipedia.org/wiki/{urlquote(article_title)}",
                            'width': original.get('width', 0),
                            'height': original.get('height', 0),
                            'content_type': 'image',
                            '_type': 'image',
                        })
    except Exception:
        pass
    return results


def _search_smithsonian(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """Smithsonian NMAAHC: National Museum of African American History and Culture."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.si.edu/openaccess/api/v1.0/search', params={
            'q': search_str,
            'rows': limit,
            'online_media_type': 'Images',
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for row in data.get('response', {}).get('rows', []):
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
                    'content_type': 'image',
                    '_type': 'image',
                })
    except Exception:
        pass
    return results


def _search_loc(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """Library of Congress: photographs, prints, and digital collections."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://www.loc.gov/search/', params={
            'q': search_str,
            'fo': 'json',
            'c': limit,
            'at': 'results',
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('results', []):
            thumb = ''
            if item.get('image_url'):
                thumbs = item['image_url']
                thumb = thumbs[0] if isinstance(thumbs, list) else thumbs
            title = item.get('title', '')
            desc_raw = item.get('description', '')
            desc = desc_raw[0] if isinstance(desc_raw, list) else (desc_raw or '')

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
                    'content_type': 'image',
                    '_type': 'image',
                })
    except Exception:
        pass
    return results


def _search_dpla(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """DPLA: Digital Public Library of America."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.dp.la/v2/items', params={
            'q': search_str,
            'page_size': limit,
            'api_key': '0000000000000000000000000000000000000000',  # DPLA public demo key
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for doc in data.get('docs', []):
            source_resource = doc.get('sourceResource', {})
            title_raw = source_resource.get('title', '')
            title = title_raw[0] if isinstance(title_raw, list) else (title_raw or 'Untitled')

            desc_raw = source_resource.get('description', '')
            desc = desc_raw[0] if isinstance(desc_raw, list) else (desc_raw or '')

            creator_raw = source_resource.get('creator', '')
            creator = creator_raw[0] if isinstance(creator_raw, list) else (creator_raw or 'Unknown')

            thumb = doc.get('object', '')
            link = doc.get('isShownAt', '')

            if title:
                results.append({
                    'title': title,
                    'description': desc[:300],
                    'creator': creator,
                    'license': 'Varies',
                    'thumb_url': thumb,
                    'full_url': thumb,
                    'source': 'DPLA',
                    'source_page': link,
                    'width': 0,
                    'height': 0,
                    'content_type': 'image',
                    '_type': 'image',
                })
    except Exception:
        pass
    return results


def _search_internet_archive(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """Internet Archive: images and texts from the Wayback Machine and collections."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://archive.org/advancedsearch.php', params={
            'q': search_str,
            'fl[]': 'identifier,title,creator,description,date,mediatype',
            'rows': limit,
            'output': 'json',
            'page': 1,
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for doc in data.get('response', {}).get('docs', []):
            identifier = doc.get('identifier', '')
            title = doc.get('title', 'Untitled') or 'Untitled'
            creator = doc.get('creator', '')
            if isinstance(creator, list):
                creator = creator[0] if creator else 'Unknown'
            desc = doc.get('description', '')
            if isinstance(desc, list):
                desc = desc[0] if desc else ''
            date = doc.get('date', '')

            thumb = f"https://archive.org/services/img/{identifier}" if identifier else ''
            link = f"https://archive.org/details/{identifier}" if identifier else ''

            results.append({
                'title': title,
                'description': (desc or '')[:300],
                'creator': creator or 'Internet Archive',
                'license': 'Varies',
                'thumb_url': thumb,
                'full_url': thumb,
                'source': 'Internet Archive',
                'source_page': link,
                'width': 0,
                'height': 0,
                'date': date,
                'content_type': 'image',
                '_type': 'image',
            })
    except Exception:
        pass
    return results


def _search_europeana(query: ParsedQuery, limit: int = 8) -> List[dict]:
    """Europeana: European cultural heritage collections."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://api.europeana.eu/record/v2/search.json', params={
            'query': search_str,
            'rows': limit,
            'profile': 'standard',
            'wskey': 'api2demo',  # Europeana demo key
        }, timeout=TIMEOUT)

        if resp.status_code != 200:
            return results

        data = resp.json()
        for item in data.get('items', []):
            title_raw = item.get('title', ['Untitled'])
            title = title_raw[0] if isinstance(title_raw, list) else title_raw

            creator_raw = item.get('dcCreator', ['Unknown'])
            creator = creator_raw[0] if isinstance(creator_raw, list) else creator_raw

            desc_raw = item.get('dcDescription', [''])
            desc = desc_raw[0] if isinstance(desc_raw, list) else (desc_raw or '')

            thumb_list = item.get('edmPreview', [])
            thumb = thumb_list[0] if thumb_list else ''

            link = item.get('guid', '') or item.get('edmIsShownAt', [''])[0] if item.get('edmIsShownAt') else ''

            results.append({
                'title': title,
                'description': (desc or '')[:300],
                'creator': creator or 'Europeana',
                'license': item.get('rights', ['Varies'])[0] if isinstance(item.get('rights'), list) else 'Varies',
                'thumb_url': thumb,
                'full_url': thumb,
                'source': 'Europeana',
                'source_page': link,
                'width': 0,
                'height': 0,
                'content_type': 'image',
                '_type': 'image',
            })
    except Exception:
        pass
    return results


def _search_lac(query: ParsedQuery, limit: int = 6) -> List[dict]:
    """Library and Archives Canada: Canadian heritage and government records."""
    results = []
    try:
        search_str = query.to_simple_query()
        resp = _session.get('https://www.collectionscanada.gc.ca/ourl/rest/search', params={
            'q': search_str,
            'rows': limit,
            'format': 'json',
        }, timeout=TIMEOUT)

        if resp.status_code == 200:
            data = resp.json()
            for item in data.get('results', data.get('docs', [])):
                title = item.get('title', 'Untitled') if isinstance(item, dict) else 'Untitled'
                results.append({
                    'title': title,
                    'description': (item.get('description', '') or '')[:300] if isinstance(item, dict) else '',
                    'creator': (item.get('creator', '') or 'Library and Archives Canada') if isinstance(item, dict) else 'Library and Archives Canada',
                    'license': 'Government of Canada Open Licence',
                    'thumb_url': item.get('thumbnail', '') if isinstance(item, dict) else '',
                    'full_url': item.get('url', '') if isinstance(item, dict) else '',
                    'source': 'Library and Archives Canada',
                    'source_page': item.get('url', '') if isinstance(item, dict) else '',
                    'width': 0,
                    'height': 0,
                    'content_type': 'image',
                    '_type': 'image',
                })
    except Exception:
        pass
    return results


# ───────────────────────────────────────────────────────
# Abstract Enrichment
# ───────────────────────────────────────────────────────

def _fetch_abstract(url: str) -> str:
    """Fetch an abstract from a URL by scraping meta tags and first paragraphs.
    Anti-hallucination: returns ONLY text found on the page. Never generates."""
    if not url:
        return ''
    try:
        resp = _session.get(url, timeout=8, headers={
            'Accept': 'text/html,*/*',
        })
        if resp.status_code != 200:
            return ''
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Priority 1: og:description
        og = soup.find('meta', property='og:description')
        if og and og.get('content') and len(og['content'].strip()) > 50:
            return og['content'].strip()[:500]

        # Priority 2: meta description
        meta = soup.find('meta', attrs={'name': 'description'})
        if meta and meta.get('content') and len(meta['content'].strip()) > 50:
            return meta['content'].strip()[:500]

        # Priority 3: DC.description (academic sites)
        dc = soup.find('meta', attrs={'name': 'DC.description'})
        if dc and dc.get('content') and len(dc['content'].strip()) > 50:
            return dc['content'].strip()[:500]

        # Priority 4: first substantial <p> tag
        for p in soup.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text[:500]

        return ''
    except Exception:
        return ''


def enrich_abstracts(results: list, max_fetches: int = 10) -> list:
    """Fetch abstracts for results that don't have one.
    Runs concurrently. Limited to max_fetches to keep response fast.
    Anti-hallucination: only uses text extracted from actual pages."""
    needs_abstract = [(i, r) for i, r in enumerate(results)
                      if not r.get('abstract') and (r.get('url') or r.get('source_page'))]

    if not needs_abstract:
        return results

    # Only fetch for the first N to keep things fast
    to_fetch = needs_abstract[:max_fetches]

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        for idx, r in to_fetch:
            url = r.get('url') or r.get('source_page', '')
            future = executor.submit(_fetch_abstract, url)
            futures[future] = idx

        for future in as_completed(futures):
            idx = futures[future]
            try:
                abstract = future.result()
                if abstract:
                    results[idx]['abstract'] = abstract
                    results[idx]['abstract_source'] = 'enriched'
            except Exception:
                pass

    return results


# ───────────────────────────────────────────────────────
# Anti-Hallucination Verification
# ───────────────────────────────────────────────────────

def verify_result(result: dict) -> dict:
    """Verify a result's data integrity. Tag any suspect fields.
    Anti-hallucination: flag results with missing critical data."""
    warnings = []

    # Title must exist and not be placeholder
    title = result.get('title', '')
    if not title or title in ('Untitled', 'Unknown', ''):
        warnings.append('missing_title')

    # Authors check
    authors = result.get('authors', '')
    if not authors or authors in ('Unknown', ''):
        warnings.append('missing_authors')

    # Year validation
    year = result.get('year', '')
    if year and year != 'n.d.':
        try:
            y = int(year)
            if y < 1800 or y > 2027:
                warnings.append('suspect_year')
        except (ValueError, TypeError):
            warnings.append('invalid_year')

    # URL validation (must start with http)
    url = result.get('url', '')
    if url and not url.startswith('http'):
        warnings.append('invalid_url')

    # DOI format check
    doi = result.get('doi', '')
    if doi and not re.match(r'^10\.\d{4,}/', doi):
        warnings.append('suspect_doi')

    result['_warnings'] = warnings
    result['_verified'] = len(warnings) == 0
    return result


# ───────────────────────────────────────────────────────
# Orchestrator
# ───────────────────────────────────────────────────────

# All available search adapters
ALL_SOURCES = {
    'openalex': _search_openalex,
    'core': _search_core,
    'crossref': _search_crossref,
    'semantic_scholar': _search_semantic_scholar,
    'eric': _search_eric,
    'doaj': _search_doaj,
    'wikimedia': _search_wikimedia,
    'wikipedia': _search_wikipedia,
    'smithsonian': _search_smithsonian,
    'loc': _search_loc,
    'dpla': _search_dpla,
    'internet_archive': _search_internet_archive,
    'europeana': _search_europeana,
    'lac': _search_lac,
}

# Source display names
SOURCE_NAMES = {
    'openalex': 'OpenAlex',
    'core': 'CORE',
    'crossref': 'CrossRef',
    'semantic_scholar': 'Semantic Scholar',
    'eric': 'ERIC',
    'doaj': 'DOAJ',
    'wikimedia': 'Wikimedia Commons',
    'wikipedia': 'Wikipedia',
    'smithsonian': 'Smithsonian NMAAHC',
    'loc': 'Library of Congress',
    'dpla': 'DPLA',
    'internet_archive': 'Internet Archive',
    'europeana': 'Europeana',
    'lac': 'Library and Archives Canada',
}


def search_all(query: ParsedQuery,
               sources: Optional[List[str]] = None,
               limit_per_source: int = 8) -> dict:
    """Search all (or selected) sources concurrently.

    Args:
        query: Parsed query object.
        sources: List of source keys to search. None = all sources.
        limit_per_source: Max results per source.

    Returns:
        {
            'results': [...],       # deduplicated, quality-scored, sorted
            'articles': [...],      # article-type results
            'images': [...],        # image-type results
            'source_counts': {},    # {source_name: count}
            'sources_searched': [], # names of sources that were searched
            'total': int,
            'query': str,
        }
    """
    if query.is_empty:
        return {'results': [], 'articles': [], 'images': [],
                'source_counts': {}, 'sources_searched': [], 'total': 0, 'query': ''}

    # Determine which sources to search
    if sources:
        adapters = {k: v for k, v in ALL_SOURCES.items() if k in sources}
    else:
        adapters = ALL_SOURCES

    # Concurrent search
    all_results = []
    source_counts = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for name, adapter in adapters.items():
            future = executor.submit(adapter, query, limit_per_source)
            futures[future] = name

        for future in as_completed(futures):
            source_name = futures[future]
            try:
                results = future.result()
                all_results.extend(results)
                display_name = SOURCE_NAMES.get(source_name, source_name)
                source_counts[display_name] = len(results)
            except Exception:
                source_counts[SOURCE_NAMES.get(source_name, source_name)] = 0

    # Score quality
    for r in all_results:
        r['quality_score'] = compute_quality_score(r)

    # Anti-hallucination: verify every result
    all_results = [verify_result(r) for r in all_results]

    # Deduplicate
    all_results = deduplicate(all_results)

    # Enrich abstracts for results missing them
    all_results = enrich_abstracts(all_results, max_fetches=12)

    # Sort: quality score descending, then citation count descending
    all_results.sort(key=lambda r: (r.get('quality_score', 0), r.get('citation_count', 0)), reverse=True)

    # Split by type
    articles = [r for r in all_results if r.get('_type') == 'article']
    images = [r for r in all_results if r.get('_type') == 'image']

    # Peer-reviewed count
    peer_reviewed_count = sum(1 for r in articles if r.get('is_peer_reviewed'))

    return {
        'results': all_results,
        'articles': articles,
        'images': images,
        'source_counts': source_counts,
        'sources_searched': list(adapters.keys()),
        'total': len(all_results),
        'peer_reviewed_count': peer_reviewed_count,
        'query': query.raw,
    }
