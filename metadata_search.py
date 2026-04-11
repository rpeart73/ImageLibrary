"""
Iris — Automatic Metadata Search

Searches Wikipedia for image metadata based on filename.
Classifies theme, suggests tags, and determines course relevance.
Called automatically on upload and via the auto-process API.
"""
import re
import requests

# Map theme names (must match database exactly) to search keywords
THEME_KEYWORDS = {
    'Black Archives and Collections': [
        'archive', 'collection', 'library', 'manuscript', 'preservation', 'digital collection',
    ],
    'Black Art and Museums': [
        'art', 'painting', 'sculpture', 'museum', 'gallery', 'installation', 'exhibition',
        'mural', 'visual art', 'woodcut', 'lithograph',
    ],
    'Black Feminism and Intersectionality': [
        'feminism', 'feminist', 'womanist', 'intersectionality', 'misogynoir',
        'bell hooks', 'angela davis', 'audre lorde', 'patricia hill collins',
    ],
    'Black Film': [
        'film', 'movie', 'cinema', 'director', 'actor', 'actress', 'blaxploitation',
        'hollywood', 'screenwriter', 'oscar', 'sundance',
    ],
    'Black Music': [
        'music', 'jazz', 'blues', 'gospel', 'hip-hop', 'rap', 'soul', 'funk', 'r&b',
        'singer', 'musician', 'album', 'record', 'motown', 'reggae', 'calypso',
    ],
    'Black Representation and Stereotypes': [
        'representation', 'stereotype', 'media image', 'controlling image',
        'portrayal', 'advertising', 'caricature', 'minstrel', 'blackface',
    ],
    'Civil Rights and Activism': [
        'civil rights', 'protest', 'activism', 'activist', 'march', 'boycott',
        'segregation', 'naacp', 'black panther', 'black lives matter', 'kneel',
        'anthem', 'racial justice', 'police brutality', 'movement', 'apartheid',
        'liberation', 'social justice', 'demonstrations',
    ],
    'Negro Leagues': [
        'negro league', 'baseball', 'satchel paige', 'josh gibson', 'barnstorming',
        'negro national league', 'negro american league',
    ],
    'Portraits and People': [
        'portrait', 'leader', 'pioneer', 'biographical',
    ],
}

# Course relevance keywords (must match course codes in DB)
COURSE_KEYWORDS = {
    'BFS142': [
        'sport', 'athlete', 'baseball', 'negro league', 'boxing', 'basketball',
        'football', 'olympic', 'race', 'resistance', 'athletic', 'player',
        'quarterback', 'track', 'tennis', 'soccer', 'cricket',
    ],
    'BFS211': [
        'music', 'jazz', 'blues', 'gospel', 'hip-hop', 'rap', 'soul', 'funk',
        'protest music', 'liberation', 'spiritual', 'album', 'concert', 'reggae',
    ],
    'BFS218': [
        'technology', 'algorithm', 'surveillance', 'digital', 'artificial intelligence',
        'facial recognition', 'bias', 'internet', 'social media', 'data', 'techno-racism',
    ],
    'BFS220': [
        'popular culture', 'media', 'film', 'television', 'stereotype', 'representation',
        'blaxploitation', 'misogynoir', 'image', 'celebrity', 'icon', 'cultural',
    ],
    'SOC122': [
        'sociology', 'social', 'indigenous', 'community', 'structure', 'inequality',
        'education', 'identity', 'society', 'sociological',
    ],
}

# Tag patterns: tag name -> keywords that trigger it
TAG_PATTERNS = {
    'activism': ['activist', 'activism', 'protest', 'civil rights', 'social justice', 'demonstration'],
    'athlete': ['athlete', 'player', 'sports', 'football', 'basketball', 'baseball', 'olympic', 'quarterback'],
    'musician': ['musician', 'singer', 'rapper', 'composer', 'jazz', 'blues', 'pianist', 'guitarist'],
    'artist': ['artist', 'painter', 'sculptor', 'printmaker', 'visual art'],
    'filmmaker': ['director', 'filmmaker', 'actor', 'actress', 'producer'],
    'writer': ['author', 'writer', 'poet', 'novelist', 'journalist', 'playwright'],
    'politician': ['politician', 'senator', 'president', 'congress', 'mayor', 'governor', 'representative'],
    'educator': ['professor', 'teacher', 'educator', 'scholar', 'academic'],
    'resistance': ['resistance', 'protest', 'boycott', 'kneel', 'defiance', 'uprising'],
    'Black history': ['african american', 'african-american', 'black history'],
    'photography': ['photograph', 'photographer', 'photo', 'daguerreotype'],
    'civil rights movement': ['civil rights movement', 'freedom ride', 'sit-in', 'march on washington'],
}


