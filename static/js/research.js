/**
 * Loom Research — Client-Side Logic
 *
 * Search, filter, select, export, reading list, brain assessment.
 */

let allResults = [];
let twoEyedResults = [];
let currentSort = 'quality';

// External search URL generators
const EXTERNAL_URLS = {
    // Institutional (York gives access to ProQuest, EBSCO, SAGE, Wiley, Springer, T&F, Oxford, Cambridge)
    york_lib: q => `https://ocul-yor.primo.exlibrisgroup.com/discovery/search?query=any,contains,${encodeURIComponent(q)}&tab=Everything&search_scope=MyInst_and_CI&vid=01OCUL_YOR:YOR_DEFAULT`,
    jstor: q => `https://www.jstor.org/action/doBasicSearch?Query=${encodeURIComponent(q)}`,
    york_proquest: q => `https://www.proquest.com/advanced?query=${encodeURIComponent(q)}`,
    york_ebsco: q => `https://search.ebscohost.com/login.aspx?direct=true&bquery=${encodeURIComponent(q)}`,
    seneca_lib: q => `https://seneca.primo.exlibrisgroup.com/discovery/search?query=any,contains,${encodeURIComponent(q)}&tab=Everything&search_scope=MyInst_and_CI&vid=01SENC_INST:01SENC_NDE`,
    // General reference
    gscholar: q => `https://scholar.google.com/scholar?q=${encodeURIComponent(q)}`,
    britannica: q => `https://www.britannica.com/search?query=${encodeURIComponent(q)}`,
    // Black Studies
    blackpast: q => `https://www.blackpast.org/?s=${encodeURIComponent(q)}`,
    schomburg: q => `https://digitalcollections.nypl.org/search/index?utf8=%E2%9C%93&keywords=${encodeURIComponent(q)}#/?scroll=24`,
    aodl: q => `https://www.aodl.org/search?q=${encodeURIComponent(q)}`,
    bec: q => `https://www.bac-lac.gc.ca/eng/collectionsearch/Pages/collectionsearch.aspx?q=${encodeURIComponent(q)}`,
    project_muse: q => `https://muse.jhu.edu/search?action=search&query=${encodeURIComponent(q)}`,
    // Indigenous
    iportal: q => `https://iportal.usask.ca/search?q=${encodeURIComponent(q)}`,
    fnigc: q => `https://fnigc.ca/?s=${encodeURIComponent(q)}`,
    isumatv: q => `https://www.isuma.tv/search/node/${encodeURIComponent(q)}`,
    ourdigitalworld: q => `http://search.ourontario.ca/results?q=${encodeURIComponent(q)}`,
    // Canadian government
    statscan: q => `https://www.statcan.gc.ca/search/results/site-search?q=${encodeURIComponent(q)}`,
    canadiana: q => `https://www.canadiana.ca/search?q=${encodeURIComponent(q)}`,
    archives_ontario: q => `https://www.archives.gov.on.ca/en/access/search.aspx?q=${encodeURIComponent(q)}`,
};

// Research profiles
const PROFILES = {
    full: {
        api: ['openalex','core','crossref','semantic_scholar','eric','doaj','wikimedia','wikipedia','smithsonian','loc','dpla','internet_archive','europeana','lac'],
        ext: ['york_lib','jstor','seneca_lib','gscholar','blackpast','schomburg','bec','iportal'],
    },
    black: {
        api: ['openalex','core','crossref','semantic_scholar','eric','doaj','smithsonian','loc','dpla','internet_archive','wikimedia'],
        ext: ['york_lib','jstor','blackpast','schomburg','aodl','bec','project_muse','gscholar'],
    },
    indigenous: {
        api: ['openalex','core','eric','doaj','wikimedia','loc','dpla','internet_archive','lac'],
        ext: ['york_lib','iportal','fnigc','isumatv','ourdigitalworld','bec','gscholar','canadiana'],
    },
    canadian: {
        api: ['openalex','core','crossref','eric','wikimedia','loc','dpla','internet_archive','lac'],
        ext: ['york_lib','seneca_lib','bec','iportal','statscan','canadiana','archives_ontario','ourdigitalworld'],
    },
    quick: {
        api: ['openalex','semantic_scholar'],
        ext: ['gscholar'],
    },
    custom: null,
};

