export const TYPES = ['decisions', 'commits', 'devlog', 'rationale', 'documents'];
export const LABELS = {decisions: 'Decisions', commits: 'Commits', 'devlog': "Devlog", rationale: 'Rationale', documents: 'Documents'};
export const content = item => item.contentMarkdown ?? item.contentText ?? '';
export const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
export function validateDataset(dataset, type) {
  if (dataset.schemaVersion !== 1 || dataset.type !== type || dataset.authority !== 'derived/non-authoritative' || !Array.isArray(dataset.items)) throw new Error(`Invalid dataset envelope: ${type}`);
  const ids = new Set();
  for (const item of dataset.items) {
    if (!item.id || ids.has(item.id) || item.type !== type || !item.source || !Array.isArray(item.links) || typeof content(item) !== 'string') throw new Error(`Invalid record: ${type}`);
    if (type === 'commits' && (!/^[a-f0-9]{40}$/.test(item.sha) || item.id !== `commit:${item.sha}`)) throw new Error('Commit identity must be a full SHA');
    ids.add(item.id);
  }
  return dataset.items;
}
export function values(item, dimension) {
  if (dimension === 'type') return [item.type];
  if (dimension === 'status') return item.status ? [item.status] : [];
  if (dimension === 'worker') return item.worker ? [item.worker] : [];
  return item[dimension] ?? [];
}
export const STATUS_KEYWORDS = ['FROZEN', 'PASS', 'Accepted', 'Approved', 'Authorized', 'Prepared', 'Implemented', 'Planned', 'WIP', 'Explanatory', 'Historical', 'Superseded'];
export function matchesStatus(item, keyword) {
  return Boolean(item.status) && item.status.toLowerCase().includes(keyword.toLowerCase());
}
export function facets(items, dimension) {
  if (dimension === 'status') return STATUS_KEYWORDS.filter(keyword => items.some(item => matchesStatus(item, keyword)));
  return [...new Set(items.flatMap(i => values(i, dimension)))].sort(compare);
}
export function filter(items, selections = {}, query = '') {
  const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(i => Object.entries(selections).every(([key, selected]) => !selected.length || selected.some(v => key === 'status' ? matchesStatus(i, v) : values(i, key).includes(v))) && terms.every(term => `${i.id} ${i.title} ${i.summary} ${content(i)} ${JSON.stringify(i.source)} ${i.keywords?.join(' ') ?? ''}`.toLowerCase().includes(term)));
}
export function facetRecords(items, selections, query, dimension) {
  return filter(items, {...selections, [dimension]:[]}, query);
}
// Contextual dimensions show only values present in the current scope; commit metadata uses projected values as-is.
export const CONTEXTUAL = ['status','classes','subclasses','worker'];
export function applicableFilters(items, selections, query = '') {
  const next = {...selections, ...Object.fromEntries(CONTEXTUAL.map(dimension => [dimension,[...(selections[dimension] ?? [])]]))};
  const dimensions = CONTEXTUAL;
  // Clear values outside the broader section/search scope first, so one stale
  // dimension cannot unnecessarily erase a valid selection in another.
  const scope = filter(items, {...next,...Object.fromEntries(CONTEXTUAL.map(dimension => [dimension,[]]))}, query);
  for (const dimension of dimensions) {
    const available = facets(scope,dimension);
    next[dimension] = next[dimension].filter(value => available.includes(value));
  }
  let changed;
  do {
    changed = false;
    const updates = dimensions.map(dimension => {
      const available = facets(facetRecords(items,next,query,dimension),dimension);
      return [dimension,next[dimension].filter(value => available.includes(value))];
    });
    for (const [dimension, selected] of updates) {
      if (selected.length !== next[dimension].length) changed = true;
      next[dimension] = selected;
    }
  } while (changed);
  return next;
}
// Sorting uses the same timestamp the UI displays, including commit-derived values; untimed records sort last.
export function recordedTime(i, context = []) {
  const t = recordTimestamp(i, context);
  if (!t) return null;
  return (t.precision === 'date' ? Date.parse(`${t.date}T00:00:00Z`) : t.ms) / 1000;
}
// Decision ID order: a decision by its own ID; a rationale entry by the newest decision it explains.
export function decisionKey(i) {
  if (i.type === 'decisions') return i.decisionId ?? '';
  return [...(i.decisionIds ?? [])].sort(compare).at(-1) ?? '';
}
// Newest first and decision ID order both descend, including their full-identity tie-breaks.
export function sorted(items, mode = 'title', context = items) {
  return [...items].sort((a,b) => {
    if (mode === 'recent') return (recordedTime(b,context) ?? -Infinity) - (recordedTime(a,context) ?? -Infinity) || compare(b.id,a.id);
    if (mode === 'id') return compare(decisionKey(b),decisionKey(a)) || compare(b.id,a.id);
    if (mode === 'status') return compare(a.status ?? '', b.status ?? '') || compare(a.id,b.id);
    return compare(a.title.toLowerCase(),b.title.toLowerCase()) || compare(a.id,b.id);
  });
}
export const slug = title => title.toLowerCase().replace(/[^\p{L}\p{N}_\- ]/gu,'').replaceAll(' ','-');
export function sameSource(a, b) {
  return ['path','selection','revision','blob','contentSha256'].every(k => a[k] === b[k]);
}
// Only explicit targets with identical source identities may resolve into a record.
export function resolveTarget(target, items) {
  if (target.recordId) {
    const record = items.find(i => i.id === target.recordId);
    return record && sameSource(record.source,target) &&
      ['fragment','lineStart'].every(key => target[key] === undefined || record.source[key] === target[key]) ? record : null;
  }
  if (target.commit) return items.find(i => i.type === 'commits' && i.sha === target.commit) ?? null;
  if (!target.path) return null;
  const candidates = items.filter(i => sameSource(i.source,target));
  if (target.fragment) {
    const sections = candidates.filter(i => i.source.fragment === target.fragment);
    if (sections.length) return sections.length === 1 ? sections[0] : null;
    const decision = candidates.find(i => i.type === 'decisions' && content(i).split('\n').some(line => /^#{1,6} /.test(line) && slug(line.replace(/^#+ /,'')) === target.fragment));
    if (decision) return decision;
  }
  const documents = candidates.filter(i => i.type !== 'decisions' && !i.source.fragment);
  return documents.length === 1 ? documents[0] : null;
}
export function sameDestination(a, b) {
  if (a.page !== b.page) return false;
  if (a.page === 'detail') return a.id === b.id && (a.fragment ?? '') === (b.fragment ?? '');
  if (a.page === 'browse') {
    if (a.section !== b.section) return false;
    const sections = visit => [...(visit.filters?.type ?? [])].sort(compare);
    return JSON.stringify(sections(a)) === JSON.stringify(sections(b));
  }
  return true;
}
export function appendVisit(trail, cursor, visit) {
  return sameDestination(trail[cursor], visit) ? trail : [...trail.slice(0,cursor + 1), visit];
}
export function briefing(items) {
  return {
    decisions: sorted(items.filter(i => i.type === 'decisions' && i.status && !/^superseded/i.test(i.status)), 'recent'),
    documents: sorted(items.filter(i => i.type === 'documents' && (i.status || ['docs/PROGRESS.md','docs/PLAN.md','docs/ARCHITECTURE.md'].includes(i.source.path))))
  };
}

// Presentation-only groups of the implementation records: each declared group takes the records whose exact source path it lists,
// in declared order; a path claimed by an earlier group stays there, and every other record falls into the unmapped fallback.
export const FALLBACK_GROUP = 'Other implementation';
export function groupImplementations(records, declared) {
  const claimed = new Set();
  const groups = declared.map(({label, paths}) => {
    const mine = records.filter(i => paths.includes(i.source.path) && !claimed.has(i.source.path));
    mine.forEach(i => claimed.add(i.source.path));
    return [label, mine];
  });
  return [...groups, [FALLBACK_GROUP, records.filter(i => !claimed.has(i.source.path))]];
}

// Commit metadata reflects the projection only: absent values stay unknown or omitted, and inferred
// markers come from the record's inferredFields, never from browser-side inference.
export function commitMetadata(i) {
  const inferred = new Set(i.inferredFields ?? []);
  const fields = [
    {label:'Class', key:'classes', values:i.classes ?? [], optional:false},
    {label:'Subclass', key:'subclasses', values:i.subclasses ?? [], optional:true},
    {label:'Worker', key:'worker', values:i.worker ? [i.worker] : [], optional:false}
  ];
  return fields.filter(f => f.values.length || !f.optional).map(f => ({
    label: f.label,
    value: f.values.length ? f.values.join(', ') : 'unknown',
    inferred: f.values.length > 0 && inferred.has(f.key)
  }));
}
export const INFERRED_MARK = '†', DERIVED_MARK = '*';
export function commitMetadataHtml(i) {
  return commitMetadata(i).map(f => `${f.label}: ${escape(f.value)}${f.inferred ? `<span class="inferred" title="Inferred from historical evidence">${INFERRED_MARK}</span>` : ''}`).join(' · ');
}

// Timestamps carry exactly the precision of their source (date, minute or second; never sub-second).
// Explicit source times win; otherwise a committed record falls back to its source revision's committer
// time and is marked derived. Canonical data is unchanged; only presentation is browser-local.
export function recordTimestamp(i, items = []) {
  if (i.type === 'commits' && Number.isFinite(i.committer?.epochSeconds)) return {ms:i.committer.epochSeconds * 1000, precision:'second', derived:false};
  const date = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:?\d{2}))?(?!\d)/.exec(i.date ?? '');
  if (date && date[2]) {
    const ms = Date.parse(`${date[1]}T${date[2]}:${date[3]}:${date[4] ?? '00'}${date[5] === 'Z' ? 'Z' : date[5].replace(/^([+-]\d{2}):?(\d{2})$/,'$1:$2')}`);
    if (Number.isFinite(ms)) return {ms, precision:date[4] ? 'second' : 'minute', derived:false};
  }
  if (date) return {date:date[1], precision:'date', derived:false};
  const commit = i.source?.revision ? items.find(c => c.type === 'commits' && c.sha === i.source.revision) : null;
  if (Number.isFinite(commit?.committer?.epochSeconds)) return {ms:commit.committer.epochSeconds * 1000, precision:'second', derived:true};
  return null;
}
export function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
}
export function formatTimestamp(t, timeZone) {
  if (t.precision === 'date') return t.date;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {timeZone, year:'numeric', month:'2-digit', day:'2-digit',
    hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'}).formatToParts(new Date(t.ms)).map(p => [p.type, p.value]));
  const text = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
  return t.precision === 'second' ? `${text}:${parts.second}` : text;
}
export function timestampHtml(t, timeZone) {
  const iso = t.precision === 'date' ? t.date : new Date(t.ms).toISOString().slice(0, t.precision === 'second' ? 19 : 16) + 'Z';
  return `<time datetime="${iso}">${escape(formatTimestamp(t, timeZone))}</time>${t.derived ? `<span class="derived" title="Timestamp derived from source commit">${DERIVED_MARK}</span>` : ''}`;
}
// One provenance line: timestamp · repo-relative path · short source hash, omitting absent parts.
export function provenanceHtml(i, items, timeZone) {
  const t = recordTimestamp(i, items), s = i.source ?? {};
  const parts = [];
  if (t) parts.push(timestampHtml(t, timeZone));
  if (s.path) parts.push(escape(s.path) + (s.fragment ? '#' + escape(s.fragment) : ''));
  if (s.revision ?? s.commit) parts.push(hashText(s.revision ?? s.commit, items));
  return parts.join(' · ');
}
// Log entries show their own title once, followed by the date their Date line records.
export function logTitle(i) {
  return `${i.title}${i.date ? ` · ${i.date}` : ''}`;
}

// Abbreviations are display only; navigation always binds an exact loaded SHA.
export function hashText(text, items, linkCommits = true) {
  const value = String(text ?? '');
  const urls = [...value.matchAll(/https?:\/\/[^\s<>]+/g)];
  const hashes = /(?<![A-Za-z0-9])(?:[a-f0-9]{64}|[a-f0-9]{40})(?![A-Za-z0-9])/g;
  let html = '', offset = 0;
  for (const match of value.matchAll(hashes)) {
    const sha = match[0], index = match.index;
    html += escape(value.slice(offset,index));
    if (urls.some(url => index >= url.index && index < url.index + url[0].length)) {
      html += escape(sha); offset = index + sha.length; continue;
    }
    const contentIdentity = /\b(?:blob|proposal(?: content)?|content|sha-?256)(?:\s+(?:hash|identity|sha))?[\s:=`]*$/i.test(value.slice(0,index));
    const target = linkCommits && !contentIdentity && sha.length === 40
      ? items.find(i => i.type === 'commits' && i.sha === sha) : null;
    const firstLine = target ? (content(target).split('\n')[0] || target.title) : '';
    html += target
      ? `<button class="hash-link" data-item="${escape(target.id)}" title="${escape(firstLine)}&#10;${sha}" aria-label="Open commit ${escape(firstLine)} · ${sha}">${sha.slice(0,12)}</button>`
      : `<span class="hash-value" title="${sha}" aria-label="Full hash ${sha}">${sha.slice(0,12)}</span>`;
    offset = index + sha.length;
  }
  return html + escape(value.slice(offset));
}

// ---- Local queue view -------------------------------------------------------------------------
// The queue payload is local and non-authoritative. Everything below turns it into plain row models;
// app.mjs renders them with textContent, so no payload value is ever parsed as HTML.
export const SESSION_LINKS = new Map([
  ['claude', id => `claude://claude.ai/epitaxy/${id}`],
  ['codex', id => `codex://threads/${id}`]
]);
const SESSION_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SESSION_TOKEN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const VENDOR_TOKEN = /^[a-z][a-z0-9_-]{0,31}$/;
// A link only for an allow-listed vendor with a UUID id; any other well-formed session is shown as text;
// anything else is unknown. Nothing is invented and no other href is ever built.
export function sessionLink(session) {
  if (!session || typeof session !== 'object') return {state: 'unknown'};
  const {vendor, id} = session;
  if (typeof vendor !== 'string' || typeof id !== 'string' || !VENDOR_TOKEN.test(vendor)) return {state: 'unknown'};
  if (SESSION_LINKS.has(vendor)) return SESSION_UUID.test(id) ? {state: 'link', vendor, id, href: SESSION_LINKS.get(vendor)(id)} : {state: 'unknown'};
  return SESSION_TOKEN.test(id) ? {state: 'text', vendor, id} : {state: 'unknown'};
}
const AWAITING_FOR = ['preview', 'approval'];
const STATUS_TONES = {BLOCKED: 'blocked', AWAITING_RESPONSE: 'wait', WIP: 'active', COMPLETE: 'done', CANCELLED: 'done'};
const text = value => typeof value === 'string' ? value : '';
const list = value => Array.isArray(value) && value.every(item => typeof item === 'string') ? value : [];
function jobRow(job, role, priority) {
  if (!job || typeof job.handle !== 'string' || !job.handle) throw new Error('Queue entry without a handle');
  const chips = [], lines = [];
  chips.push(job.has_record === false || job.status == null ? {text: 'no record', tone: 'missing'} : {text: text(job.status), tone: STATUS_TONES[job.status] ?? 'plain'});
  const waiting = job.awaiting_lead;
  let awaiting = null;
  if (waiting && typeof waiting === 'object') {
    const what = AWAITING_FOR.includes(waiting.for) ? waiting.for : 'unknown';
    awaiting = {what, session: sessionLink(waiting.session)};
    chips.push({text: 'waiting for you', tone: 'you'});
  }
  if (text(job.waiting)) lines.push(text(job.waiting));
  const unmet = list(job.unsatisfied_dependencies), missing = new Set(list(job.missing_dependencies));
  if (unmet.length) lines.push('waiting on: ' + unmet.map(name => missing.has(name) ? `${name} (no record)` : name).join(', '));
  if (['WIP', 'AWAITING_RESPONSE', 'BLOCKED'].includes(job.status) && text(job.activity)) lines.push(`reported activity: ${job.activity}`);
  if (Number.isInteger(job.pending_count) && job.pending_count > 0) lines.push(`${job.pending_count} pending ${job.pending_count === 1 ? 'note' : 'notes'}`);
  return {role, priority: role === 'member' ? '' : priority, workKind: text(job.work_kind), handle: job.handle,
          description: text(job.description), chips, lines, awaiting};
}
const priorityText = value => Number.isInteger(value) ? String(value) : 'unset';
export function epicRow(entry) {
  const counts = new Map();
  for (const job of entry.jobs) { const key = job?.status ?? 'no record'; counts.set(key, (counts.get(key) ?? 0) + 1); }
  return {role: 'epic', priority: priorityText(entry.priority), workKind: '', handle: `${entry.handle} (Epic)`,
          description: `${entry.jobs.length} ${entry.jobs.length === 1 ? 'Job' : 'Jobs'}`, chips: [],
          lines: [entry.jobs.length ? [...counts].map(([status, count]) => `${count} ${status}`).join(' · ')
                                   : `empty; awaiting ${entry.closure_policy === 'explicit' ? 'explicit closure' : 'automatic pruning'}`,
                  `closure: ${entry.closure_policy ?? 'automatic'}`], awaiting: null};
}
// The footer says which copy of the datasets is on screen; the server names it in X-MC-Data-Source.
export function dataSourceLabel(header) {
  const match = /^local; revision=([0-9a-f]{40}); generated=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z); generator=([0-9a-f]{12}); head=(current|older)$/.exec(header ?? '');
  return match ? `LOCAL COPY GENERATED ${match[2].replace('T', ' ').replace('Z', ' UTC')} FROM ${match[1].slice(0, 12)} · ${match[4] === 'current' ? 'CURRENT WITH HEAD' : 'OLDER THAN HEAD, PRESS REFRESH'} · GENERATOR ${match[3]}` : 'SOURCE UNKNOWN';
}
// The status light. The execution payload is versioned MAJOR.MINOR: any minor of major 1 is read, anything else is unknown.
const LIGHT_TONES = {CLEAR: 'clear', OVERLAP: 'overlap', FROZEN: 'frozen', UNKNOWN: 'unknown'};
const lightTime = since => text(since).replace('T', ' ').replace('Z', ' UTC');
export function lightModel(execution) {
  const unknown = legend => ({level: 'UNKNOWN', tone: 'unknown', legend, title: legend, pollSeconds: 0});
  if (!execution || typeof execution !== 'object') return unknown('STATUS UNKNOWN');
  if (text(execution.schema).split('.')[0] !== '1') return unknown('STATUS UNKNOWN: payload version not understood');
  if (!Object.hasOwn(LIGHT_TONES, execution.level)) return unknown('STATUS UNKNOWN');
  const windows = Array.isArray(execution.windows) ? execution.windows.filter(row => row && typeof row === 'object') : [];
  const first = windows[0];
  const more = windows.length > 1 ? ` (+${windows.length - 1} more)` : '';
  const legend = {
    CLEAR: 'CLEAR',
    UNKNOWN: text(execution.line) || 'STATUS UNKNOWN',
    OVERLAP: `OVERLAP: ${text(execution.line).replace(/^OVERLAP:\s*/, '') || 'another owner is on the same ground'}`,
    FROZEN: `REFS FROZEN: ${text(first?.owner) || 'lifecycle operation'}, ${lightTime(first?.since)}${more}`}[execution.level];
  const lines = windows.map(row => [text(row.owner) || 'lifecycle operation', `${text(row.scope)} ${text(row.state)}`.trim(),
    `since ${lightTime(row.since)}`, ...(Array.isArray(row.flags) ? row.flags.map(text).filter(Boolean) : [])].join(' · '));
  for (const overlap of Array.isArray(execution.overlaps) ? execution.overlaps : []) {
    if (overlap && Array.isArray(overlap.owners) && text(overlap.text)) lines.push(`overlap: ${overlap.owners.map(text).join(' with ')}, ${text(overlap.text)}`);
  }
  if (Number.isInteger(execution.stale_claims) && execution.stale_claims > 0) lines.push(`${execution.stale_claims} stale ${execution.stale_claims === 1 ? 'claim' : 'claims'}: the lead developer runs clear`);
  if (text(execution.database_state) && !['ok', 'missing'].includes(execution.database_state)) lines.push(`claims database: ${text(execution.database_state)}; derived evidence only`);
  if (Number.isInteger(execution.declared_without_evidence) && execution.declared_without_evidence > 0) lines.push(`${execution.declared_without_evidence} declared without evidence`);
  lines.push(`read ${lightTime(execution.read_at)}`);
  return {level: execution.level, tone: LIGHT_TONES[execution.level], legend, title: lines.join('\n'),
          pollSeconds: Number.isInteger(execution.poll_seconds) && execution.poll_seconds > 0 ? execution.poll_seconds : 0};
}
// The claims strip at the top of the Queue tab: one row per active owner, so usually none to three. It joins the claim to the
// queue on the Job handle (a handle inside an Epic is `epic--job`) and flags a Job the record calls finished while its
// transaction still exists. The status of the transaction wins; the flag only asks the lead developer to look.
const FINISHED = ['COMPLETE', 'CANCELLED'];
export function stripModel(execution, queuePayload) {
  const none = note => ({rows: [], note});
  if (!execution || typeof execution !== 'object') return none('Claims unavailable');
  if (text(execution.schema).split('.')[0] !== '1' || !Object.hasOwn(LIGHT_TONES, execution.level)) return none('Claims unavailable');
  if (execution.level === 'UNKNOWN') return none('Claims unavailable: the status could not be read');
  const jobs = new Map();
  if (queuePayload && Array.isArray(queuePayload.queue)) {
    for (const entry of [...queuePayload.queue, ...(Array.isArray(queuePayload.unqueued) ? queuePayload.unqueued : [])]) {
      for (const job of entry?.kind === 'epic' && Array.isArray(entry.jobs) ? entry.jobs : [entry]) if (job && typeof job.handle === 'string') jobs.set(job.handle, job);
    }
  }
  const overlaps = Array.isArray(execution.overlaps) ? execution.overlaps.filter(o => o && Array.isArray(o.owners) && text(o.text)) : [];
  const rows = [];
  for (const claim of Array.isArray(execution.claims) ? execution.claims : []) {
    if (!claim || typeof claim !== 'object' || !text(claim.owner)) continue;
    const level = Object.hasOwn(LIGHT_TONES, claim.level) ? claim.level : 'UNKNOWN';
    const flags = Array.isArray(claim.flags) ? claim.flags.map(text).filter(Boolean) : [];
    const job = jobs.get(text(claim.job_handle).split('--').pop());
    if (job && FINISHED.includes(job.status)) flags.push(`conflict: Job ${job.status}, transaction still exists`);
    const detail = overlaps.filter(o => o.owners.includes(claim.owner)).map(o => `with ${o.owners.filter(x => x !== claim.owner).map(text).join(', ')}: ${text(o.text)}`);
    rows.push({owner: text(claim.owner), level, tone: LIGHT_TONES[level], jobHandle: text(claim.job_handle),
               subclasses: Array.isArray(claim.subclasses) ? claim.subclasses.map(text).filter(Boolean).join(', ') : '',
               files: Number.isInteger(claim.files) ? claim.files : 0, since: lightTime(claim.since), flags, detail});
  }
  return rows.length ? {rows, note: ''} : none('No active claims');
}
// Validates the envelope and returns {state, message, readAt, queue:[row], unscheduled:[row]}.
export function queueModel(payload) {
  if (!payload || payload.schema !== 1 || payload.authority !== 'local/non-authoritative' || !['ok', 'absent', 'error'].includes(payload.state)
      || !Array.isArray(payload.queue) || !Array.isArray(payload.unqueued)) throw new Error('Invalid queue payload');
  const queue = [];
  for (const entry of payload.queue) {
    if (entry?.kind === 'epic') {
      if (typeof entry.handle !== 'string' || !Array.isArray(entry.jobs)) throw new Error('Invalid Epic entry');
      queue.push(epicRow(entry), ...entry.jobs.map(job => jobRow(job, 'member', '')));
    } else queue.push(jobRow(entry, 'job', priorityText(entry?.priority)));
  }
  return {state: payload.state, message: text(payload.message), readAt: text(payload.read_at), queue,
          unscheduled: payload.unqueued.map(job => jobRow(job, 'job', 'unset'))};
}
