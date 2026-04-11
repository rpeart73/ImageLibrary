"""
Iris Research Portal — Citation Export

Formats search results into APA 7th, RIS (Zotero/Mendeley), and BibTeX.
Supports single and bulk export.
"""
import re
import unicodedata
from typing import List, Optional


def _clean_text(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _surname_key(apa: str) -> str:
    """Extract lowercase first author surname for sorting."""
    if not apa:
        return ''
    return apa.split(',')[0].split('.')[0].strip().lower()


# ───────────────────────────────────────────────────────
# APA 7th Edition
# ───────────────────────────────────────────────────────

def format_apa_article(result: dict) -> str:
    """Format an article/paper result as APA 7th.

    Expected keys: authors, year, title, journal, volume, issue, pages, doi, url
    """
    authors = _clean_text(result.get('authors', 'Unknown'))
    year = result.get('year') or 'n.d.'
    title = _clean_text(result.get('title', 'Untitled'))
    journal = _clean_text(result.get('journal', ''))
    volume = result.get('volume', '')
    issue = result.get('issue', '')
    pages = result.get('pages', '')
    doi = result.get('doi', '')
    url = result.get('url', '')

    # Author. (Year). Title. *Journal*, *Volume*(Issue), Pages. DOI/URL
    citation = f"{authors}. ({year}). {title}."

    if journal:
        citation += f" *{journal}*"
        if volume:
            citation += f", *{volume}*"
            if issue:
                citation += f"({issue})"
        if pages:
            citation += f", {pages}"
        citation += '.'

    if doi:
        if not doi.startswith('http'):
            citation += f" https://doi.org/{doi}"
        else:
            citation += f" {doi}"
    elif url:
        citation += f" {url}"

    return citation


def format_apa_image(result: dict) -> str:
    """Format an image/media result as APA 7th.

    Expected keys: creator, year/date, title, source, source_page/url
    """
    creator = _clean_text(result.get('creator', '')) or 'Unknown'
    year = result.get('year') or result.get('date') or 'n.d.'
    title = _clean_text(result.get('title', 'Untitled'))
    source = result.get('source', '')
    url = result.get('source_page') or result.get('url', '')
    medium = result.get('medium', 'Image')

    # Creator. (Year). *Title* [Medium]. Source. URL
    citation = f"{creator}. ({year}). *{title}* [{medium}]."
    if source:
        citation += f" {source}."
    if url:
        citation += f" {url}"

    return citation


def format_apa(result: dict) -> str:
    """Auto-detect result type and format as APA 7th."""
    if result.get('_type') == 'image' or result.get('content_type') == 'image':
        return format_apa_image(result)
    # If it has apa_citation already, use it
    if result.get('apa_citation'):
        return result['apa_citation']
    return format_apa_article(result)


def export_apa_list(results: List[dict]) -> str:
    """Export a sorted APA 7th reference list."""
    citations = [format_apa(r) for r in results]
    citations.sort(key=_surname_key)
    return "References\n\n" + "\n\n".join(citations)


# ───────────────────────────────────────────────────────
# RIS (Research Information Systems) — Zotero/Mendeley
# ───────────────────────────────────────────────────────

def _ris_type(result: dict) -> str:
    """Map content type to RIS type tag."""
    ct = result.get('content_type', result.get('_type', 'article'))
    mapping = {
        'article': 'JOUR',
        'journal-article': 'JOUR',
        'book': 'BOOK',
        'book-chapter': 'CHAP',
        'conference-paper': 'CPAPER',
        'dissertation': 'THES',
        'report': 'RPRT',
        'image': 'FIGURE',
        'video': 'VIDEO',
        'webpage': 'ELEC',
    }
    return mapping.get(ct, 'GEN')


def format_ris(result: dict) -> str:
    """Format a single result as RIS."""
    lines = []
    lines.append(f"TY  - {_ris_type(result)}")

    # Authors — split on semicolons; if none, use the whole string as one author
    authors_str = result.get('authors', '')
    if authors_str:
        clean = authors_str.replace(' et al.', '').strip()
        if ';' in clean:
            parts = [a.strip() for a in clean.split(';') if a.strip()]
        elif ', ' in clean and clean.count(',') >= 2:
            # Multiple "Last, F." entries separated by ", " — split carefully
            # Pattern: "Last, F., Last2, F." -> split on ", " that follows a period or single letter
            parts = re.split(r',\s+(?=[A-Z][a-z])', clean)
            parts = [p.strip().rstrip(',') for p in parts if p.strip()]
        else:
            parts = [clean]
        for author in parts:
            if author:
                lines.append(f"AU  - {author}")

    title = _clean_text(result.get('title', ''))
    if title:
        lines.append(f"TI  - {title}")
        lines.append(f"T1  - {title}")

    year = result.get('year', '')
    if year and year != 'n.d.':
        lines.append(f"PY  - {year}")
        lines.append(f"DA  - {year}///")

    journal = result.get('journal', '')
    if journal:
        lines.append(f"JO  - {journal}")
        lines.append(f"T2  - {journal}")

    volume = result.get('volume', '')
    if volume:
        lines.append(f"VL  - {volume}")

    issue = result.get('issue', '')
    if issue:
        lines.append(f"IS  - {issue}")

    pages = result.get('pages', '')
    if pages:
        if '-' in pages:
            sp, ep = pages.split('-', 1)
            lines.append(f"SP  - {sp.strip()}")
            lines.append(f"EP  - {ep.strip()}")
        else:
            lines.append(f"SP  - {pages}")

    doi = result.get('doi', '')
    if doi:
        lines.append(f"DO  - {doi}")

    url = result.get('url') or result.get('source_page', '')
    if url:
        lines.append(f"UR  - {url}")

    abstract = _clean_text(result.get('abstract', ''))
    if abstract:
        lines.append(f"AB  - {abstract}")

    source = result.get('source', '')
    if source:
        lines.append(f"DB  - {source}")

    # Tags/keywords
    for tag in result.get('tags', []):
        lines.append(f"KW  - {tag}")

    lines.append("ER  - ")
    return '\n'.join(lines)


def export_ris(results: List[dict]) -> str:
    """Export multiple results as a single RIS file."""
    entries = [format_ris(r) for r in results]
    return '\n\n'.join(entries)


# ───────────────────────────────────────────────────────
# BibTeX
# ───────────────────────────────────────────────────────

def _bibtex_key(result: dict) -> str:
    """Generate a BibTeX citation key: surname_year_firstword."""
    authors = result.get('authors', 'unknown')
    surname = re.split(r'[,\s]', authors)[0].lower()
    surname = re.sub(r'[^a-z]', '', surname) or 'unknown'
    year = result.get('year', 'nd')
    title_word = re.split(r'\s+', result.get('title', 'untitled'))[0].lower()
    title_word = re.sub(r'[^a-z]', '', title_word) or 'untitled'
    return f"{surname}_{year}_{title_word}"


def _bibtex_type(result: dict) -> str:
    """Map content type to BibTeX entry type."""
    ct = result.get('content_type', result.get('_type', 'article'))
    mapping = {
        'article': 'article',
        'journal-article': 'article',
        'book': 'book',
        'book-chapter': 'incollection',
        'conference-paper': 'inproceedings',
        'dissertation': 'phdthesis',
        'report': 'techreport',
        'image': 'misc',
        'video': 'misc',
    }
    return mapping.get(ct, 'misc')


def _escape_bibtex(text: str) -> str:
    """Escape special BibTeX characters."""
    if not text:
        return ''
    text = text.replace('&', r'\&')
    text = text.replace('%', r'\%')
    text = text.replace('#', r'\#')
    text = text.replace('_', r'\_')
    return text


def format_bibtex(result: dict) -> str:
    """Format a single result as BibTeX."""
    entry_type = _bibtex_type(result)
    key = _bibtex_key(result)
    fields = []

    authors = result.get('authors', '')
    if authors:
        fields.append(f"  author = {{{_escape_bibtex(authors)}}}")

    title = _clean_text(result.get('title', ''))
    if title:
        fields.append(f"  title = {{{_escape_bibtex(title)}}}")

    year = result.get('year', '')
    if year and year != 'n.d.':
        fields.append(f"  year = {{{year}}}")

    journal = result.get('journal', '')
    if journal:
        fields.append(f"  journal = {{{_escape_bibtex(journal)}}}")

    volume = result.get('volume', '')
    if volume:
        fields.append(f"  volume = {{{volume}}}")

    issue = result.get('issue', '')
    if issue:
        fields.append(f"  number = {{{issue}}}")

    pages = result.get('pages', '')
    if pages:
        fields.append(f"  pages = {{{pages}}}")

    doi = result.get('doi', '')
    if doi:
        fields.append(f"  doi = {{{doi}}}")

    url = result.get('url') or result.get('source_page', '')
    if url:
        fields.append(f"  url = {{{url}}}")

    abstract = _clean_text(result.get('abstract', ''))
    if abstract and len(abstract) < 1000:
        fields.append(f"  abstract = {{{_escape_bibtex(abstract)}}}")

    return f"@{entry_type}{{{key},\n" + ",\n".join(fields) + "\n}"


def export_bibtex(results: List[dict]) -> str:
    """Export multiple results as a single BibTeX file."""
    entries = [format_bibtex(r) for r in results]
    return '\n\n'.join(entries)