function setProfile(name) {
    document.querySelectorAll('.profile-btn').forEach(b => b.classList.remove('profile-active'));
    document.getElementById('prof-' + name).classList.add('profile-active');
    if (name === 'custom') { updateSourceCount(); return; }
    const profile = PROFILES[name];
    if (!profile) return;
    document.querySelectorAll('.src-toggle').forEach(c => c.checked = false);
    document.querySelectorAll('.ext-toggle').forEach(c => c.checked = false);
    profile.api.forEach(v => { const cb = document.querySelector(`.src-toggle[value="${v}"]`); if (cb) cb.checked = true; });
    profile.ext.forEach(v => { const cb = document.querySelector(`.ext-toggle[value="${v}"]`); if (cb) cb.checked = true; });
    updateSourceCount();
}

function onSourceChange() {
    document.querySelectorAll('.profile-btn').forEach(b => b.classList.remove('profile-active'));
    document.getElementById('prof-custom').classList.add('profile-active');
    updateSourceCount();
}

function selectAllSources() {
    document.querySelectorAll('.src-toggle, .ext-toggle').forEach(c => c.checked = true);
    document.querySelectorAll('.profile-btn').forEach(b => b.classList.remove('profile-active'));
    document.getElementById('prof-custom').classList.add('profile-active');
    updateSourceCount();
}

function clearAllSources() {
    document.querySelectorAll('.src-toggle, .ext-toggle').forEach(c => c.checked = false);
    document.querySelectorAll('.profile-btn').forEach(b => b.classList.remove('profile-active'));
    document.getElementById('prof-custom').classList.add('profile-active');
    updateSourceCount();
}

// ─── Library Authentication ────────────────────────────

const authState = { york: false, seneca: false };

document.addEventListener('DOMContentLoaded', function() {
    // Load auth state from sessionStorage
    ['york', 'seneca'].forEach(lib => {
        if (sessionStorage.getItem('auth_' + lib) === '1') {
            authState[lib] = true;
            markConnected(lib);
        }
    });

    // Track clicks on auth links
    document.querySelectorAll('.auth-link').forEach(link => {
        link.addEventListener('click', function() {
            const lib = this.id.replace('auth-', '');
            setTimeout(() => {
                authState[lib] = true;
                sessionStorage.setItem('auth_' + lib, '1');
                markConnected(lib);
                updateAuthStatus();
            }, 500);
        });
    });
    updateAuthStatus();
});

function markConnected(lib) {
    const el = document.getElementById('auth-' + lib);
    if (el) {
        el.classList.add('connected');
        el.textContent = el.textContent.replace('Log in to', '').trim() + ' (connected)';
    }
}

function updateAuthStatus() {
    const connected = Object.values(authState).filter(Boolean).length;
    const total = Object.keys(authState).length;
    const el = document.getElementById('auth-status');
    if (el) {
        if (connected === 0) el.textContent = 'Not connected';
        else if (connected === total) el.textContent = 'All libraries connected';
        else el.textContent = connected + '/' + total + ' connected';
    }
}

// ─── Source Count ──────────────────────────────────────

function updateSourceCount() {
    const apiCount = document.querySelectorAll('.src-toggle:checked').length;
    const extCount = document.querySelectorAll('.ext-toggle:checked').length;
    const total = apiCount + extCount;

    const label = document.getElementById('source-count-label');
    if (label) label.textContent = total + ' source' + (total !== 1 ? 's' : '') + ' selected';

    const heading = document.getElementById('empty-heading');
    if (heading) heading.textContent = 'Search ' + total + ' database' + (total !== 1 ? 's' : '') + ' at once';

    const loadingText = document.querySelector('.research-loading-text');
    if (loadingText) loadingText.textContent = 'Searching ' + apiCount + ' databases' + (extCount ? ' + ' + extCount + ' external...' : '...');
}

