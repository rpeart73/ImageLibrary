/**
 * Iris Research Portal — Client-Side Logic
 *
 * Search, filter, select, export, reading list, brain assessment.
 */

let allResults = [];
let twoEyedResults = [];
let currentSort = 'quality';

// ─── Search ────────────────────────────────────────────

function doSearch(event) {
    if (event) event.preventDefault();

    const q = document.getElementById('research-q').value.trim();
    if (!q) return;

    const course = document.getElementById('research-course').value;
    const twoEyed = document.getElementById('two-eyed-toggle').checked ? '1' : '0';

    // Collect selected sources
    const sources = [...document.querySelectorAll('.src-toggle:checked')]
        .map(cb => cb.value).join(',');

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
                    <a href="/import-url?url=${encodeURIComponent(r.full_url)}" class="research-link">Import to Iris</a>
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

    const filtered = allResults.filter(r =>
        typeFilters.includes(r._type) && sourceFilters.includes(r.source)
    );
    renderResults(filtered);

    // Update counts
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
        a.download = `iris_research.${ext}`;
        a.click();
    }))
    .catch(err => alert('Export failed: ' + err.message));
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
