import {TYPES, LABELS, content, escape as e, validateDataset, filter, facets, facetRecords, applicableFilters, matchesStatus, values, CONTEXTUAL, sorted, recordedTime, resolveTarget, appendVisit, sameDestination, briefing, groupImplementations, slug, hashText, commitMetadataHtml, recordTimestamp, formatTimestamp, browserTimeZone, provenanceHtml, logTitle, INFERRED_MARK, DERIVED_MARK, queueModel, lightModel, stripModel, dataSourceLabel} from './core.mjs';

const $ = selector => document.querySelector(selector);
let items = [], trail = [], cursor = 0, devlogOff = false, declaredGroups = [], groupsProblem = '';
const blankFilters = () => ({type:[],categories:[],topics:[],status:[],classes:[],subclasses:[],worker:[]});
const zone = browserTimeZone() ?? undefined;
function displayTitle(i) {
  if (i.type === 'devlog') return logTitle(i);
  if (i.type !== 'rationale' || !i.decisionIds?.length) return i.title;
  const prefix = `${i.decisionIds.join(', ')} · `;
  const title = i.title.startsWith(prefix) ? i.title.slice(prefix.length) : i.title.replace(/:\s*[GD]\d{3}\b.*$/, '');
  return prefix + title;
}
const viewLabel = visit => visit.page === 'detail' ? (items.find(i => i.id === visit.id) ? displayTitle(items.find(i => i.id === visit.id)) : visit.id) : visit.page === 'browse' ? (visit.section ? LABELS[visit.section] : 'Search' + (visit.filters?.type?.length ? ' · ' + visit.filters.type.map(type => LABELS[type]).join(' + ') : ' · All')) : ({home:'Overview',briefing:'Status briefing',queue:'Queue'}[visit.page]);
const urlFor = visit => `#${visit.page}${visit.id ? '/' + encodeURIComponent(visit.id) : ''}${visit.fragment ? '/' + encodeURIComponent(visit.fragment) : ''}`;
function current() { return trail[cursor]; }
function save(replace = false) {
  const state = {missionControl:1, trail, cursor};
  history[replace ? 'replaceState' : 'pushState'](state,'',urlFor(current()));
}
function navigate(visit) {
  const nextTrail = appendVisit(trail,cursor,visit);
  if (nextTrail === trail) return;
  trail = nextTrail; cursor = trail.length - 1;
  save(); render(); $('#view').focus(); window.scrollTo(0,0);
}
const shortHashes = text => String(text ?? '').replace(/(?<![A-Za-z0-9])(?:[a-f0-9]{64}|[a-f0-9]{40})(?![A-Za-z0-9])/g, sha => sha.slice(0,12));
function itemButton(i, label = displayTitle(i)) { return `<button class="open" data-item="${e(i.id)}" title="${e(i.sha ? (content(i).split('\n')[0] || i.title) + '\n' + i.sha : label)}">${e(shortHashes(label))}</button>`; }
function badges(i) {
  return `<span class="badge">${e(LABELS[i.type])}</span>${i.status ? `<span class="badge" title="${e(i.status)}">${e(i.status)}</span>` : ''}${i.source.selection === 'proposal' ? '<span class="badge proposal">Proposal source</span>' : ''}`;
}
function commitIdentity(i) {
  return `<p class="source-small">${provenanceHtml(i,items,zone)} · ${commitMetadataHtml(i)}</p>`;
}
function sourcePreview(i) {
  return `<p class="source-small">${provenanceHtml(i,items,zone)}</p>`;
}
// Omit summaries that only restate the card title: a commit subject.
const summaryText = i => (i.type === 'commits' && i.summary === i.title) ? '' : i.summary ?? '';
function card(i, compact = false) {
  const description = i.type === 'decisions'
      ? (i.summary ?? '').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/[`*]/g,'').split(/\.\s/)[0]
      : '';
    const shortDescription = description.length > 180 ? description.slice(0,180).replace(/\s+\S*$/,'') + '…' : description;
  const label = shortDescription ? `${i.title} · ${shortDescription}` : displayTitle(i);
  if (compact) {
    if (i.type === 'documents') {
      return `<article class="card compact-card"><h3>${itemButton(i)}</h3><div class="document-status-row"><span class="badge">${e(LABELS[i.type])}</span>${i.status ? `<span class="badge document-status">${e(i.status)}</span>` : ''}${i.source.selection === 'proposal' ? '<span class="badge proposal">Proposal source</span>' : ''}</div>${sourcePreview(i)}</article>`;
    }
    const status = i.status?.length > 80
      ? `<details><summary>Status details</summary><span class="badge">${e(i.status)}</span></details>`
      : i.status ? `<span class="badge">${e(i.status)}</span>` : '';
    return `<article class="card compact-card"><h3>${itemButton(i,label)}</h3>${badges({...i,status:null})}${status}${sourcePreview(i)}</article>`;
  }
  const summary = summaryText(i);
  return `<article class="card">${badges(i)}<h3>${itemButton(i, label)}</h3>${summary ? `<p class="summary">${hashText(summary.slice(0,280),items)}${summary.length > 280 ? '…' : ''}</p>` : ''}${i.type === 'commits' && current().page === 'browse' ? commitIdentity(i) : sourcePreview(i)}</article>`;
}
function panel(title, list, note='', compact = false) { return `<section class="panel"><h3>${e(title)} <span class="micro">/ ${list.length}</span></h3>${note ? `<p class="summary">${e(note)}</p>` : ''}<div class="cards">${list.length ? list.map(i => card(i,compact)).join('') : '<p class="empty">No records in this snapshot.</p>'}</div></section>`; }
// The main bar has two cells with a divider: the pages that summarize, then the record sections that open the search,
// from the most to the least authoritative.
const VIEW_PAGES = [['queue','Queue'],['home','Overview'],['briefing','Status briefing']];
const RECORD_ORDER = ['decisions','rationale','documents','commits','devlog'];
// The hover text of each main button says what the page or the section holds.
const TIPS = {
  queue: 'The local job queue and the active claims, read from the coordination notes of this machine. Not authoritative.',
  home: 'Record counts, the most recent commits and the most recent devlog entries.',
  briefing: 'The recorded status of the plan, progress, architecture and design documents, with the current decisions beside them.',
  decisions: 'The standing decisions, each with its status and history. They bind the work.',
  rationale: 'Why the decisions were made: the reasons, the alternatives and what the lead developer supplied. Explanatory, not binding.',
  documents: 'The documents of the project: rules, specifications, designs, plan, progress and architecture.',
  commits: 'The commit history, with the class, subclass and worker of each commit.',
  devlog: 'The historical log of the project of meaningful changes. Human-oriented and not binding; a project may keep none.'
};
const searchedType = visit => visit.page === 'browse' ? (visit.section ?? (visit.filters?.type?.length === 1 ? visit.filters.type[0] : null)) : null;
function header() {
  const cell = (label, buttons) => `<div class="bar-cell" role="group" aria-label="${label}">${buttons}</div>`;
  $('#primary').innerHTML = cell('Views',VIEW_PAGES.map(([page,label]) => `<button data-page="${page}" title="${e(TIPS[page])}" aria-pressed="${current().page === page}">${label}</button>`).join(''))
    + cell('Records',RECORD_ORDER.filter(type => TYPES.includes(type)).map(type => `<button data-type="${type}" title="${e(TIPS[type] + ' Opens the search with this section selected.')}" aria-pressed="${searchedType(current()) === type}">${e(LABELS[type])}</button>`).join(''));
  $('#breadcrumbs').hidden = cursor === 0;
  $('#breadcrumbs').innerHTML = trail.slice(0,cursor + 1).map((visit,index) => index > 0 && index < cursor - 3 ? (index === 1 ? `<span title="${cursor - 4} earlier visits hidden" aria-label="${cursor - 4} earlier visits hidden">…</span>` : '') : `<button data-crumb="${index}" class="${index === cursor ? 'current' : ''}" ${index === cursor ? 'aria-current="page"' : ''}>${e(viewLabel(visit))}</button>`).filter(Boolean).join('<span aria-hidden="true">›</span>');
}
function home() {
  const commits = items.filter(i => i.type === 'commits');
  const latest = sorted(commits,'recent')[0];
  return `<div class="section-head"><div><p class="eyebrow">DECLARED REPOSITORY RECORDS</p><h2>A clear view of the project.</h2></div><button data-page="briefing">Open Status briefing →</button></div><p class="intro">Explore decisions, engineering history and the documents behind them. Every observation leads back to its declared source. This terminal has no write or workflow controls.</p><p class="counts">${RECORD_ORDER.map(type => `<span><strong>${items.filter(i => i.type === type).length}</strong> ${e(LABELS[type].toLowerCase())}</span>`).join(' · ')}</p><div class="notice"><strong>Snapshot context.</strong> ${items.length} records loaded from locally generated JSON files. Latest recorded commit by committer time: <code>${hashText(latest?.sha ?? 'none',items)}</code>. This is coverage inside the snapshot, not proof of current HEAD. Proposal-origin records remain labeled proposal. Later main changes may be absent.</div><div class="grid">${panel('Recent recorded commits',sorted(commits,'recent').slice(0,4))}${panel('Recent devlog entries',sorted(items.filter(i => i.type === 'devlog'),'recent').slice(0,4))}</div>`;
}
function briefingView() {
  const c = briefing(items);
  const lenses = [['PLAN','docs/PLAN.md'],['PROGRESS','docs/PROGRESS.md'],['ARCHITECTURE','docs/ARCHITECTURE.md']];
  const paths = new Set(lenses.map(([,path]) => path));
  const implementations = c.documents.filter(i => !paths.has(i.source.path));
  // Presentation-only groups using exact source paths already present in JSON, declared by the installed extensions and by
  // the project and served as /groups.json; nothing is listed here.
  const groups = groupImplementations(implementations,declaredGroups);
  const implementationView = '<section class="panel implementation-view" aria-labelledby="implementation-title"><h3 id="implementation-title">IMPLEMENTATION STATUS <span class="micro">/ ' + implementations.length + '</span></h3><p class="summary">Documents grouped for reading by their declared source identity. Recorded statuses remain unchanged.</p>' + (groupsProblem ? '<div class="error" role="alert">Declared groups are not shown: ' + e(groupsProblem) + '</div>' : '') + groups.filter(([,records]) => records.length).map(([name,records]) => '<section class="implementation-module" aria-label="' + e(name) + '"><h4>' + e(name) + ' <span class="micro">/ ' + records.length + '</span></h4><div class="cards">' + records.map(i => card(i,true)).join('') + '</div></section>').join('') + '</section>';
  const recentDecisions = c.decisions.slice(0,15);
  const projectView = lenses.map(([label,path]) => '<section class="briefing-lens" aria-label="' + label + '"><div class="cards">' + (c.documents.filter(i => i.source.path === path).map(i => card(i,true)).join('') || '<p class="empty">No record in this snapshot.</p>') + '</div></section>').join('');
  return `<p class="eyebrow">RECORDED PROJECT STATUS</p><h2>Status briefing</h2><p class="intro">Project lenses and implementation records, with decision context alongside. Select an item to inspect its content and source identity.</p><div class="notice"><strong>Non-authoritative snapshot briefing.</strong> These are source-authored claims in the loaded datasets, not a live project-status assertion. Verify current Progress, decisions and Git history before drawing normative conclusions. No active transaction state is available or inferred.</div><div class="briefing-layout"><div class="briefing-primary"><section class="panel command-view" aria-labelledby="command-view-title"><h3 id="command-view-title">COMMAND / PROJECT VIEW</h3><p class="summary">Three project-level lenses from the recorded snapshot.</p>${projectView}</section>${implementationView}</div><aside class="panel decisions-rail" aria-labelledby="decisions-rail-title"><h3 id="decisions-rail-title">DECISIONS</h3><p class="summary">Cross-cutting context · newest recorded dates first. Showing ${recentDecisions.length} of ${c.decisions.length} eligible entries; explicitly Superseded entries are omitted here. No relevance relationships are inferred.</p><button class="all-decisions" data-type="decisions">View all Decisions →</button><div class="cards">${recentDecisions.map(i => card(i,true)).join('') || '<p class="empty">No eligible decisions in this snapshot.</p>'}</div></aside></div>`;
}

function browse() {
  const visit = current(); visit.filters = {...blankFilters(), ...visit.filters}; visit.query ??= ''; visit.sort ??= 'recent'; visit.offset ??= 0;
  const applicable = applicableFilters(items,visit.filters,visit.query);
  if (JSON.stringify(applicable) !== JSON.stringify(visit.filters)) {visit.filters=applicable;visit.offset=0;save(true);}
  const scope = filter(items,{type:visit.section ? [visit.section] : visit.filters.type});
  // Section-specific orders: decision ID for all-decision or all-rationale scopes, title A→Z for all-document or all-commit scopes.
  const only = type => scope.length > 0 && scope.every(i => i.type === type);
  // Source status order is offered only when some record in scope carries a source status.
  const decisionsOnly = only('decisions') || only('rationale'), titleSort = only('documents') || only('commits'), hasStatus = scope.some(i => i.status);
  if (!['recent',...(hasStatus ? ['status'] : []),...(decisionsOnly ? ['id'] : []),...(titleSort ? ['title'] : [])].includes(visit.sort)) visit.sort = 'recent';
  const selected = filter(items,visit.filters,visit.query), ordered = sorted(selected,visit.sort,items);
  if (visit.offset >= ordered.length) visit.offset = 0;
  const dimensions = {type:'Sections / type',categories:'Category',topics:'Topic',status:'Status keywords',classes:'Commit class',subclasses:'Commit subclass',worker:'Worker'};
  const controls = Object.entries(dimensions).map(([key,label]) => {
    if (key === 'type' && visit.section) return '';
    const contextual = CONTEXTUAL.includes(key);
    const records = contextual ? facetRecords(items,visit.filters,visit.query,key) : items;
    const available = facets(records,key);
    // Dimensions without any source values (currently Category and Topic) are hidden rather than shown empty.
    if (!available.length) return '';
    const hint = key === 'status' ? '<p>Contains matching in source status, ignoring case. Keywords can overlap.</p>' : '';
    return `<fieldset><legend>${e(label)}</legend>${hint}${available.map(value => `<button data-filter="${key}" data-value="${e(value)}" aria-pressed="${visit.filters[key].includes(value)}">${e(key === 'type' ? LABELS[value] : value)} <span class="micro">(${records.filter(i => key === 'status' ? matchesStatus(i,value) : values(i,key).includes(value)).length})</span></button>`).join('')}</fieldset>`;
  }).join('');
  return `<div class="browse-layout"><aside class="filters"><h2>${visit.section ? e(LABELS[visit.section]) : 'Search the records'}</h2><p>No selection means all. Values within a dimension combine with OR; dimensions and search narrow together.</p>${searchedType(visit) === 'devlog' && devlogOff ? '<p class="summary">This project keeps no devlog.</p>' : ''}<button id="reset">Clear all filters</button>${controls}</aside><section><div class="toolbar"><label for="search" class="micro">SEARCH</label><input type="search" id="search" value="${e(visit.query)}" placeholder="Search titles, content, identities…"><label for="sort" class="micro">SORT</label><select id="sort"><option value="recent" ${visit.sort === 'recent' ? 'selected' : ''}>Recorded date · newest first</option>${decisionsOnly ? `<option value="id" ${visit.sort === 'id' ? 'selected' : ''}>Decision ID · newest first</option>` : ''}${titleSort ? `<option value="title" ${visit.sort === 'title' ? 'selected' : ''}>Title · A → Z</option>` : ''}${hasStatus ? `<option value="status" ${visit.sort === 'status' ? 'selected' : ''}>Source status</option>` : ''}</select></div><div class="result-bar"><p id="result-count" class="micro" role="status">${selected.length} OF ${scope.length} ENTRIES · DATES ARE SOURCE FACTS</p><div class="pagination"><button data-offset="${visit.offset-20}" ${visit.offset === 0 ? 'disabled' : ''}>← Previous</button><span class="micro">${ordered.length ? visit.offset+1 : 0}–${Math.min(visit.offset+20,ordered.length)} / ${ordered.length}</span><button data-offset="${visit.offset+20}" ${visit.offset+20 >= ordered.length ? 'disabled' : ''}>Next →</button></div></div><div class="cards">${ordered.slice(visit.offset,visit.offset+20).map(i => card(i)).join('') || '<p class="empty">No matching records. Clear a filter or change the search.</p>'}</div></section></div>`;
}
function sourceFacts(source) {
  return `<dl>${Object.entries(source).map(([key,value]) => `<dt>${e(key)}</dt><dd>${hashText(value,items,['sha','commit','revision','parents'].includes(key))}</dd>`).join('')}</dl><details><summary>Full source identity</summary><pre class="raw">${e(JSON.stringify(source,null,2))}</pre></details>`;
}
function linkControl(link,index) {
  const target = resolveTarget(link.target,items);
  if (target) return `<button data-link="${index}">${e(target.type === 'rationale' ? displayTitle(target) : (link.label ?? target.title))} →</button>`;
  const url = link.target.url;
  if (url && /^https?:\/\//.test(url)) return `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(link.label ?? url)} ↗</a>`;
  return `<strong>${hashText(link.label ?? link.target.path ?? link.target.commit ?? 'Source target',items,false)}</strong><p class="summary">Traceable source target; matching rendered record is not in this snapshot.</p>`;
}
function inline(text) {
  // Keep code samples, Markdown citations and external URLs inert and exact.
  const tokens = /(`[^`]+`|\[[^\]]*\]\([^)]*\)|https?:\/\/[^\s<>]+)/g;
  const prose = value => hashText(value,items).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
  let out = '', offset = 0;
  for (const match of text.matchAll(tokens)) {
    out += prose(text.slice(offset,match.index));
    const token = match[0];
    if (token.startsWith('`')) {
      const code = token.slice(1,-1);
      const bareHash = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(code);
      const contentIdentity = /\b(?:blob|proposal(?: content)?|content|sha-?256)(?:\s+(?:hash|identity|sha))?[\s:=]*$/i.test(text.slice(0,match.index));
      out += '<code>' + (bareHash ? hashText(code,items,!contentIdentity) : e(code)) + '</code>';
    } else out += e(token);
    offset = match.index + token.length;
  }
  return out + prose(text.slice(offset));
}
function markdown(text) {
  const out = [], lines = text.split('\n'), counts = {}; let code = null, buffer = [], list = false;
  const closeList = () => { if (list) {out.push('</ul>');list=false;} };
  for (let n=0;n<lines.length;n++) {
    const line = lines[n];
    if (/^(`{3,}|~{3,})/.test(line)) {
      closeList(); const fence = line.match(/^(`{3,}|~{3,})/)[0];
      if (!code) {code = fence;buffer=[];} else if (line.startsWith(code)) {out.push(`<pre><code>${e(buffer.join('\n'))}</code></pre>`);code=null;} else buffer.push(line);
      continue;
    }
    if (code) {buffer.push(line);continue;}
    const h = /^(#{1,6}) (.+?)\s*$/.exec(line);
    if (h) {closeList();const id = slug(h[2]), count = counts[id] ?? 0; counts[id] = count+1; out.push(`<h${h[1].length} id="source-${e(id + (count ? '-' + count : ''))}">${inline(h[2])}</h${h[1].length}>`);continue;}
    if (/^\|/.test(line) && /^\|[\s:|\-]+\|\s*$/.test(lines[n+1] ?? '')) {
      closeList();const cells = value => value.replace(/^\||\|\s*$/g,'').split('|');
      out.push('<div class="table-scroll" tabindex="0" role="region" aria-label="Source table"><table><thead><tr>' + cells(line).map(c=>`<th>${inline(c.trim())}</th>`).join('') + '</tr></thead><tbody>');n++;
      while (/^\|/.test(lines[n+1] ?? '')) out.push('<tr>' + cells(lines[++n]).map(c=>`<td>${inline(c.trim())}</td>`).join('') + '</tr>');
      out.push('</tbody></table></div>');continue;
    }
    if (/^[-*] /.test(line)) {if (!list) {out.push('<ul>');list=true;}out.push(`<li>${inline(line.slice(2))}</li>`);continue;}
    closeList();
    if (line.startsWith('> ')) out.push(`<blockquote>${inline(line.slice(2))}</blockquote>`);
    else if (line.trim()) out.push(`<p>${inline(line)}</p>`);
  }
  closeList(); if (code) out.push(`<pre><code>${e(buffer.join('\n'))}</code></pre>`);
  return out.join('');
}
function detail() {
  const visit = current(), i = items.find(i => i.id === visit.id);
  if (!i) return `<div class="error"><h2>Record unavailable</h2><p>This identity is not in the loaded snapshot.</p><code>${e(visit.id)}</code></div>`;
  return `<p class="eyebrow">${e(LABELS[i.type])} / RECORD DETAIL</p><h2>${e(displayTitle(i))}</h2>${badges(i)}${i.type === 'commits' ? commitIdentity(i) : `${sourcePreview(i)}<p class="source-small">${hashText(i.id,items,false)}</p>`}<div class="detail-layout"><article class="panel ${i.type === 'decisions' ? 'decision-content' : ''}"><div class="prose">${i.contentFormat === 'markdown' ? markdown(content(i)) : `<pre>${hashText(content(i),items)}</pre>`}</div>${i.type === 'commits' ? '' : `<details><summary>${i.contentFormat === 'markdown' ? 'Original Markdown from JSON' : 'Original text from JSON'}</summary><pre class="raw">${e(content(i))}</pre></details>`}</article><aside class="panel trace"><h3>Source traceability</h3><p class="summary">Identity as declared in JSON. Proposal source content is not labeled committed; displayed claims do not establish current authority.</p>${sourceFacts(i.source)}${i.sha ? `<h3>Commit metadata</h3>${sourceFacts({sha:i.sha,parents:i.parents.join(', '),author:i.author.name,committer:i.committer.name,recordedCommitterTime:formatTimestamp(recordTimestamp(i),zone)})}` : ''}<h3>Explicit outgoing links / ${i.links.length}</h3><p class="summary">Only source-backed directed links. Source-only targets retain their identities. No reverse relationships are inferred.</p>${i.links.map((link,index) => `<div class="link-row">${linkControl(link,index)}<p class="micro">${e(link.relation)}</p><details><summary>Target identity & supporting evidence</summary><h4>Target</h4>${sourceFacts(link.target)}<h4>Evidence</h4>${sourceFacts(link.evidence)}</details></div>`).join('') || '<p class="summary">No explicit outgoing links.</p>'}</aside></div>`;
}
// ---- Local queue tab and the one local action ---------------------------------------------------
// Every payload value reaches the page through textContent or setAttribute, never as markup.
const el = (tag, className, content) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
};
// The status light in the masthead: read on load and on focus, and on a timer only when the server says so.
let lightToken = 0, lightTimer = null, lightRead = 0;
function drawLight(model) {
  const light = $('#light');
  light.className = `jq-light jq-light-${model.tone}`;
  light.replaceChildren(el('span', 'jq-light-legend', model.legend), el('span', 'jq-light-dot'));
  light.setAttribute('title', model.title);
  light.setAttribute('aria-label', model.legend);
  clearTimeout(lightTimer);
  if (model.pollSeconds > 0) lightTimer = setTimeout(() => { if (document.visibilityState === 'visible') loadLight(); }, model.pollSeconds * 1000);
}
async function loadLight() {
  const token = ++lightToken;
  lightRead = Date.now();
  let model;
  try {
    const response = await fetch('../queue.json', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    model = lightModel((await response.json()).execution);
  } catch (error) { model = lightModel({schema: '1.0', level: 'UNKNOWN', line: `STATUS UNKNOWN: ${error.message}`}); }
  if (token === lightToken) drawLight(model);
}
function refreshLightOnFocus() { if (document.visibilityState === 'visible' && Date.now() - lightRead > 2000) loadLight(); }
function queueView() { return '<p class="eyebrow">LOCAL QUEUE</p><h2>Job queue</h2><div id="queue-view"><p class="loading" role="status">Reading the local queue…</p></div>'; }
let queueToken = 0;
async function loadQueue() {
  const token = ++queueToken;
  const show = nodes => { if (token === queueToken && current().page === 'queue' && $('#queue-view')) $('#queue-view').replaceChildren(...nodes); };
  try {
    const response = await fetch('../queue.json', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body = await response.json();
    if (token === queueToken) { lightToken++; lightRead = Date.now(); drawLight(lightModel(body.execution)); }
    show(queueNodes(queueModel(body), stripModel(body.execution, body)));
  } catch (error) {
    show([el('div', 'error', `Queue unavailable: ${error.message}`)]);
  }
}
function sessionNode(awaiting) {
  const wrap = el('span', 'jq-session');
  wrap.append(el('span', 'jq-chip jq-chip-you', 'waiting for you'), ` for ${awaiting.what} · `);
  const session = awaiting.session;
  if (session.state === 'link') {
    const link = el('a', 'jq-open', 'open session');
    link.setAttribute('href', session.href);
    link.setAttribute('rel', 'noopener noreferrer');
    link.setAttribute('title', `Opens the ${session.vendor} session ${session.id}. It approves nothing.`);
    wrap.append(link);
  } else wrap.append(session.state === 'text' ? `session: ${session.vendor} ${session.id}` : 'session unknown');
  return wrap;
}
function queueTable(rows) {
  const table = el('table', 'jq-table'), thead = el('thead'), head = el('tr');
  for (const [cls, label] of [['jq-pri', 'Pri'], ['jq-kind', 'Kind'], ['jq-handle', 'Handle'], ['jq-desc', 'Description'], ['jq-state', 'State / waiting / dependencies']]) head.append(el('th', cls, label));
  thead.append(head);
  const body = el('tbody');
  for (const row of rows) {
    const tr = el('tr', row.role === 'epic' ? 'jq-epic' : row.role === 'member' ? 'jq-member' : '');
    const state = el('td', 'jq-state');
    for (const chip of row.chips) if (chip.tone !== 'you') state.append(el('span', `jq-chip jq-chip-${chip.tone}`, chip.text));
    if (row.awaiting) state.append(sessionNode(row.awaiting));
    for (const line of row.lines) state.append(el('div', 'jq-line', line));
    tr.append(el('td', 'jq-pri', row.priority), el('td', 'jq-kind', row.workKind), el('td', 'jq-handle', (row.role === 'member' ? '→ ' : '') + row.handle), el('td', 'jq-desc', row.description), state);
    body.append(tr);
  }
  table.append(thead, body);
  return table;
}
// The claims strip: only active claims, one row per running transaction, or one quiet line. Plain text only.
function claimsStrip(strip) {
  if (!strip.rows.length) return el('p', 'jq-line jq-sub-quiet', strip.note);
  const table = el('table', 'jq-table jq-strip'), thead = el('thead'), head = el('tr'), body = el('tbody');
  for (const label of ['Owner', 'Level', 'Subclass', 'Since', 'Flags / overlap']) head.append(el('th', '', label));
  thead.append(head);
  for (const row of strip.rows) {
    const owner = el('td', 'jq-handle', row.owner);
    if (row.jobHandle) owner.append(el('div', 'jq-line', row.jobHandle));
    const flags = el('td');
    for (const flag of row.flags) flags.append(el('span', `jq-chip ${flag.startsWith('conflict') ? 'jq-chip-blocked' : 'jq-chip-wait'}`, flag));
    for (const line of row.detail) flags.append(el('div', 'jq-line', line));
    const level = el('td');
    level.append(el('span', `jq-chip jq-chip-level-${row.tone}`, row.level));
    const tr = el('tr');
    tr.append(owner, level, el('td', '', row.subclasses || '-'), el('td', '', row.since), flags);
    body.append(tr);
  }
  table.append(thead, body);
  return table;
}
function queueNodes(model, strip) {
  const read = Date.parse(model.readAt);
  const time = Number.isFinite(read) ? formatTimestamp({ms: read, precision: 'second'}, zone) : 'an unknown time';
  // Top of the Queue tab: notices that must come first, such as stop lines, are added to this list.
  const nodes = [el('p', 'notice', `Local, non-authoritative, read at ${time}. Press F5 to read it again. The repository and the committed records win on any conflict; pending notes appear as counts only.`)];
  if (strip) nodes.push(el('p', 'jq-sub', 'Active claims'), claimsStrip(strip));
  if (model.state !== 'ok') nodes.push(el('div', model.state === 'absent' ? 'notice' : 'error', model.message || 'The local queue could not be read.'));
  else {
    nodes.push(model.queue.length ? queueTable(model.queue) : el('p', 'empty', 'The queue is empty.'));
    if (model.unscheduled.length) nodes.push(el('p', 'jq-sub', 'Unscheduled'), queueTable(model.unscheduled));
  }
  return nodes;
}
function render() {
  header(); $('#view').innerHTML = ({home, browse, briefing:briefingView, queue:queueView, detail}[current().page] ?? home)();
  if (current().page === 'queue') loadQueue();
  document.title = `${viewLabel(current())} · Mission Control`;
  if (current().fragment) requestAnimationFrame(() => {
    const target = document.getElementById('source-' + current().fragment);
    if (target) target.scrollIntoView();
    else { const note = document.createElement('p'); note.className='notice';note.textContent=`Source fragment: ${current().fragment}. An exact displayed heading is unavailable; target content and traceability are retained.`; $('#view').prepend(note); }
  });
}
function updateBrowse() {save(true); const focus = document.activeElement?.id, start = $('#search')?.selectionStart; render(); if (focus === 'search') {$('#search').focus(); $('#search').setSelectionRange(start,start);} }
document.addEventListener('click',event => {
  if (event.target.closest('.skip')) {event.preventDefault();$('#view').focus();return;}
  const record = event.target.closest('a[data-record]');
  if (record && items.length && items.some(i => i.id === record.dataset.record)) {event.preventDefault();navigate({page:'detail',id:record.dataset.record});return;}
  const button = event.target.closest('button'); if (!button || button.disabled || !items.length) return;
  if (button.dataset.page) navigate({page:button.dataset.page});
  else if (button.dataset.type) navigate({page:'browse',filters:{...blankFilters(),type:[button.dataset.type]}});
  else if (button.dataset.item) navigate({page:'detail',id:button.dataset.item});
  else if (button.dataset.crumb !== undefined) {const delta = Number(button.dataset.crumb)-cursor;if (delta) history.go(delta);}
  else if (button.dataset.filter) {
    const v=current(), key=button.dataset.filter, value=button.dataset.value;
    v.filters[key] = v.filters[key].includes(value) ? v.filters[key].filter(x=>x!==value) : [...v.filters[key],value];v.offset=0;updateBrowse();
  } else if (button.id === 'reset') {Object.assign(current(),{filters:{...blankFilters(),type:current().section ? [current().section] : []},query:'',offset:0});updateBrowse();}
  else if (button.dataset.offset) {current().offset=Number(button.dataset.offset);updateBrowse();window.scrollTo(0,0);}
  else if (button.dataset.link !== undefined) {
    const item = items.find(i=>i.id===current().id), link=item.links[Number(button.dataset.link)], target=resolveTarget(link.target,items);
    if (target) navigate({page:'detail',id:target.id,fragment:link.target.fragment});
  }
});
document.addEventListener('input',event => {if (event.target.id === 'search') {current().query=event.target.value;current().offset=0;updateBrowse();}});
document.addEventListener('change',event => {if (event.target.id === 'sort') {current().sort=event.target.value;current().offset=0;updateBrowse();}});
// The one local action: a POST the server accepts only from this page, with no parameters.
let refreshing = false;
try {
  const last = sessionStorage.getItem('mc-refresh-result');
  if (last) {$('#refresh-status').textContent = last; sessionStorage.removeItem('mc-refresh-result');}
} catch {}
$('#refresh').addEventListener('click', async () => {
  if (refreshing) return;
  refreshing = true;
  const button = $('#refresh'), status = $('#refresh-status');
  button.disabled = true;
  status.textContent = 'Updating the local datasets from what changed since the last refresh…';
  try {
    const response = await fetch('../refresh', {method: 'POST', cache: 'no-store'});
    const result = await response.json().catch(() => ({}));
    if (!response.ok || result.state !== 'ok') throw new Error(result.message ?? `HTTP ${response.status}`);
    // The message names the kind that ran (incremental, full rebuild or already current) and its time; the reload
    // would erase it, so the next load shows it once.
    status.textContent = `${result.message} (${result.seconds}s in all)`;
    try {sessionStorage.setItem('mc-refresh-result', status.textContent);} catch {}
    setTimeout(() => location.reload(), 900);
  } catch (error) {
    status.textContent = `Refresh failed: ${error.message}`;
    refreshing = false; button.disabled = false;
  }
});
window.addEventListener('popstate',event => {
  if (event.state?.missionControl === 1) {
    // Existing forward visits are retained while the visible breadcrumb ends at the cursor.
    const state=event.state;
    trail = trail.length >= state.trail.length && state.trail.every((visit,index) => sameDestination(visit,trail[index]))
      ? [...state.trail, ...trail.slice(state.trail.length)] : state.trail;
    cursor=state.cursor;render();
  } else initializeHistory();
});
function initializeHistory() {
  const [requestedPage,id,fragment] = location.hash.slice(1).split('/');
  const page = requestedPage === 'coordinates' ? 'briefing' : requestedPage;
  let visit={page:['home','browse','briefing','queue','detail'].includes(page) ? page : 'queue'};
  try {if (id) visit.id=decodeURIComponent(id);if(fragment) visit.fragment=decodeURIComponent(fragment);} catch {visit={page:'queue'};}
  trail=[visit];cursor=0;save(true);render();
}
window.addEventListener('hashchange',() => {if (urlFor(current()) !== location.hash) initializeHistory();});
window.addEventListener('focus', refreshLightOnFocus);
document.addEventListener('visibilitychange', refreshLightOnFocus);
loadLight();
try {
  try {
    const response=await fetch('../groups.json', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const body=await response.json();
    if (body.state !== 'ok') throw new Error(body.message ?? 'the declaration was refused');
    declaredGroups=body.groups;
  } catch(error) { groupsProblem=error.message; }
  const sources=[];
  const loaded=await Promise.all(TYPES.map(async type => {
    const response=await fetch(`../data/${type}.json`,{cache:'no-store'});
    if (!response.ok) throw new Error(`Could not load ${type}: HTTP ${response.status}`);
    sources.push(response.headers.get('X-MC-Data-Source') ?? '');
    const dataset=await response.json();
    if (type === 'devlog' && dataset.enabled === false) devlogOff=true;
    return validateDataset(dataset,type);
  }));
  if (new Set(sources).size !== 1) throw new Error('The datasets changed while loading. Reload the page.');
  items=loaded.flat();if (new Set(items.map(i=>i.id)).size !== items.length) throw new Error('Duplicate record identity across datasets');
  $('#load-status').textContent=`${items.length} RECORDS / 5 DATASETS · ${dataSourceLabel(sources[0])}${devlogOff ? ' · NO DEVLOG' : ''}`;
  $('#legend').innerHTML=`Times shown in ${e(zone ?? 'browser local time')} · <span class="derived">${DERIVED_MARK}</span> timestamp derived from source commit · <span class="inferred">${INFERRED_MARK}</span> inferred from historical evidence`;
  if (history.state?.missionControl === 1) {trail=history.state.trail;cursor=history.state.cursor;render();} else initializeHistory();
} catch(error) {
  $('#view').innerHTML=`<div class="error" role="alert"><h2>Datasets unavailable</h2><p>${e(error.message)}</p><p>The datasets are generated locally and never committed. Press Refresh data in the header to generate them at this checkout's HEAD (about a minute); otherwise start the local server described in the README. No partial copy is displayed.</p></div>`;
  $('#load-status').textContent='LOAD FAILED';
}