// ─── Search ────────────────────────────────────────────

function doSearch(event) {
    if (event) event.preventDefault();

    const q = document.getElementById('research-q').value.trim();
    if (!q) return;

    const course = document.getElementById('research-course').value;
    const twoEyed = document.getElementById('two-eyed-toggle').checked ? '1' : '0';

    // Collect selected API sources
    const sources = [...document.querySelectorAll('.src-toggle:checked')]
        .map(cb => cb.value).join(',');

    // Open external sources in new tabs
    document.querySelectorAll('.ext-toggle:checked').forEach(cb => {
        const urlFn = EXTERNAL_URLS[cb.value];
        if (urlFn) window.open(urlFn(q), '_blank');
    });

    // Update URL without reload
    const url = new URL(window.location);
    url.searchParams.set('q', q);
    if (course) url.searchParams.set('course', course);
    else url.searchParams.delete('course');
    window.history.replaceState({}, '', url);

    // Show loading, hide others
    document.getElementById('research-empty').style.display = 'none';
    document.getElementById('research-loading').style.display = 'block';
    document.getElementById('research-results-container').style.display = 'none';

    const apiCount = document.querySelectorAll('.src-toggle:checked').length;
    const extCount = document.querySelectorAll('.ext-toggle:checked').length;
    document.querySelector('.research-loading-text').textContent =
        'Searching ' + apiCount + ' databases' + (extCount ? ' + ' + extCount + ' external...' : '...');
    const sourcesDisplay = sources.split(',')
        .map(s => s.replace('_', ' '))
        .map(s => s.charAt(0).toUpperCase() + s.slice(1))
        .join(', ');
    document.getElementById('loading-sources').textContent = sourcesDisplay;

    fetch(`/api/research-search?q=${encodeURIComponent(q)}&course=${encodeURIComponent(course)}&sources=${encodeURIComponent(sources)}&two_eyed=${twoEyed}`)
        .then(r => r.json())
        .then(data => {
            document.getElementById('research-loading').style.display = 'none';

            // Tag each result with a stable index
            allResults = data.results.map((r, i) => ({...r, _idx: i}));
            twoEyedResults = (data.two_eyed_results || []).map((r, i) => ({...r, _idx: 10000 + i}));

            if (!allResults.length && !twoEyedResults.length) {
                document.getElementById('research-empty').style.display = 'block';
                document.getElementById('research-empty').innerHTML =
                    `<h3>No results found</h3><p>Try broader terms, different sources, or remove Boolean operators.</p>`;
                return;
            }

            document.getElementById('research-results-container').style.display = 'grid';

            // Results header
            const articles = allResults.filter(r => r._type === 'article');
            const images = allResults.filter(r => r._type === 'image');
            document.getElementById('results-count').textContent = allResults.length + ' results';
            document.getElementById('results-breakdown').textContent =
                articles.length + ' articles, ' + images.length + ' images';
            document.getElementById('count-articles').textContent = articles.length;
            document.getElementById('count-images').textContent = images.length;
            document.getElementById('count-peer-reviewed').textContent =
                allResults.filter(r => r.is_peer_reviewed).length;
            document.getElementById('count-open-access').textContent =
                allResults.filter(r => r.is_open_access).length;

            // Sources searched
            const searched = data.sources_searched || [];
            document.getElementById('results-sources').textContent =
                searched.length + ' sources searched';

            // Source facets
            const sourceCounts = data.source_counts || {};
            let facetHtml = '<h4>Source</h4>';
            Object.entries(sourceCounts)
                .sort((a, b) => b[1] - a[1])
                .forEach(([src, cnt]) => {
                    facetHtml += `<label><input type="checkbox" class="source-filter" value="${src}" checked onchange="filterResults()"> ${src} <span class="sidebar-count">${cnt}</span></label>`;
                });
            document.getElementById('source-facets').innerHTML = facetHtml;

            // Parsed query display
            if (data.parsed && (data.parsed.phrases.length || data.parsed.fields && Object.keys(data.parsed.fields).length || data.parsed.excluded.length)) {
                const info = document.getElementById('parsed-query-info');
                let parts = [];
                if (data.parsed.phrases.length) parts.push('Phrases: "' + data.parsed.phrases.join('", "') + '"');
                if (data.parsed.excluded.length) parts.push('Excluded: ' + data.parsed.excluded.join(', '));
                if (data.parsed.fields && Object.keys(data.parsed.fields).length) {
                    parts.push('Fields: ' + Object.entries(data.parsed.fields).map(([k,v]) => k+':'+v).join(', '));
                }
                info.textContent = 'Parsed: ' + parts.join(' | ');
                info.style.display = 'block';
            } else {
                document.getElementById('parsed-query-info').style.display = 'none';
            }

            renderResults(allResults);

            // Two-Eyed Seeing
            if (twoEyedResults.length) {
                document.getElementById('two-eyed-section').style.display = 'block';
                renderTwoEyedResults(twoEyedResults);
            } else {
                document.getElementById('two-eyed-section').style.display = 'none';
            }
        })
        .catch(err => {
            document.getElementById('research-loading').style.display = 'none';
            document.getElementById('research-empty').style.display = 'block';
            document.getElementById('research-empty').innerHTML =
                `<h3>Search failed</h3><p>${err.message}</p>`;
        });
}

