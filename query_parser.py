"""
Iris Research Portal — Query Parser

Parses Boolean (AND/OR/NOT), phrase ("exact match"), and field
(author:, title:, journal:) queries into a structured object that
API adapters consume.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ParsedQuery:
    """Structured representation of a research query."""
    raw: str
    terms: List[str] = field(default_factory=list)
    phrases: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)
    fields: dict = field(default_factory=dict)  # {field_name: value}
    operator: str = 'AND'  # default boolean operator

    @property
    def is_empty(self) -> bool:
        return not self.terms and not self.phrases and not self.fields

    def to_simple_query(self) -> str:
        """Flatten back to a simple string for APIs that don't support Boolean."""
        parts = list(self.phrases) + list(self.terms)
        return ' '.join(parts)

    def to_boolean_query(self) -> str:
        """Produce a Boolean query string for APIs that support it (e.g. OpenAlex)."""
        parts = []
        for phrase in self.phrases:
            parts.append(f'"{phrase}"')
        parts.extend(self.terms)
        joined = f' {self.operator} '.join(parts)
        if self.excluded:
            joined += ' NOT ' + ' NOT '.join(self.excluded)
        return joined

    def to_field_query(self, field_map: dict) -> dict:
        """Map parsed fields to API-specific parameter names.

        field_map: {'author': 'author.search', 'title': 'title.search', ...}
        Returns a dict of API parameters.
        """
        params = {}
        for local_field, value in self.fields.items():
            if local_field in field_map:
                params[field_map[local_field]] = value
        return params


# Field prefixes recognised in queries
FIELD_PREFIXES = {'author', 'title', 'journal', 'year', 'doi', 'subject', 'topic'}


def parse_query(raw: str) -> ParsedQuery:
    """Parse a raw search string into a structured ParsedQuery.

    Supports:
      - Phrases:   "controlling images"
      - Fields:    author:hooks  title:"coded bias"  journal:sociology
      - Boolean:   racial profiling AND algorithm NOT conference
      - Mixed:     author:hooks "controlling images" AND media NOT conference

    Returns a ParsedQuery dataclass.
    """
    raw = raw.strip()
    if not raw:
        return ParsedQuery(raw='')

    result = ParsedQuery(raw=raw)

    # 1. Extract field:value and field:"value with spaces" pairs
    field_pattern = re.compile(
        r'\b(' + '|'.join(FIELD_PREFIXES) + r'):'
        r'(?:"([^"]+)"|(\S+))',
        re.IGNORECASE
    )
    for match in field_pattern.finditer(raw):
        field_name = match.group(1).lower()
        value = match.group(2) or match.group(3)
        result.fields[field_name] = value

    # Remove field expressions from the working string
    working = field_pattern.sub('', raw).strip()

    # 2. Extract quoted phrases
    phrase_pattern = re.compile(r'"([^"]+)"')
    for match in phrase_pattern.finditer(working):
        result.phrases.append(match.group(1).strip())
    working = phrase_pattern.sub('', working).strip()

    # 3. Detect explicit Boolean operators
    #    Split on AND/OR/NOT (case-insensitive, word boundaries)
    has_and = bool(re.search(r'\bAND\b', working))
    has_or = bool(re.search(r'\bOR\b', working))

    if has_or and not has_and:
        result.operator = 'OR'

    # 4. Extract NOT terms
    not_pattern = re.compile(r'\bNOT\s+(\S+)', re.IGNORECASE)
    for match in not_pattern.finditer(working):
        result.excluded.append(match.group(1).strip())
    working = not_pattern.sub('', working)

    # 5. Remove Boolean operators from remaining text
    working = re.sub(r'\b(AND|OR)\b', ' ', working, flags=re.IGNORECASE)

    # 6. Remaining words become individual terms
    tokens = working.split()
    for token in tokens:
        token = token.strip('.,;:!?()')
        if token and token.lower() not in ('and', 'or', 'not'):
            result.terms.append(token)

    return result


# Two-Eyed Seeing augmentation map
# When enabled, searches simultaneously surface Indigenous perspectives
TWO_EYED_SEEING_MAP = {
    'surveillance': ['pass system', 'OCAP', 'Indigenous data sovereignty', 'digital colonialism'],
    'education': ['land-based learning', 'knowledge keeper', 'Indigenous pedagogy', 'Two Row Wampum'],
    'justice': ['restorative justice', 'treaty rights', 'Indigenous sovereignty', 'gladue'],
    'health': ['traditional medicine', 'Indigenous healing', 'medicine wheel', 'holistic health'],
    'technology': ['Indigenous futurism', 'digital divide', 'Indigenous innovation', 'tech colonialism'],
    'media': ['Indigenous media', 'APTN', 'Indigenous storytelling', 'oral tradition'],
    'policing': ['starlight tours', 'MMIW', 'over-policing', 'Thunder Bay police'],
    'identity': ['blood quantum', 'status Indian', 'Metis identity', 'self-determination'],
    'poverty': ['reserve conditions', 'boil water advisory', 'intergenerational trauma', 'TRC calls to action'],
    'environment': ['land defender', 'pipeline resistance', 'Indigenous environmentalism', 'water protector'],
    'music': ['powwow', 'throat singing', 'Indigenous hip-hop', 'A Tribe Called Red'],
    'sport': ['Tom Longboat', 'Indigenous athletes', 'lacrosse origins', 'Jim Thorpe'],
    'art': ['Norval Morrisseau', 'Woodland art', 'beadwork', 'Indigenous contemporary art'],
    'film': ['Alanis Obomsawin', 'Indigenous cinema', 'NFB Indigenous', 'Zacharias Kunuk'],
    'data': ['OCAP principles', 'Indigenous data sovereignty', 'FNIGC', 'First Nations data governance'],
    'algorithm': ['algorithmic colonialism', 'Indigenous AI ethics', 'bias and Indigenous peoples'],
    'stereotypes': ['noble savage', 'Indian princess', 'vanishing Indian', 'Hollywood Indian'],
    'resistance': ['Oka Crisis', 'Idle No More', 'Wet\'suwet\'en', 'Six Nations land reclamation'],
}


def augment_two_eyed_seeing(query: ParsedQuery) -> List[str]:
    """Given a parsed query, return additional search terms that surface
    Indigenous perspectives alongside Western ones.

    Returns a list of augmented query strings (each a separate search).
    """
    augmented = []
    all_terms = [t.lower() for t in query.terms] + [p.lower() for p in query.phrases]

    for term in all_terms:
        for key, indigenous_terms in TWO_EYED_SEEING_MAP.items():
            if key in term:
                for it in indigenous_terms:
                    augmented.append(it)

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for a in augmented:
        if a not in seen:
            seen.add(a)
            unique.append(a)

    return unique