def clean_filename_to_query(filename):
    """Convert a filename into a usable search query.

    Strips extensions, counters, separators, and common noise words.
    """
    # Remove extension
    name = re.sub(r'\.[^.]+$', '', filename)
    # Remove trailing counters (e.g., _1, _2)
    name = re.sub(r'_\d+$', '', name)
    # Remove thumb/poster/still suffixes
    name = re.sub(r'_(thumb|poster|still|cover|promo|banner).*$', '', name, flags=re.IGNORECASE)
    # Replace underscores and hyphens with spaces
    name = re.sub(r'[_\-]', ' ', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


WIKI_HEADERS = {
    'User-Agent': 'IrisImageLibrary/1.0 (localhost; educational image catalog)',
}


def search_wikipedia(query):
    """Search Wikipedia for a subject and return structured data.

    Returns dict with title, description, extract, source_url or None.
    """
    try:
        # Search for the best matching article
        resp = requests.get(
            'https://en.wikipedia.org/w/api.php',
            params={
                'action': 'query',
                'list': 'search',
                'srsearch': query,
                'format': 'json',
                'srlimit': 1,
            },
            headers=WIKI_HEADERS,
            timeout=5,
        )
        if resp.status_code != 200:
            return None

        data = resp.json()

        results = data.get('query', {}).get('search', [])
        if not results:
            return None

        article_title = results[0]['title']

        # Fetch the article summary
        summary_resp = requests.get(
            f'https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(article_title)}',
            headers=WIKI_HEADERS,
            timeout=5,
        )
        if summary_resp.status_code != 200:
            return None

        summary = summary_resp.json()

        return {
            'title': summary.get('title', ''),
            'description': summary.get('description', ''),
            'extract': summary.get('extract', ''),
            'source_url': summary.get('content_urls', {}).get('desktop', {}).get('page', ''),
        }
    except Exception:
        return None


def classify_theme(text):
    """Classify text into one of Iris's 9 themes based on keyword frequency."""
    text_lower = text.lower()
    scores = {}
    for theme, keywords in THEME_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[theme] = score

    if scores:
        return max(scores, key=scores.get)
    return 'Portraits and People'


def suggest_tags(wiki_data, query):
    """Generate tag suggestions from Wikipedia data and the search query."""
    tags = set()
    text = f"{wiki_data.get('extract', '')} {wiki_data.get('description', '')}".lower()

    # The subject name itself is always a tag
    subject = query.strip().title()
    if len(subject) > 2:
        tags.add(subject)

    for tag, keywords in TAG_PATTERNS.items():
        if any(kw in text for kw in keywords):
            tags.add(tag)

    return list(tags)[:10]


def determine_course_relevance(text, tags):
    """Determine which courses this image is relevant to, with fit level."""
    combined = f"{text} {' '.join(tags)}".lower()
    relevance = []

    for code, keywords in COURSE_KEYWORDS.items():
        matches = [kw for kw in keywords if kw in combined]
        if matches:
            if len(matches) >= 3:
                fit = 'strong'
            elif len(matches) >= 2:
                fit = 'moderate'
            else:
                fit = 'supplementary'
            relevance.append({
                'code': code,
                'relevance': f"Related to {code} themes: {', '.join(matches[:4])}",
                'fit': fit,
            })

    return relevance


def is_relevant_match(query, wiki_title):
    """Check whether the Wikipedia result actually matches the search query.

    Prevents false positives like "Legends of Soul Music" matching "John Legend".
    """
    stop_words = {
        'the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and',
        'or', 'is', 'was', 'with', 'by', 'from', 'its', 'their',
    }
    query_words = [w for w in query.lower().split() if w not in stop_words and len(w) > 2]
    title_lower = wiki_title.lower()

    if not query_words:
        return False

    matches = sum(1 for w in query_words if w in title_lower)

    if len(query_words) <= 2:
        return matches >= 1
    return matches >= 2


def search_metadata(filename):
    """Search for metadata based on an image filename.

    Returns a dict with title, description, narrative, source_url, theme,
    tags, courses, medium — or None if nothing was found.
    """
    query = clean_filename_to_query(filename)
    if not query or len(query) < 3:
        return None

    wiki_data = search_wikipedia(query)
    if not wiki_data:
        return None

    # Reject results that don't actually match the query
    if not is_relevant_match(query, wiki_data['title']):
        return None

    extract = wiki_data.get('extract', '')
    short_desc = wiki_data.get('description', '')
    full_text = f"{extract} {short_desc}"

    theme = classify_theme(full_text)
    tags = suggest_tags(wiki_data, wiki_data['title'])
    courses = determine_course_relevance(full_text, tags)

    return {
        'title': wiki_data['title'],
        'description': short_desc or extract[:300].rsplit('.', 1)[0] + '.' if extract else short_desc,
        'narrative': extract or None,
        'source_url': wiki_data.get('source_url', ''),
        'theme': theme,
        'tags': tags,
        'courses': courses,
        'medium': 'Photograph',
    }


def classify_from_page(title, description=''):
    """Classify metadata from page context (og:title, og:description).

    Used when importing from article URLs where the page itself provides
    better context than the image filename.
    """
    text = f"{title} {description}"

    theme = classify_theme(text)
    tags = suggest_tags({'extract': description, 'description': title}, title)
    courses = determine_course_relevance(text, tags)

    return {
        'title': title,
        'description': description or None,
        'theme': theme,
        'tags': tags,
        'courses': courses,
    }