// ─── Render ────────────────────────────────────────────

function renderResults(items) {
    const list = document.getElementById('research-results-list');
    if (!items.length) {
        list.innerHTML = '<div class="research-empty"><p>No results match the current filters.</p></div>';
        return;
    }
    list.innerHTML = items.map(r => renderCard(r)).join('');
}

function renderTwoEyedResults(items) {
    const list = document.getElementById('two-eyed-results-list');
    list.innerHTML = items.map(r => renderCard(r, true)).join('');
}

function renderCard(r, isTwoEyed) {
    const qualityClass = r.quality_score > 70 ? 'quality-high' :
                         r.quality_score > 40 ? 'quality-medium' : 'quality-low';
    const twoEyedClass = isTwoEyed ? ' two-eyed-result' : '';

    if (r._type === 'image') {
        return `
        <div class="research-result${twoEyedClass}" data-type="image" data-source="${r.source}" data-idx="${r._idx}">
            <label class="research-select"><input type="checkbox" class="result-cb" data-idx="${r._idx}" onchange="updateCounts()"></label>
            <div class="research-result-thumb">
                <img src="${r.thumb_url}" alt="${esc(r.title)}" loading="lazy"
                     onerror="this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2280%22 height=%2280%22><rect fill=%22%23e8f0eb%22 width=%2280%22 height=%2280%22/></svg>'">
            </div>
            <div class="research-result-body">
                <div class="research-result-title">${esc(r.title)}</div>
                <div class="research-result-meta">
                    ${r.creator || 'Unknown creator'}
                    ${r.width && r.height ? ' | ' + r.width + ' x ' + r.height : ''}
                </div>
                <div class="research-result-meta">
                    <span class="type-tag type-tag-image">Image</span>
                    <span class="source-badge">${esc(r.source)}</span>
                    <span class="quality-badge ${qualityClass}">Q${r.quality_score}</span>
                    ${r.license ? '<span class="source-badge">' + esc(r.license) + '</span>' : ''}
                </div>
                ${r.description ? '<div class="research-result-abstract">' + esc(r.description) + '</div>' : ''}
                ${isTwoEyed && r.indigenous_term ? '<div style="font-size:11px;color:#8b5e3c;margin-top:4px">Indigenous perspective: ' + esc(r.indigenous_term) + '</div>' : ''}
                <div class="research-result-actions">
                    ${r.source_page ? '<a href="' + r.source_page + '" target="_blank" class="research-link">View source</a>' : ''}
                    <a href="/import-url?url=${encodeURIComponent(r.full_url)}" class="research-link">Import to Library</a>
                </div>
            </div>
        </div>`;
    }

    // Article card
    return `
    <div class="research-result${twoEyedClass}" data-type="article" data-source="${r.source}" data-idx="${r._idx}">
        <label class="research-select"><input type="checkbox" class="result-cb" data-idx="${r._idx}" onchange="updateCounts()"></label>
        <div class="research-result-body">
            <div class="research-result-title">
                ${r.url ? '<a href="' + r.url + '" target="_blank">' + esc(r.title) + '</a>' : esc(r.title)}
            </div>
            <div class="research-result-authors">${esc(r.authors || 'Unknown')} (${r.year || 'n.d.'})</div>
            <div class="research-result-meta">
                <span class="type-tag type-tag-article">Article</span>
                <span class="source-badge">${esc(r.source)}</span>
                <span class="quality-badge ${qualityClass}">Q${r.quality_score}</span>
                ${r.is_open_access ? '<span class="oa-badge">Open Access</span>' : ''}
                ${r.is_peer_reviewed ? '<span class="source-badge">Peer-reviewed</span>' : ''}
                ${r.citation_count > 0 ? '<span class="source-badge">Cited ' + r.citation_count + '</span>' : ''}
            </div>
            ${r.journal ? '<div class="research-result-meta"><em>' + esc(r.journal) + '</em>' + (r.volume ? ' ' + r.volume : '') + (r.issue ? '(' + r.issue + ')' : '') + (r.pages ? ', ' + r.pages : '') + '</div>' : ''}
            ${r.abstract ? '<div class="research-result-abstract">' + esc(r.abstract) + '</div>' : ''}
            ${r.tags && r.tags.length ? '<div class="research-result-tags">' + r.tags.map(t => '<span class="tag">' + esc(t) + '</span>').join('') + '</div>' : ''}
            ${isTwoEyed && r.indigenous_term ? '<div style="font-size:11px;color:#8b5e3c;margin-top:4px">Indigenous perspective: ' + esc(r.indigenous_term) + '</div>' : ''}
            <details class="citation-panel" style="margin-top:6px">
                <summary style="font-size:12px;color:var(--accent);cursor:pointer">APA 7th Citation</summary>
                <code class="apa-citation" style="font-size:12px;word-break:break-word">${esc(r.apa_citation || '')}</code>
            </details>
            <div class="research-result-actions">
                ${r.url ? '<a href="' + r.url + '" target="_blank" class="research-link">Full text</a>' : ''}
                ${r.doi ? '<a href="https://doi.org/' + r.doi + '" target="_blank" class="research-link">DOI</a>' : ''}
                ${r.pdf_url ? '<a href="' + r.pdf_url + '" target="_blank" class="research-link">PDF</a>' : ''}
                ${r.url ? '<a href="#" class="research-link" style="font-weight:600" onclick="openReader('+r._idx+'); return false;">Read</a>' : ''}
                <a href="#" class="research-link" style="color:var(--accent);font-weight:600" onclick="pushToZotero(${r._idx}); return false;">Push to Zotero</a>
                <a href="#" class="research-link" onclick="addOneToReadingList(${r._idx}); return false;">Save</a>
            </div>
        </div>
    </div>`;
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ─── Filtering ─────────────────────────────────────────

function filterResults() {
    const typeFilters = [...document.querySelectorAll('.type-filter:checked')].map(cb => cb.value);
    const sourceFilters = [...document.querySelectorAll('.source-filter:checked')].map(cb => cb.value);
    const peerOnly = document.getElementById('peer-reviewed-filter').checked;
    const oaOnly = document.getElementById('open-access-filter').checked;
    const verifiedOnly = document.getElementById('verified-filter').checked;

    const filtered = allResults.filter(r => {
        if (!typeFilters.includes(r._type)) return false;
        if (!sourceFilters.includes(r.source)) return false;
        if (peerOnly && !r.is_peer_reviewed) return false;
        if (oaOnly && !r.is_open_access) return false;
        if (verifiedOnly && !r._verified) return false;
        return true;
    });
    renderResults(filtered);

    const articles = filtered.filter(r => r._type === 'article').length;
    const images = filtered.filter(r => r._type === 'image').length;
    document.getElementById('results-count').textContent = filtered.length + ' results';
    document.getElementById('results-breakdown').textContent = articles + ' articles, ' + images + ' images';
}

// ─── Sorting ───────────────────────────────────────────

function sortResults() {
    currentSort = document.getElementById('results-sort').value;
    const sortFn = {
        'quality': (a, b) => (b.quality_score || 0) - (a.quality_score || 0),
        'citations': (a, b) => (b.citation_count || 0) - (a.citation_count || 0),
        'year-desc': (a, b) => {
            const ya = parseInt(a.year) || 0, yb = parseInt(b.year) || 0;
            return yb - ya;
        },
        'year-asc': (a, b) => {
            const ya = parseInt(a.year) || 9999, yb = parseInt(b.year) || 9999;
            return ya - yb;
        },
        'title': (a, b) => (a.title || '').localeCompare(b.title || ''),
    };
    allResults.sort(sortFn[currentSort] || sortFn['quality']);
    filterResults();
}

// ─── Selection ─────────────────────────────────────��───

function getAllResults() {
    return [...allResults, ...twoEyedResults];
}

function getSelected() {
    const checked = [...document.querySelectorAll('.result-cb:checked')];
    return checked.map(cb => {
        const idx = parseInt(cb.dataset.idx);
        return getAllResults().find(r => r._idx === idx);
    }).filter(Boolean);
}

function updateCounts() {
    const selected = getSelected();
    const imgCount = selected.filter(r => r._type === 'image').length;
    const total = selected.length;

    document.getElementById('reading-list-btn').disabled = total === 0;
    document.getElementById('reading-list-btn').textContent = 'Add to Reading List (' + total + ')';
    document.getElementById('assess-btn').disabled = total === 0;
    document.getElementById('assess-btn').textContent = 'Brain Assess (' + total + ')';
    document.getElementById('import-btn').disabled = imgCount === 0;
    document.getElementById('import-btn').textContent = 'Import Images (' + imgCount + ')';
    document.getElementById('export-apa-btn').disabled = total === 0;
    document.getElementById('export-ris-btn').disabled = total === 0;
    document.getElementById('export-bib-btn').disabled = total === 0;

    // Highlight selected cards
    document.querySelectorAll('.research-result').forEach(card => {
        const cb = card.querySelector('.result-cb');
        card.classList.toggle('selected', cb && cb.checked);
    });
}

function toggleSelectAll() {
    document.querySelectorAll('.result-cb').forEach(cb => cb.checked = true);
    updateCounts();
}

function deselectAll() {
    document.querySelectorAll('.result-cb').forEach(cb => cb.checked = false);
    updateCounts();
}

// ─── Export ────────────────────────────────────────────

function exportSelected(format) {
    const selected = getSelected();
    if (!selected.length) return;

    fetch('/api/research-export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({format: format, results: selected}),
    })
    .then(resp => resp.blob().then(blob => {
        const ext = {apa: 'txt', ris: 'ris', bibtex: 'bib'}[format] || 'txt';
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = `loom_research.${ext}`;
        a.click();
    }))
    .catch(err => alert('Export failed: ' + err.message));
}

// ─── Document Reader ───────────────────────────────────

function openReader(idx) {
    const r = getAllResults().find(x => x._idx === idx);
    if (!r || !r.url) return;

    const panel = document.getElementById('reader-panel');
    const content = document.getElementById('reader-content');
    const titleEl = document.getElementById('reader-title');
    const linkEl = document.getElementById('reader-source-link');

    titleEl.textContent = r.title || 'Loading...';
    linkEl.href = r.url;
    content.innerHTML = '<div class="reader-loading">Loading preview...</div>';
    panel.style.display = 'block';
    panel.scrollIntoView({behavior: 'smooth'});

    fetch('/api/preview?url=' + encodeURIComponent(r.url))
        .then(resp => resp.json())
        .then(data => {
            titleEl.textContent = data.title || r.title || 'Document';

            if (data.type === 'pdf') {
                content.innerHTML = '<iframe src="' + esc(data.url) + '"></iframe>';
            } else if (data.type === 'article') {
                const paragraphs = data.text.split('\n\n').map(p => '<p>' + esc(p) + '</p>').join('');
                content.innerHTML = paragraphs +
                    '<p style="font-size:12px;color:var(--text-muted);margin-top:20px;border-top:1px solid var(--border);padding-top:12px">' +
                    'Source: <a href="' + esc(data.url) + '" target="_blank">' + esc(data.url) + '</a>' +
                    (data.source_verified ? ' (content verified from source)' : '') + '</p>';
            } else {
                content.innerHTML = '<p>' + esc(data.text || 'Preview unavailable.') + '</p>' +
                    '<p><a href="' + esc(r.url) + '" target="_blank">Open full text in new tab</a></p>';
            }
        })
        .catch(err => {
            content.innerHTML = '<p>Failed to load preview: ' + esc(err.message) + '</p>' +
                '<p><a href="' + esc(r.url) + '" target="_blank">Open in new tab instead</a></p>';
        });
}

function closeReader() {
    document.getElementById('reader-panel').style.display = 'none';
}

// ─── Push to Zotero (single item RIS download) ────────

function pushToZotero(idx) {
    const r = getAllResults().find(x => x._idx === idx);
    if (!r) return;
    fetch('/api/research-export', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({format: 'ris', results: [r]}),
    })
    .then(resp => resp.blob().then(blob => {
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        const safe = (r.title || 'source').replace(/[^a-zA-Z0-9]/g, '_').substring(0, 40);
        a.download = safe + '.ris';
        a.click();
    }));
}

function addOneToReadingList(idx) {
    const r = getAllResults().find(x => x._idx === idx);
    if (!r) return;
    fetch('/api/reading-list', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(r),
    })
    .then(resp => {
        if (resp.status === 201) {
            const card = document.querySelector(`[data-idx="${idx}"]`);
            if (card) card.style.borderLeftColor = 'var(--accent)';
        }
    });
}

// ─── Reading List ──────────────────────────────────────

function addToReadingList() {
    const selected = getSelected();
    if (!selected.length) return;

    const btn = document.getElementById('reading-list-btn');
    btn.disabled = true;
    btn.textContent = 'Adding...';

    let added = 0, skipped = 0;
    const promises = selected.map(r =>
        fetch('/api/reading-list', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(r),
        })
        .then(resp => { if (resp.status === 201) added++; else skipped++; })
        .catch(() => skipped++)
    );

    Promise.all(promises).then(() => {
        btn.textContent = added + ' added' + (skipped ? ', ' + skipped + ' already saved' : '');
        setTimeout(() => { btn.disabled = false; updateCounts(); }, 2000);
        loadReadingList();
    });
}

function loadReadingList() {
    fetch('/api/reading-list')
        .then(r => r.json())
        .then(items => {
            const panel = document.getElementById('reading-list-panel');
            const list = document.getElementById('reading-list-items');
            if (!items.length) {
                panel.style.display = 'none';
                return;
            }
            panel.style.display = 'block';
            list.innerHTML = items.map(item => `
                <div class="reading-list-item" data-id="${item.id}">
                    <span>${esc(item.title)} <span style="color:var(--text-muted);font-size:11px">(${esc(item.source)})</span></span>
                    <span class="reading-list-remove" onclick="removeFromReadingList(${item.id})">Remove</span>
                </div>
            `).join('');
        });
}

function removeFromReadingList(id) {
    fetch('/api/reading-list?id=' + id, {method: 'DELETE'})
        .then(() => loadReadingList());
}

// ─── Brain Assessment ──────────────────────────────────

function brainAssess() {
    const selected = getSelected();
    if (!selected.length) return;

    const btn = document.getElementById('assess-btn');
    btn.disabled = true;
    btn.textContent = 'Asking Seneca Brain...';

    const panel = document.getElementById('brain-panel');
    const assessDiv = document.getElementById('brain-assessment');
    panel.style.display = 'block';
    assessDiv.innerHTML = '<p class="research-loading-text">Querying Seneca Brain for curriculum relevance...</p>';
    panel.scrollIntoView({behavior: 'smooth'});

    const query = document.getElementById('research-q').value;
    const course = document.getElementById('research-course').value;
    const items = selected.slice(0, 8).map(r => ({
        title: r.title,
        description: (r.description || r.abstract || '').substring(0, 150),
        source: r.source,
    }));

    fetch('/api/brain-assess', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query: query, course: course, results: items}),
    })
    .then(r => r.json())
    .then(data => {
        btn.disabled = false;
        btn.textContent = 'Brain Assess (' + selected.length + ')';
        if (data.assessment) {
            const formatted = data.assessment
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\n\n/g, '<br><br>')
                .replace(/\n/g, '<br>');
            assessDiv.innerHTML = '<div class="brain-response">' + formatted + '</div>';
            if (data.course) {
                assessDiv.innerHTML += '<p style="font-size:12px;color:var(--text-muted);margin-top:8px">Assessed for: <strong>' + esc(data.course) + '</strong></p>';
            }
        } else {
            assessDiv.innerHTML = '<p>Brain assessment unavailable.</p>';
        }
    })
    .catch(err => {
        btn.disabled = false;
        btn.textContent = 'Brain Assess (' + selected.length + ')';
        assessDiv.innerHTML = '<p>Failed: ' + esc(err.message) + '</p>';
    });
}

// ─── Import Images ─────────────────────────────────────

function importSelectedImages() {
    const selected = getSelected().filter(r => r._type === 'image');
    if (!selected.length) return;

    const btn = document.getElementById('import-btn');
    btn.disabled = true;
    let imported = 0, failed = 0;

    function importNext(i) {
        if (i >= selected.length) {
            btn.textContent = imported + ' imported' + (failed ? ', ' + failed + ' failed' : '');
            setTimeout(() => { btn.disabled = false; updateCounts(); }, 2000);
            return;
        }
        const r = selected[i];
        btn.textContent = 'Importing ' + (i + 1) + '/' + selected.length + '...';
        fetch('/import-url', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'url=' + encodeURIComponent(r.full_url),
            redirect: 'follow',
        })
        .then(resp => {
            if (resp.ok || resp.redirected) {
                imported++;
                const card = document.querySelector(`[data-idx="${r._idx}"]`);
                if (card) card.classList.add('imported');
            } else failed++;
            importNext(i + 1);
        })
        .catch(() => { failed++; importNext(i + 1); });
    }
    importNext(0);
}

// ─── Saved Searches ────────────────────────────────────

function saveCurrentSearch() {
    const q = document.getElementById('research-q').value.trim();
    if (!q) return;
    const course = document.getElementById('research-course').value;

    const name = prompt('Name this search:', q.substring(0, 50));
    if (!name) return;

    fetch('/api/saved-searches', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: name, query: q, course: course}),
    })
    .then(r => r.json())
    .then(data => {
        if (data.ok) alert('Search saved: ' + name);
    });
}

// ─── Search History ────────────────────────────────────

function loadSearchHistory() {
    fetch('/api/search-history')
        .then(r => r.json())
        .then(items => {
            if (!items.length) return;
            const section = document.getElementById('search-history-section');
            const list = document.getElementById('search-history-list');
            section.style.display = 'block';
            // Deduplicate by query text
            const seen = new Set();
            const unique = items.filter(i => {
                if (seen.has(i.query.toLowerCase())) return false;
                seen.add(i.query.toLowerCase());
                return true;
            }).slice(0, 15);
            list.innerHTML = unique.map(i =>
                `<span class="history-item" onclick="runHistorySearch('${esc(i.query)}')">${esc(i.query)}</span>`
            ).join('');
        });
}

function runHistorySearch(query) {
    document.getElementById('research-q').value = query;
    doSearch();
}
