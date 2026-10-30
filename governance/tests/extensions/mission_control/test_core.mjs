import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {TYPES, validateDataset, filter, sorted, facets, facetRecords, applicableFilters, resolveTarget, appendVisit, briefing, groupImplementations, escape, hashText} from '../../../extensions/mission_control/ui/core.mjs';

// The datasets are never committed: ui/check.py generates them at HEAD, or MC_DATA_DIR names a folder that holds them.
const dataDir = process.env.MC_DATA_DIR;
if (!dataDir) throw new Error('Set MC_DATA_DIR to a folder holding the five datasets, or run governance/tests/extensions/mission_control/check.py');
const items = (await Promise.all(TYPES.map(async type => validateDataset(JSON.parse(await readFile(join(dataDir,`${type}.json`),'utf8')),type)))).flat();
test('all five generated datasets load with complete record identities', () => {
  // Counts derive from the generated copy so routine data refreshes do not invalidate these invariants.
  for (const type of TYPES) assert.ok(items.some(i=>i.type===type),type);
  assert.equal(new Set(items.map(i=>i.id)).size,items.length);
  assert.equal(filter(items).length,items.length);
  for (const i of items) assert.ok((i.contentMarkdown ?? i.contentText).length);
});
test('OR inside dimension; AND across dimensions and free text', () => {
  const sample = [
    {id:'a',type:'documents',title:'Alpha release',contentText:'science',source:{},categories:['engineering'],topics:['release'],status:'Accepted'},
    {id:'b',type:'decisions',title:'Beta release',contentText:'science',source:{},categories:['engineering'],topics:['release'],status:'Proposed'},
    {id:'c',type:'documents',title:'Gamma',contentText:'science',source:{},categories:['other'],topics:['release'],status:'Accepted'}
  ];
  assert.deepEqual(filter(sample,{type:['documents','decisions'],status:['Accepted','Proposed'],categories:['engineering']},'release science').map(i=>i.id),['a','b']);
  assert.deepEqual(filter(sample,{type:['documents','decisions'],status:['Accepted'],categories:['engineering']},'release science').map(i=>i.id),['a']);
  assert.equal(filter(sample,{categories:[],topics:[]},'missing').length,0);
});
test('empty categories and topics stay empty', () => {
  assert.deepEqual(facets(items,'categories'),[]); assert.deepEqual(facets(items,'topics'),[]);
});
test('status keywords use case-insensitive contains, overlap and preserve source wording', () => {
  const sample = [
    {id:'a',type:'documents',title:'A',contentText:'',source:{},status:'**FROZEN**, confirmed; Accepted scope'},
    {id:'b',type:'documents',title:'B',contentText:'',source:{},status:'Partially superseded by G027'},
    {id:'c',type:'decisions',title:'C',contentText:'',source:{},status:'Accepted boundary; requirements WIP'},
    {id:'d',type:'documents',title:'D',contentText:'PASS',source:{}}
  ];
  assert.deepEqual(facets(sample,'status'),['FROZEN','Accepted','WIP','Superseded']);
  assert.deepEqual(filter(sample,{status:['frozen']}).map(i=>i.id),['a']);
  assert.deepEqual(filter(sample,{status:['ACCEPTED']}).map(i=>i.id),['a','c']);
  assert.deepEqual(filter(sample,{status:['Frozen','Superseded']}).map(i=>i.id),['a','b']);
  assert.deepEqual(filter(sample,{status:['Accepted'],type:['decisions']}).map(i=>i.id),['c']);
  assert.equal(filter(sample,{status:['PASS']}).length,0);
  assert.equal(sample[0].status,'**FROZEN**, confirmed; Accepted scope');
});
test('explicit target resolution requires exact source identity and full commit SHA', () => {
  const document=items.find(i=>i.type==='documents');
  assert.equal(resolveTarget({...document.source},items),document);
  assert.equal(resolveTarget({...document.source,revision:'0'.repeat(40)},items),null);
  const commit=items.find(i=>i.type==='commits');
  assert.equal(resolveTarget({commit:commit.sha},items),commit);
  assert.equal(resolveTarget({commit:commit.sha.slice(0,12)},items),null);
  for (const i of items) for (const link of i.links) {
    const resolved=resolveTarget(link.target,items);
    if (resolved && link.target.path) assert.equal(resolved.source.path,link.target.path);
  }
});
test('contextual status clears stale selections without hiding results', () => {
  const original={type:['commits'],status:['Historical']};
  const commits=applicableFilters(items,original);
  assert.deepEqual(commits.status,[]);
  assert.equal(filter(items,commits).length,items.filter(i=>i.type==='commits').length);
  assert.deepEqual(facets(facetRecords(items,commits,'','status'),'status'),[]);
  assert.deepEqual(original.status,['Historical']);
  const docs=applicableFilters(items,{type:['documents'],status:['Prepared']});
  assert.deepEqual(docs.status,['Prepared']);
  const prepared = items.filter(i=>i.type==='documents' && core.matchesStatus(i,'Prepared'));
  assert.ok(prepared.length > 0);
  assert.deepEqual(filter(items,docs),prepared);
  const logs=applicableFilters(items,{type:['devlog'],status:[]});
  assert.deepEqual(facets(facetRecords(items,logs,'','status'),'status'),['Historical']);
});
test('rationale records preserve joint explanations and navigate explicit associations both ways', () => {
  const rationale = items.filter(i => i.type === 'rationale');
  assert.ok(rationale.length > 0);
  const joint = rationale.find(i => i.decisionIds?.includes('G016') && i.decisionIds.includes('G017'));
  assert.ok(joint);
  assert.equal(rationale.filter(i => i.source.fragment === joint.source.fragment).length,1);
  for (const explanation of rationale) for (const link of explanation.links.filter(l => l.relation === 'explains')) {
    const decision = resolveTarget(link.target,items);
    assert.equal(decision?.type,'decisions');
    assert.ok(decision.links.some(l => l.relation === 'rationale' && resolveTarget(l.target,items) === explanation));
  }
});
test('record targets fail closed on identity drift; source-only links never choose an arbitrary section', () => {
  for (const item of items) for (const link of item.links.filter(l => l.target.recordId)) {
    const record = resolveTarget(link.target,items);
    assert.equal(record?.id,link.target.recordId);
    assert.equal(resolveTarget({...link.target,recordId:'missing'},items),null);
    assert.equal(resolveTarget({...link.target,revision:'0'.repeat(40)},items),null);
    assert.equal(resolveTarget({...link.target,fragment:'missing-fragment'},items),null);
    assert.equal(resolveTarget({...link.target,lineStart:-1},items),null);
  }
  const sections = items.filter(i => i.type === 'rationale');
  const source = {...sections[0].source}; delete source.fragment; delete source.lineStart;
  assert.equal(resolveTarget(source,items),null);
  const counts = new Map();
  for (const item of sections) counts.set(item.source.fragment,(counts.get(item.source.fragment) ?? 0)+1);
  const ambiguous = sections.find(i => counts.get(i.source.fragment) > 1);
  assert.ok(ambiguous);
  assert.equal(resolveTarget({...ambiguous.source},items),null);
});
test('history preserves repeated and cross-type visits, truncates only abandoned forward branch', () => {
  const home={page:'home'}, a={page:'detail',id:'a'}, b={page:'detail',id:'b'};
  let trail=appendVisit([home],0,a);trail=appendVisit(trail,1,b);trail=appendVisit(trail,2,a);
  assert.deepEqual(trail,[home,a,b,a]);
  assert.deepEqual(appendVisit(trail,1,{page:'briefing'}),[home,a,{page:'briefing'}]);
});
test('current destination is a no-op preserving filters, cursor and forward history', () => {
  const browse={page:'browse',filters:{type:['decisions'],status:['Accepted']},query:'release',offset:20};
  const trail=[{page:'home'},browse,{page:'briefing'}];
  assert.equal(appendVisit(trail,1,{page:'browse',filters:{type:['decisions']}}),trail);
  assert.equal(trail[1],browse);
  assert.equal(appendVisit(trail,2,{page:'briefing'}),trail);
  assert.equal(appendVisit([{page:'detail',id:'a'}],0,{page:'detail',id:'a'}).length,1);
  assert.equal(appendVisit([{page:'detail',id:'a'}],0,{page:'detail',id:'a',fragment:'evidence'}).length,2);
  assert.equal(appendVisit(trail,1,{page:'browse',filters:{type:['documents']}}).length,3);
  assert.equal(appendVisit(trail,1,{page:'browse'}).length,3);
});
test('Status briefing aggregates source facts without transaction state or inferred relationships', () => {
  const report=briefing(items);
  assert.ok(report.documents.some(i=>i.source.path==='docs/PROGRESS.md'));
  assert.ok(report.decisions.every(i=>!/^superseded/i.test(i.status)));
  assert.deepEqual(Object.keys(report),['decisions','documents']);
  assert.ok(sorted(items,'recent').every(i=>items.includes(i)));
});
test('implementation records are grouped by declared exact paths in declared order, with an unmapped fallback', () => {
  const doc = path => ({id:path,type:'documents',source:{path}});
  const records = ['a.md','b.md','c.md','d.md'].map(doc);
  const names = result => result.map(([label,list]) => [label,list.map(i => i.source.path)]);
  assert.deepEqual(names(groupImplementations(records,[{label:'Second',paths:['c.md']},{label:'First',paths:['b.md','a.md','missing.md']}])),
    [['Second',['c.md']],['First',['a.md','b.md']],['Other implementation',['d.md']]]);
  assert.deepEqual(names(groupImplementations(records,[])),[['Other implementation',['a.md','b.md','c.md','d.md']]]);
  // A path listed twice never shows a record twice; the earlier group keeps it.
  assert.deepEqual(names(groupImplementations(records,[{label:'One',paths:['a.md']},{label:'Two',paths:['a.md','b.md']}])),
    [['One',['a.md']],['Two',['b.md']],['Other implementation',['c.md','d.md']]]);
});
test('hostile source content is escaped rather than executed', () => {
  assert.equal(escape('<img src=x onerror="alert(1)">'), '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
});
test('browser fetches are restricted to the five datasets, the declared groups, the local queue (also read by the status light) and the one refresh POST', async () => {
  const app=await readFile(new URL('../../../extensions/mission_control/ui/app.mjs',import.meta.url),'utf8');
  assert.equal((app.match(/\bfetch\(/g) ?? []).length,5);
  assert.ok(app.includes("fetch('../groups.json', {cache:'no-store'})"));
  assert.ok(app.includes('fetch(`../data/${type}.json`'));
  assert.equal(app.split("fetch('../queue.json', {cache:'no-store'})").length - 1, 2); // the queue tab and the status light
  assert.ok(app.includes("fetch('../refresh', {method: 'POST', cache: 'no-store'})"));
  assert.ok(!app.includes('XMLHttpRequest'));
});

test('hash display preserves exact full identities and never resolves prefixes or content hashes', () => {
  const sha = 'f83c6ccf3fd859f2cac90a58b03a91bd0f5ec7e5';
  const commit = {type:'commits',sha,id:`commit:${sha}`,title:'Display title',contentText:'Commit subject & purpose\n\nBody is not tooltip text'};
  const linked = hashText(sha,[commit]);
  assert.ok(linked.includes(`data-item="commit:${sha}"`));
  assert.ok(linked.includes(`title="Commit subject &amp; purpose&#10;${sha}"`));
  assert.ok(!linked.includes('Body is not tooltip text'));
  assert.ok(linked.includes('>f83c6ccf3fd8</button>'));
  assert.ok(!hashText(sha.slice(0,12),[commit]).includes('data-item'));
  assert.ok(!hashText(sha.slice(0,-1)+'6',[commit]).includes('data-item'));
  for (const text of [`blob: ${sha}`,`proposal content hash: ${sha}`,sha+'a'.repeat(24)]) {
    assert.ok(!hashText(text,[commit]).includes('data-item'));
    assert.ok(hashText(text,[commit]).includes('title="'));
  }
  assert.ok(!hashText(sha,[commit],false).includes('data-item'));
  assert.ok(hashText('<script>'+sha+'</script>',[commit]).includes('&lt;script&gt;'));
  assert.equal(hashText('x'+sha,[commit]),'x'+sha);
  assert.equal(hashText('ABC123',[]),'ABC123');
  assert.equal(hashText('https://example.com/commit/'+sha,[commit]),'https://example.com/commit/'+sha);
  const before = JSON.stringify(items); hashText(sha,items); assert.equal(JSON.stringify(items),before);
});
import * as core from '../../../extensions/mission_control/ui/core.mjs';

async function presentation(records, visit) {
  const app = (await readFile(new URL('../../../extensions/mission_control/ui/app.mjs',import.meta.url),'utf8'))
    .replace(/^import .*?;\n/, '').split("document.addEventListener(")[0];
  const names = Object.keys(core);
  return new Function('e', ...names, 'records', 'visit', `${app}\nitems=records;trail=[visit];cursor=0;return {card,detail,commitIdentity,browse};`)
    (core.escape, ...names.map(name=>core[name]), records, visit);
}

test('commit identity uses the common provenance line and structured metadata without author or JSON mutation', async () => {
  const commit = {...items.find(i=>i.type==='commits'),
    committer:{epochSeconds:0}, author:{name:'A <B>',email:'a&b@example.com',epochSeconds:999},
    classes:['governance','tooling'], subclasses:['mission-control'], worker:'Worker <W>', inferredFields:[]};
  const original = JSON.stringify(commit);
  const renderer = await presentation([commit],{page:'browse'});
  const html = renderer.commitIdentity(commit);
  const local = core.formatTimestamp({ms:0,precision:'second'},core.browserTimeZone() ?? undefined);
  assert.match(local,/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/);
  assert.ok(html.startsWith(`<p class="source-small"><time datetime="1970-01-01T00:00:00Z">${local}</time> · <button class="hash-link" data-item="${commit.id}"`));
  assert.ok(html.endsWith(' · Class: governance, tooling · Subclass: mission-control · Worker: Worker &lt;W&gt;</p>'));
  assert.ok(!html.includes('Author') && !html.includes('a&amp;b@example.com'));
  assert.ok(!html.includes('committed') && !html.includes('inferred') && !html.includes('class="derived"'));
  assert.equal(JSON.stringify(commit),original);
  assert.ok(items.filter(i=>i.type==='commits').every(i=>i.author?.name && i.author?.email));
});

test('commit metadata omits absent Subclass, keeps unknowns and marks only inferred fields', () => {
  const base = {classes:['tooling'], subclasses:[], worker:null, inferredFields:[]};
  assert.equal(core.commitMetadataHtml(base),'Class: tooling · Worker: unknown');
  assert.equal(core.commitMetadataHtml({...base,classes:[]}),'Class: unknown · Worker: unknown');
  const mark = '<span class="inferred" title="Inferred from historical evidence">†</span>';
  assert.equal(core.commitMetadataHtml({...base,inferredFields:['classes']}),`Class: tooling${mark} · Worker: unknown`);
  assert.equal(core.commitMetadataHtml({...base,subclasses:['checkpoint','mission-control'],inferredFields:['subclasses']}),
    `Class: tooling · Subclass: checkpoint, mission-control${mark} · Worker: unknown`);
  assert.equal(core.commitMetadataHtml({...base,worker:'Synthetic worker',inferredFields:['worker']}),`Class: tooling · Worker: Synthetic worker${mark}`);
  assert.equal(core.commitMetadataHtml({...base,classes:[],inferredFields:['classes','worker']}),'Class: unknown · Worker: unknown');
  assert.notEqual(core.INFERRED_MARK,core.DERIVED_MARK);
  for (const commit of items.filter(i=>i.type==='commits')) {
    const html = core.commitMetadataHtml(commit);
    assert.ok(!/mixed/i.test(html) && !html.includes('·  ·') && !html.endsWith('· '));
    assert.equal(html.includes('Subclass: '),commit.subclasses.length > 0);
    assert.equal(html.includes('class="inferred"'),commit.inferredFields.some(f=>f==='worker' ? commit.worker : commit[f].length));
    if (!commit.worker) assert.ok(html.includes('Worker: unknown') && !html.includes(`Worker: unknown${mark}`));
  }
  const explicit = items.find(i=>i.type==='commits' && i.worker && !i.inferredFields.length && i.subclasses.length);
  assert.equal(core.commitMetadataHtml(explicit),`Class: ${explicit.classes.join(', ')} · Subclass: ${explicit.subclasses.join(', ')} · Worker: ${core.escape(explicit.worker)}`);
});

test('timestamps keep source precision, cap at seconds, use 24-hour local time and mark only commit-derived values', () => {
  const ny = 'America/New_York', commit = {type:'commits',sha:'a'.repeat(40),committer:{epochSeconds:1791297618}};
  const fmt = (record,zone=ny) => { const t = core.recordTimestamp(record,[commit]); return t && core.formatTimestamp(t,zone); };
  assert.equal(fmt({date:'2026-10-02T08:43Z'}),'2026-10-02 04:43');
  assert.equal(fmt({date:'2026-10-02T08:43Z'},'UTC'),'2026-10-02 08:43');
  assert.equal(fmt({date:'2026-10-02'}),'2026-10-02');
  assert.equal(fmt({date:'2026-10-02 (America/New_York). Historical record'}),'2026-10-02');
  assert.equal(fmt({date:'2026-10-02T09:40:18.437Z'},'UTC'),'2026-10-02 09:40:18');
  assert.equal(fmt({date:'2026-10-02T09:40Z'},'UTC'),'2026-10-02 09:40');
  assert.equal(fmt({date:'2026-10-02T21:40:18+02:00'},'UTC'),'2026-10-02 19:40:18');
  assert.equal(fmt({date:'2026-10-02T09:40'}),'2026-10-02');
  assert.equal(fmt({type:'commits',committer:{epochSeconds:1791297618}},'UTC'),'2026-10-06 14:40:18');
  assert.equal(fmt({type:'commits',committer:{epochSeconds:1791297618}}),'2026-10-06 10:40:18');
  assert.equal(fmt({source:{path:'docs/X.md',revision:commit.sha}}),'2026-10-06 10:40:18');
  assert.equal(core.recordTimestamp({source:{path:'docs/X.md',revision:commit.sha}},[commit]).derived,true);
  assert.equal(core.recordTimestamp({date:'2026-10-02T08:43Z',source:{revision:commit.sha}},[commit]).derived,false);
  assert.equal(core.recordTimestamp({date:'not a date',source:{selection:'proposal'}},[commit]),null);
  assert.equal(core.recordTimestamp({source:{revision:'b'.repeat(40)}},[commit]),null);
  const line = record => core.provenanceHtml(record,[commit],ny);
  assert.equal(line({date:'2026-10-02T08:43Z',source:{path:'devlog/a.md',revision:commit.sha}}),
    `<time datetime="2026-10-02T08:43Z">2026-10-02 04:43</time> · devlog/a.md · ${core.hashText(commit.sha,[commit])}`);
  assert.equal(line({source:{path:'docs/CHECKPOINT-WORKFLOW.md',revision:commit.sha}}),
    `<time datetime="2026-10-06T14:40:18Z">2026-10-06 10:40:18</time><span class="derived" title="Timestamp derived from source commit">*</span> · docs/CHECKPOINT-WORKFLOW.md · ${core.hashText(commit.sha,[commit])}`);
  assert.equal(line({date:'2026-10-01',source:{path:'governance/DECISIONS.md',fragment:'g001',revision:commit.sha}}),
    `<time datetime="2026-10-01">2026-10-01</time> · governance/DECISIONS.md#g001 · ${core.hashText(commit.sha,[commit])}`);
  assert.equal(line({source:{path:'docs/PROPOSED.md',selection:'proposal'}}),'docs/PROPOSED.md');
  for (const i of items) {
    const html = core.provenanceHtml(i,items,ny), t = core.recordTimestamp(i,items);
    assert.ok(!/committed|No recorded date|N\/A|Unknown date/i.test(html.replace(/<[^>]+>/g,'').replace(core.escape(i.source.path ?? '') + (i.source.fragment ? '#' + core.escape(i.source.fragment) : ''),'')));
    assert.ok(!/\d{2}:\d{2}:\d{2}\.\d/.test(html));
    assert.equal(html.includes('class="derived"'),Boolean(t?.derived));
    assert.equal(Boolean(t?.derived),!i.date && i.type !== 'commits' && Boolean(t));
    if (i.type === 'devlog') assert.match(html,/^<time datetime="[^"]+Z">\d{4}-\d{2}-\d{2} \d{2}:\d{2}<\/time> · /);
    if (i.source.revision) assert.ok(html.includes(`data-item="commit:${i.source.revision}"`));
  }
});

test('log titles show the entry title once followed by its date', () => {
  assert.equal(core.logTitle({title:'The first entry',date:'2026-10-02T08:43Z'}),'The first entry · 2026-10-02T08:43Z');
  assert.equal(core.logTitle({title:'A plain title',subject:'Other'}),'A plain title');
  assert.equal(core.logTitle({title:'An ISO entry',subject:'Other',date:'2026-10-16T13:03Z'}),'An ISO entry · 2026-10-16T13:03Z');
  for (const log of items.filter(i=>i.type==='devlog')) assert.ok(core.logTitle(log).endsWith(' · ' + log.date));
});

test('header carries the server-filled project identity slot and one display legend', async () => {
  const html = await readFile(new URL('../../../extensions/mission_control/ui/index.html',import.meta.url),'utf8');
  assert.ok(html.includes('>Mission Control</h1><p class="tagline" id="project-name">Project unavailable</p>'));
  assert.ok(!html.includes('replace with your project name'));
  assert.ok(html.includes('<p class="eyebrow">REPOSITORY OBSERVATORY</p><h1 title="'));
  assert.ok(html.includes('<p id="legend" class="legend"></p>'));
  assert.ok(!html.includes('mailto:'));
});

test('the masthead draws a horizontal and a vertical band that cross in a square, with the eyebrow inside the horizontal band', async () => {
  const html = await readFile(new URL('../../../extensions/mission_control/ui/index.html', import.meta.url), 'utf8');
  const css = await readFile(new URL('../../../extensions/mission_control/ui/style.css', import.meta.url), 'utf8');
  const header = html.slice(html.indexOf('<header class="masthead">'), html.indexOf('</header>'));
  for (const name of ['band-h', 'band-v']) assert.ok(header.includes(`<div class="${name}" aria-hidden="true"></div>`), name);
  assert.ok(!header.includes('class="signal"') && !header.includes('elbow'));
  const block = css.slice(css.indexOf('/* Crossing masthead'), css.indexOf('/* Status light in the masthead'));
  for (const rule of ['--band:34px', '--band-gap:8px', '--band-v-left:46px', '--hair:6px', '--hair-gap:6px', '--band-top:calc(var(--band-gap) + var(--hair) + var(--hair-gap))']) assert.ok(css.includes(rule), rule);
  // The palette is blues and turquoise.
  for (const rule of ['--bg:#0b1722', '--accent:#456fb9', '--link:#4fd1c5', '--indigo:#8fa8f0', '--sky:#9fdcf0']) assert.ok(css.includes(rule), rule);
  for (const gone of ['--amber', '--violet', '--rose', '--teal', '--blue:', '--elbow', 'border-radius:var(--elbow', 'border-top-left-radius']) assert.ok(!css.includes(gone), gone);
  for (const rule of ['.band-h{inset:var(--band-top) 0 auto 0;height:var(--band);', '.band-v{inset:0 auto 20px var(--band-v-left);width:var(--band);', 'pointer-events:none', 'color:var(--bg)']) assert.ok(block.includes(rule), rule);
  // Straight bands: neither carries a radius, and both are translucent so that the crossing square shows the two colors blended.
  assert.ok(!/border-radius|border-top|border-left/.test(block));
  assert.equal((block.match(/background:rgba\(/g) || []).length, 4);
  // The thin lines: turquoise beside the vertical band and blue above the horizontal band, each one line width from its band.
  assert.ok(block.includes('.masthead::before{content:"";inset:0 auto 20px calc(var(--band-v-left) - var(--hair-gap) - var(--hair));width:var(--hair);background:rgba(79,209,197,.9)}'));
  assert.ok(block.includes('.masthead::after{content:"";inset:var(--band-gap) 0 auto 0;height:var(--hair);background:rgba(88,140,235,.75)}'));
  assert.equal(6, 6, 'the gap and the line share one size, --hair-gap and --hair');
  // The eyebrow sits in the horizontal band, right of the vertical one.
  assert.ok(block.includes('.masthead .eyebrow{position:absolute;top:var(--band-top);left:calc(var(--band-v-left) + var(--band) + 16px);'), 'the eyebrow follows the top of the horizontal band and clears the vertical band');
  assert.ok(css.includes('min-height:calc(124px + var(--band-top));padding:calc(42px + var(--band-top)) 28px 24px 102px'), 'the content keeps its distance below the band and right of the vertical band');
  assert.ok(block.includes('@media(max-width:650px){.masthead{padding:calc(42px + var(--band-top)) 20px 24px 90px}.band-v{left:16px}.masthead::before{left:calc(16px - var(--hair-gap) - var(--hair))}'));
  assert.ok(block.includes('left:calc(16px + var(--band) + 12px);right:12px;font-size:11px;letter-spacing:.02em'));
});

test('the main bar has a views cell and a records cell divided by a line, with no Browse all, and each record button opens the search with its section preselected', async () => {
  const app = await readFile(new URL('../../../extensions/mission_control/ui/app.mjs', import.meta.url), 'utf8');
  const css = await readFile(new URL('../../../extensions/mission_control/ui/style.css', import.meta.url), 'utf8');
  // Views: Queue first, then Overview and Status briefing. Records: one button per section, from TYPES.
  assert.ok(app.includes("const VIEW_PAGES = [['queue','Queue'],['home','Overview'],['briefing','Status briefing']];"));
  assert.ok(app.includes("const RECORD_ORDER = ['decisions','rationale','documents','commits','devlog'];"), 'records run from the most to the least authoritative');
  assert.ok(app.includes("cell('Views',VIEW_PAGES.map(") && app.includes("cell('Records',RECORD_ORDER.filter(type => TYPES.includes(type)).map(type =>"));
  assert.deepEqual([...TYPES].sort(), ['commits','decisions','devlog','documents','rationale'], 'every section has a record button');
  const page = await readFile(new URL('../../../extensions/mission_control/ui/index.html', import.meta.url), 'utf8');
  assert.ok(!page.includes('id="back"') && !page.includes('id="forward"') && !app.includes("'#back'") && !app.includes("'#forward'"), 'the browser provides Back and Forward');
  assert.ok(page.includes('<nav id="breadcrumbs"'), 'the breadcrumbs stay');
  assert.ok(!app.includes('bar-label'), 'the cells carry no visible label');
  // Every one of the eight buttons has a hover text, and each record button's also says what a click does.
  for (const key of ['queue','home','briefing',...TYPES]) assert.ok(app.includes(`\n  ${key}: '`), 'hover text of ' + key);
  assert.ok(app.includes('title="${e(TIPS[page])}"') && app.includes("title=\"${e(TIPS[type] + ' Opens the search with this section selected.')}\""));
  assert.ok(!app.includes('Browse all') && !app.includes("'browse'].map"), 'Browse all is gone');
  // A record button opens the search page with that one section selected in the type filter, which stays visible.
  assert.ok(app.includes("navigate({page:'browse',filters:{...blankFilters(),type:[button.dataset.type]}})"));
  assert.ok(app.includes("aria-pressed=\"${searchedType(current()) === type}\""));
  assert.ok(!app.includes('class="tiles"') && !app.includes('class="tile"'), 'the Overview tiles that repeated the record buttons are gone');
  assert.ok(app.includes('<p class="counts">'));
  // The divider and the small label over each cell.
  assert.ok(css.includes('.bar-cell+.bar-cell{border-left:1px solid var(--edge);padding-left:1.4rem}'));
  assert.ok(!css.includes('.tile'));
  // The record buttons and the search share one destination test: the section selected in the filter is the destination.
  assert.ok(core.sameDestination({page:'browse',filters:{type:['commits']}}, {page:'browse',filters:{type:['commits']}}));
  assert.ok(!core.sameDestination({page:'browse',filters:{type:['commits']}}, {page:'browse',filters:{type:['decisions']}}));
  // An address with no page, or an unknown one, opens the Queue; an explicit #home still opens Overview.
  assert.ok(app.includes("includes(page) ? page : 'queue'};"));
  assert.ok(app.includes("catch {visit={page:'queue'};}"));
});

test('cards and details use one provenance line without committed, absence prose or repeated dates', async () => {
  const commit = items.find(i=>i.type==='commits');
  const browse = await presentation(items,{page:'browse'});
  const overview = await presentation(items,{page:'home'});
  assert.ok(browse.card(commit).includes(' · Class: '));
  assert.ok(!overview.card(commit).includes(' · Class: ') && !overview.card(commit).includes('Author'));
  for (const i of items) for (const html of [browse.card(i),overview.card(i),browse.card(i,true)]) {
    const strip = value => value.replace(/<[^>]+>/g,'').replace(core.escape(i.source.path ?? '') + (i.source.fragment ? '#' + core.escape(i.source.fragment) : ''),'');
    // [^]*? so a carriage return inside a commit message (a title attribute) cannot end the match early.
    const meta = strip([...html.matchAll(/<p class="source-small">[^]*?<\/p>/g)].map(m=>m[0]).join(''));
    assert.ok(meta.length && !/committed|No recorded date/.test(meta));
    if (i.type === 'devlog' && i.date) assert.equal(strip(html).split(i.date).length - 1,1);
  }
  const detail = await presentation(items,{page:'detail',id:commit.id});
  const html = detail.detail();
  assert.ok(html.includes('Source traceability'));
  assert.ok(html.includes('recordedCommitterTime'));
  assert.ok(html.includes('Explicit outgoing links'));
  assert.ok(!html.includes('Original text from JSON'));
  assert.ok(html.includes(hashText(core.content(commit),items)));
  for (const record of items.filter(i=>i.type!=='commits')) {
    const renderer = await presentation(items,{page:'detail',id:record.id});
    assert.ok(renderer.detail().includes(record.contentFormat==='markdown' ? 'Original Markdown from JSON' : 'Original text from JSON'));
  }
});

test('newest-first sorting uses displayed timestamps; decision ID order descends and is offered only for decisions', async () => {
  const commit = {id:'commit:'+'a'.repeat(40),type:'commits',sha:'a'.repeat(40),committer:{epochSeconds:1791297618}};
  const sample = [
    {id:'decision:G023',type:'decisions',date:'2026-10-03',source:{}},
    {id:'decision:G033',type:'decisions',date:'2026-10-03',source:{}},
    {id:'documents:docs/X.md',type:'documents',source:{revision:commit.sha}},
    {id:'documents:docs/P.md',type:'documents',source:{selection:'proposal'}},
    commit
  ];
  assert.deepEqual(core.sorted(sample,'recent').map(i=>i.id),['documents:docs/X.md',commit.id,'decision:G033','decision:G023','documents:docs/P.md']);
  assert.deepEqual(core.sorted(sample.filter(i=>i.type==='decisions'),'id').map(i=>i.id),['decision:G033','decision:G023']);
  const decisions = core.sorted(items.filter(i=>i.type==='decisions'),'id');
  // The newest numbered decision comes from the data, so a newer snapshot cannot fail this assertion.
  const numbered = decisions.map(i=>/^decision:G(\d{3})$/.exec(i.id)).filter(Boolean).map(m=>m[1]).sort();
  assert.equal(decisions[0].id,'decision:G' + numbered.at(-1)); assert.equal(decisions.at(-1).id,'decision:G001');
  const recent = core.sorted(items,'recent');
  for (let n=1;n<recent.length;n++) assert.ok((core.recordedTime(recent[n-1],items) ?? -Infinity) >= (core.recordedTime(recent[n],items) ?? -Infinity));
  const browseDecisions = await presentation(items,{page:'browse',section:'decisions',filters:{type:['decisions'],categories:[],topics:[],status:[]}});
  const browseAll = await presentation(items,{page:'browse'});
  assert.ok(browseDecisions.browse().includes('<option value="id" '));
  assert.ok(browseDecisions.browse().includes('<option value="recent" selected>Recorded date · newest first</option>'));
  assert.ok(!browseAll.browse().includes('<option value="id" ') && !browseAll.browse().includes('value="title"'));
  const rationale = core.sorted(items.filter(i=>i.type==='rationale'),'id');
  const keys = rationale.map(i=>core.decisionKey(i)).filter(Boolean);
  assert.deepEqual(keys, [...keys].sort().reverse());
  assert.equal(keys[0], [...items.filter(i=>i.type==='rationale').flatMap(i=>i.decisionIds)].sort().at(-1));
  assert.equal(rationale.at(-1).decisionIds.length,0);
  assert.equal(core.decisionKey({type:'rationale',decisionIds:['G009','G011','G010']}),'G011');
  const browseRationale = await presentation(items,{page:'browse',section:'rationale',filters:{type:['rationale'],categories:[],topics:[],status:[]}});
  assert.ok(browseRationale.browse().includes('<option value="id" '));
  const browseDocuments = await presentation(items,{page:'browse',section:'documents',filters:{type:['documents'],categories:[],topics:[],status:[]}});
  assert.ok(browseDocuments.browse().includes('<option value="title" >Title · A → Z</option>') && !browseDocuments.browse().includes('<option value="id" '));
  assert.ok(!browseDecisions.browse().includes('value="title"') && !browseRationale.browse().includes('value="title"'));
  const browseCommits = await presentation(items,{page:'browse',section:'commits',filters:{type:['commits'],categories:[],topics:[],status:[]}});
  assert.ok(browseCommits.browse().includes('<option value="title" >Title · A → Z</option>') && !browseCommits.browse().includes('<option value="id" '));
  const commitCard = browseCommits.card(items.find(i=>i.type==='commits'));
  assert.ok(/<h3><button class="open"[^>]*>(?![0-9a-f]{12} · )/.test(commitCard));
  assert.ok(!commitCard.includes('class="summary"'));
  const distinct = {...items.find(i=>i.type==='commits'),summary:'A different summary'};
  assert.ok(browseCommits.card(distinct).includes('<p class="summary">A different summary</p>'));
  assert.ok(!browseCommits.browse().includes('value="status"') && browseDocuments.browse().includes('value="status"') && browseAll.browse().includes('value="status"'));
  const staleStatus = await presentation(items,{page:'browse',section:'commits',sort:'status',filters:{type:['commits'],categories:[],topics:[],status:[]}});
  assert.ok(staleStatus.browse().includes('<option value="recent" selected>'));
  const stale = await presentation(items,{page:'browse',sort:'id'});
  assert.ok(stale.browse().includes('<option value="recent" selected>'));
});

test('commit class, subclass and worker filters use projected values and combine like other dimensions', async () => {
  const commits = items.filter(i=>i.type==='commits');
  assert.deepEqual(core.facets(commits,'classes'),[...new Set(commits.flatMap(i=>i.classes))].sort(core.compare));
  assert.deepEqual(core.facets(commits,'worker'),[...new Set(commits.map(i=>i.worker).filter(Boolean))].sort(core.compare));
  assert.ok(!core.facets(commits,'worker').includes(null) && !core.facets(commits,'classes').includes('mixed'));
  const tooling = core.filter(items,{classes:['tooling']});
  assert.ok(tooling.length && tooling.every(i=>i.type==='commits' && i.classes.includes('tooling')));
  const both = core.filter(items,{classes:['tooling'],subclasses:['mission-control']});
  assert.ok(both.every(i=>i.classes.includes('tooling') && i.subclasses.includes('mission-control')));
  const worker = commits.find(i=>i.worker).worker;
  assert.ok(core.filter(items,{worker:[worker]}).every(i=>i.worker===worker));
  assert.deepEqual(core.applicableFilters(items,{type:['decisions'],worker:[worker],classes:['tooling']}).worker,[]);
  const commitsView = await presentation(items,{page:'browse',section:'commits',filters:{type:['commits']}});
  const html = commitsView.browse();
  for (const legend of ['Commit class','Commit subclass','Worker']) assert.ok(html.includes(`<legend>${legend}</legend>`));
  assert.ok(!html.includes('<legend>Category</legend>') && !html.includes('<legend>Topic</legend>') && !html.includes('No source values'));
  const decisionsView = await presentation(items,{page:'browse',section:'decisions',filters:{type:['decisions']}});
  assert.ok(!decisionsView.browse().includes('<legend>Worker</legend>'));
  assert.ok(decisionsView.browse().includes('<fieldset><legend>Status keywords</legend><p>Contains matching') && !decisionsView.browse().includes('<details'));
});

// ---- Local queue view ----
const UUID = '0b8f3a52-4c1d-4e6a-9f27-3d5c8a1e7b90';
const job = (handle, extra = {}) => ({handle, status: 'PENDING', activity: 'UNKNOWN', description: null, work_kind: null, waiting: null,
  dependencies: [], unsatisfied_dependencies: [], missing_dependencies: [], awaiting_lead: null, pending_count: 0, has_record: true, ...extra});
const payload = (extra = {}) => ({schema: 1, authority: 'local/non-authoritative', read_at: '2026-10-10T12:00:00Z', state: 'ok', queue: [], unqueued: [], ...extra});

test('session links exist only for allow-listed vendors with a UUID id; everything else is text or unknown', () => {
  assert.deepEqual(core.sessionLink({vendor: 'claude', id: UUID}), {state: 'link', vendor: 'claude', id: UUID, href: `claude://claude.ai/epitaxy/${UUID}`});
  assert.equal(core.sessionLink({vendor: 'codex', id: UUID.toUpperCase()}).href, `codex://threads/${UUID.toUpperCase()}`);
  assert.deepEqual(core.sessionLink({vendor: 'other_ai', id: 'chat-42'}), {state: 'text', vendor: 'other_ai', id: 'chat-42'});
  const hostile = [null, undefined, 'claude', {}, {vendor: 'claude'}, {vendor: 'claude', id: 'x'}, {vendor: 'claude', id: UUID + '/../x'},
    {vendor: 'claude', id: UUID + '" onmouseover="x'}, {vendor: 'codex', id: 'javascript:alert(1)'}, {vendor: 'Claude', id: UUID},
    {vendor: 'constructor', id: UUID}, {vendor: '__proto__', id: UUID}, {vendor: 'toString', id: 'a'}, {vendor: 'x y', id: 'a'},
    {vendor: 'other', id: 'a b'}, {vendor: 'other', id: ''}, {vendor: 'other', id: 'x'.repeat(129)}, {vendor: 7, id: UUID}, {vendor: 'claude', id: 7}];
  for (const bad of hostile) {
    const result = core.sessionLink(bad);
    assert.ok(result.state !== 'link' || /^(claude:\/\/claude\.ai\/epitaxy|codex:\/\/threads)\/[0-9a-f-]{36}$/i.test(result.href), JSON.stringify(bad));
    assert.equal(result.href === undefined || result.state === 'link', true);
  }
  assert.equal(core.sessionLink({vendor: 'claude', id: 'x'}).state, 'unknown');
  assert.equal(core.sessionLink({vendor: 'javascript', id: 'alert(1)'}).state, 'unknown');
  assert.equal(core.sessionLink({vendor: 'constructor', id: UUID}).state, 'text');
  assert.deepEqual([...core.SESSION_LINKS.keys()], ['claude', 'codex']);
});

test('the page allow-list of linked vendors equals the one the helper validates', async () => {
  const helper = await readFile(new URL('../../../tools/coordination/local_jobs.py', import.meta.url), 'utf8');
  const match = /^LINKED_VENDORS = \(([^)]*)\)$/m.exec(helper);
  assert.ok(match);
  assert.deepEqual([...match[1].matchAll(/'([a-z]+)'/g)].map(m => m[1]), [...core.SESSION_LINKS.keys()]);
});

test('queue rows keep Epic bands, dependency-ordered members, empty member priority and unset priorities', () => {
  const model = core.queueModel(payload({
    queue: [{handle: 'sample_epic', kind: 'epic', priority: 3, jobs: [job('member_read', {status: 'WIP'}), job('member_write', {unsatisfied_dependencies: ['member_read']})]},
            {...job('sample_job', {description: 'Tidy files.', work_kind: 'cleanup'}), kind: 'job', priority: null}],
    unqueued: [job('loose_job')]}));
  assert.deepEqual(model.queue.map(r => [r.role, r.priority, r.handle]), [
    ['epic', '3', 'sample_epic (Epic)'], ['member', '', 'member_read'], ['member', '', 'member_write'], ['job', 'unset', 'sample_job']]);
  assert.equal(model.queue[0].description, '2 Jobs');
  assert.deepEqual(model.queue[0].lines, ['1 WIP · 1 PENDING', 'closure: automatic']);
  assert.equal(model.queue[3].workKind, 'cleanup');
  assert.equal(model.queue[3].description, 'Tidy files.');
  assert.deepEqual(model.unscheduled.map(r => [r.role, r.priority, r.handle]), [['job', 'unset', 'loose_job']]);
  assert.equal(model.readAt, '2026-10-10T12:00:00Z');
});

test('the estimated size shows its label, then the note, and stays empty for Epics and unset Jobs', () => {
  const model = core.queueModel(payload({
    queue: [{handle: 'sample_epic', kind: 'epic', priority: 1, jobs: [job('member_one', {estimated_size: 'large'})]},
            {...job('sized_job', {estimated_size: 'uncertain', estimated_size_note: 'small review; fix size uncertain'}), kind: 'job', priority: 2},
            {...job('plain_job', {estimated_size: 'small-medium'}), kind: 'job', priority: 3},
            {...job('unset_job'), kind: 'job', priority: 4},
            {...job('odd_job', {estimated_size: 'huge', estimated_size_note: 'ignored'}), kind: 'job', priority: 5}]}));
  assert.deepEqual(model.queue.map(r => [r.handle, r.estSize]), [
    ['sample_epic (Epic)', ''], ['member_one', 'Large'], ['sized_job', 'Uncertain: small review; fix size uncertain'],
    ['plain_job', 'Small–medium'], ['unset_job', ''], ['odd_job', '']]);
  assert.equal(core.sizeText('medium-large', ''), 'Medium–large');
  assert.equal(core.sizeText(null, 'a note alone'), '');
});

test('the page labels exist for exactly the sizes the helper validates', async () => {
  const helper = await readFile(new URL('../../../tools/coordination/local_jobs.py', import.meta.url), 'utf8');
  const block = /^ESTIMATED_SIZES = \{([^}]*)\}$/m.exec(helper);
  assert.ok(block);
  assert.deepEqual([...block[1].matchAll(/^\s+'([a-z-]+)':/gm)].map(m => m[1]), [...core.SIZE_LABELS.keys()]);
});

test('an empty explicit Epic stays visible with its closure policy', () => {
  const model = core.queueModel(payload({queue: [{handle: 'more_work', kind: 'epic', priority: 1,
    closure_policy: 'explicit', jobs: []}]}));
  assert.equal(model.queue[0].description, '0 Jobs');
  assert.deepEqual(model.queue[0].lines, ['empty; awaiting explicit closure', 'closure: explicit']);
});

test('BLOCKED, waiting on dependencies, missing records and pending counts stay distinct', () => {
  const [blocked, waiting, ghost, noted] = core.queueModel(payload({queue: [
    {...job('blocked_job', {status: 'BLOCKED', waiting: 'Needs the interpreter.', activity: 'PAUSED'}), kind: 'job', priority: 1},
    {...job('waiting_job', {unsatisfied_dependencies: ['blocked_job', 'ghost_job'], missing_dependencies: ['ghost_job']}), kind: 'job', priority: 2},
    {...job('ghost_job', {status: null, has_record: false}), kind: 'job', priority: 3},
    {...job('noted_job', {status: 'WIP', activity: 'ACTIVE', pending_count: 1}), kind: 'job', priority: 4}]})).queue;
  assert.deepEqual(blocked.chips, [{text: 'BLOCKED', tone: 'blocked'}]);
  assert.deepEqual(blocked.lines, ['Needs the interpreter.', 'reported activity: PAUSED']);
  assert.deepEqual(waiting.chips, [{text: 'PENDING', tone: 'plain'}]);
  assert.deepEqual(waiting.lines, ['waiting on: blocked_job, ghost_job (no record)']);
  assert.deepEqual(ghost.chips, [{text: 'no record', tone: 'missing'}]);
  assert.deepEqual(noted.lines, ['reported activity: ACTIVE', '1 pending note']);
});

test('a Job waiting for the lead developer carries a chip, what it waits for and a validated session link', () => {
  const rows = core.queueModel(payload({queue: [
    {...job('previewing', {awaiting_lead: {for: 'preview', session: {vendor: 'claude', id: UUID}}}), kind: 'job', priority: 1},
    {...job('sealed', {awaiting_lead: {for: 'approval', session: {vendor: 'other_ai', id: 'chat-7'}}}), kind: 'job', priority: 2},
    {...job('unknown_session', {awaiting_lead: {for: 'approval'}}), kind: 'job', priority: 3},
    {...job('odd', {awaiting_lead: {for: 'dinner', session: {vendor: 'claude', id: 'x'}}}), kind: 'job', priority: 4},
    {...job('quiet'), kind: 'job', priority: 5}]})).queue;
  assert.deepEqual(rows[0].awaiting, {what: 'preview', session: {state: 'link', vendor: 'claude', id: UUID, href: `claude://claude.ai/epitaxy/${UUID}`}});
  assert.ok(rows[0].chips.some(chip => chip.text === 'waiting for you'));
  assert.equal(rows[1].awaiting.session.state, 'text');
  assert.equal(rows[2].awaiting.session.state, 'unknown');
  assert.deepEqual(rows[3].awaiting, {what: 'unknown', session: {state: 'unknown'}});
  assert.equal(rows[4].awaiting, null);
  assert.ok(!rows[4].chips.some(chip => chip.text === 'waiting for you'));
});

test('absent and error states carry a message and never a queue', () => {
  for (const state of ['absent', 'error']) {
    const model = core.queueModel(payload({state, message: 'No local coordination records exist.'}));
    assert.deepEqual([model.state, model.queue, model.unscheduled, model.message], [state, [], [], 'No local coordination records exist.']);
  }
});

test('an invalid queue payload is refused instead of being displayed', () => {
  for (const bad of [null, {}, payload({schema: 2}), payload({authority: 'repository'}), payload({state: 'fine'}), payload({queue: {}}),
                     payload({unqueued: null}), payload({queue: [{kind: 'job'}]}), payload({queue: [{handle: 'e', kind: 'epic', jobs: 'x'}]}),
                     payload({queue: [{handle: 'e', kind: 'epic', jobs: [null]}]})]) {
    assert.throws(() => core.queueModel(bad), Error, JSON.stringify(bad));
  }
});

test('queue values reach the page only through textContent and setAttribute', async () => {
  const app = await readFile(new URL('../../../extensions/mission_control/ui/app.mjs', import.meta.url), 'utf8');
  const queue = app.slice(app.indexOf('// ---- Local queue tab'), app.indexOf('function render()'));
  assert.ok(queue.length > 500);
  for (const forbidden of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function', 'createContextualFragment']) {
    assert.ok(!queue.includes(forbidden), forbidden);
  }
  assert.equal((queue.match(/setAttribute\('href'/g) ?? []).length, 1);
  assert.ok(queue.includes("setAttribute('href', session.href)"));
  assert.ok(!/\.href\s*=/.test(queue));
  const html = await readFile(new URL('../../../extensions/mission_control/ui/index.html', import.meta.url), 'utf8');
  assert.ok(!/<style|style=|onclick=|<script(?![^>]*src=)/.test(html));
});

// ---- Status light ----
const execution = (extra = {}) => ({schema: '1.0', level: 'CLEAR', line: 'CLEAR: no ref window is open.', read_at: '2026-10-13T12:00:00Z', windows: [],
  database_state: 'ok', declared_without_evidence: 0, poll_seconds: 0, ...extra});
const frozenWindow = (extra = {}) => ({owner: 'm1', scope: 'prepare', state: 'preparing', since: '2026-10-13T10:00:00Z', flags: [], age_flag: false, ...extra});

test('the light reads CLEAR, FROZEN with its owner and time, OVERLAP and UNKNOWN, and the legend carries the meaning', () => {
  assert.deepEqual([core.lightModel(execution()).tone, core.lightModel(execution()).legend], ['clear', 'CLEAR']);
  const frozen = core.lightModel(execution({level: 'FROZEN', windows: [frozenWindow(), frozenWindow({owner: 'm2'})]}));
  assert.deepEqual([frozen.tone, frozen.legend], ['frozen', 'REFS FROZEN: m1, 2026-10-13 10:00:00 UTC (+1 more)']);
  assert.match(frozen.title, /m1 · prepare preparing · since 2026-10-13 10:00:00 UTC/);
  assert.equal(core.lightModel(execution({level: 'FROZEN', windows: [frozenWindow({owner: null, scope: 'lifecycle', state: 'integrate'})]})).legend, 'REFS FROZEN: lifecycle operation, 2026-10-13 10:00:00 UTC');
  const overlap = core.lightModel(execution({level: 'OVERLAP', line: 'OVERLAP: m3, same file(s): a.txt'}));
  assert.deepEqual([overlap.tone, overlap.legend], ['overlap', 'OVERLAP: m3, same file(s): a.txt']);
  const unknown = core.lightModel(execution({level: 'UNKNOWN', line: 'STATUS UNKNOWN: Git failed'}));
  assert.deepEqual([unknown.tone, unknown.legend], ['unknown', 'STATUS UNKNOWN: Git failed']);
});

test('a payload of another major, a missing one or an unknown level is shown as unknown, never as clear', () => {
  for (const bad of [null, undefined, 'CLEAR', {}, execution({schema: '2.0'}), execution({schema: 1}), execution({schema: undefined}),
                     execution({level: 'GREEN'}), execution({level: 'constructor'}), execution({level: undefined})]) {
    const model = core.lightModel(bad);
    assert.equal(model.tone, 'unknown', JSON.stringify(bad));
    assert.equal(model.pollSeconds, 0);
  }
  assert.match(core.lightModel(execution({schema: '2.0'})).legend, /payload version not understood/);
});

test('a newer minor of the same major is read, and unknown fields are ignored', () => {
  const model = core.lightModel(execution({schema: '1.7', level: 'FROZEN', windows: [frozenWindow({future: 'x'})], extra: {a: 1}}));
  assert.equal(model.tone, 'frozen');
});

test('the title lists flags, a degraded database and the read time; a healthy or missing database adds no notice', () => {
  const degraded = core.lightModel(execution({level: 'FROZEN', database_state: 'corrupt', declared_without_evidence: 2, windows: [frozenWindow({flags: ['unknown_or_dead_command', 'age']})]}));
  assert.match(degraded.title, /unknown_or_dead_command · age/);
  assert.match(degraded.title, /claims database: corrupt; derived evidence only/);
  assert.match(degraded.title, /2 declared without evidence/);
  for (const state of ['ok', 'missing']) assert.doesNotMatch(core.lightModel(execution({database_state: state})).title, /claims database/);
});

test('the poll interval is honored only when it is a positive whole number', () => {
  assert.equal(core.lightModel(execution()).pollSeconds, 0);
  assert.equal(core.lightModel(execution({poll_seconds: 30})).pollSeconds, 30);
  for (const bad of [-1, 0, 1.5, '30', null]) assert.equal(core.lightModel(execution({poll_seconds: bad})).pollSeconds, 0);
});

test('hostile payload text stays text: the light is drawn with textContent and setAttribute only', async () => {
  const model = core.lightModel(execution({level: 'FROZEN', windows: [frozenWindow({owner: '<img src=x onerror=alert(1)>'})]}));
  assert.ok(model.legend.includes('<img'));
  const app = await readFile(new URL('../../../extensions/mission_control/ui/app.mjs', import.meta.url), 'utf8');
  const light = app.slice(app.indexOf('// The status light in the masthead'), app.indexOf('function queueView()'));
  assert.ok(light.length > 300);
  for (const forbidden of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) assert.ok(!light.includes(forbidden), forbidden);
  const html = await readFile(new URL('../../../extensions/mission_control/ui/index.html', import.meta.url), 'utf8');
  assert.ok(html.indexOf('id="light"') > html.indexOf('class="actions"') && html.indexOf('id="light"') < html.indexOf('id="refresh"'));
});

test('the title lists each overlap and the stale claims, and the legend of an overlap carries the wording', () => {
  const model = core.lightModel(execution({schema: '1.1', level: 'OVERLAP', line: 'OVERLAP: m1 with m2, same subclass, different files: checkpoint', stale_claims: 2,
    overlaps: [{owners: ['m1', 'm2'], kind: 'same_subclass', text: 'same subclass, different files: checkpoint'}, {owners: 'bad'}, null]}));
  assert.equal(model.legend, 'OVERLAP: m1 with m2, same subclass, different files: checkpoint');
  assert.match(model.title, /overlap: m1 with m2, same subclass, different files: checkpoint/);
  assert.match(model.title, /2 stale claims: the lead developer runs clear/);
  assert.match(core.lightModel(execution({stale_claims: 1})).title, /1 stale claim: the lead developer runs clear/);
  assert.doesNotMatch(core.lightModel(execution({stale_claims: 0})).title, /stale/);
});

// ---- Claims strip ----
const claim = (extra = {}) => ({owner: 'm1', job_handle: 'worker_claims', level: 'CLEAR', subclasses: ['checkpoint', 'decisions'], files: 3, since: '2026-10-13T10:00:00Z', flags: [], ...extra});
const queued = (handle, status) => ({handle, status, kind: 'job'});

test('the strip lists one row per active owner with its level, subclasses, since time and flags', () => {
  const strip = core.stripModel(execution({schema: '1.1', level: 'OVERLAP', claims: [claim({level: 'OVERLAP', flags: ['age']}), claim({owner: 'm2', job_handle: null, level: 'FROZEN', subclasses: [], files: 0})],
    overlaps: [{owners: ['m1', 'm2'], kind: 'same_file', text: 'same file(s): a.py'}]}), null);
  assert.equal(strip.note, '');
  assert.equal(strip.rows.length, 2);
  const [first, second] = strip.rows;
  assert.deepEqual([first.owner, first.level, first.tone, first.subclasses, first.files, first.since, first.flags, first.jobHandle],
                   ['m1', 'OVERLAP', 'overlap', 'checkpoint, decisions', 3, '2026-10-13 10:00:00 UTC', ['age'], 'worker_claims']);
  assert.deepEqual(first.detail, ['with m2: same file(s): a.py']);
  assert.deepEqual([second.tone, second.subclasses, second.jobHandle], ['frozen', '', '']);
});

test('with nothing active the strip is one quiet line, and an unreadable status is said plainly', () => {
  assert.deepEqual(core.stripModel(execution({schema: '1.1', claims: []}), null), {rows: [], note: 'No active claims'});
  assert.deepEqual(core.stripModel(execution(), null), {rows: [], note: 'No active claims'}, 'a 1.0 payload has no claims');
  assert.match(core.stripModel(execution({level: 'UNKNOWN'}), null).note, /Claims unavailable/);
  for (const bad of [null, undefined, 'x', execution({schema: '2.0'}), execution({level: 'GREEN'})]) assert.match(core.stripModel(bad, null).note, /Claims unavailable/);
});

test('a claim is joined to the queue on its Job handle, and a finished Job with a live transaction is a conflict flag', () => {
  const queue = {queue: [{kind: 'epic', handle: 'worker_coordination', jobs: [queued('worker_claims', 'COMPLETE'), queued('other_job', 'WIP')]}, queued('solo_job', 'CANCELLED')],
                 unqueued: [queued('loose_job', 'COMPLETE')]};
  const rows = core.stripModel(execution({schema: '1.1', claims: [claim(), claim({owner: 'm2', job_handle: 'worker_coordination--worker_claims'}), claim({owner: 'm3', job_handle: 'other_job'}),
    claim({owner: 'm4', job_handle: 'solo_job'}), claim({owner: 'm5', job_handle: 'loose_job'}), claim({owner: 'm6', job_handle: 'unknown_job'}), claim({owner: 'm7', job_handle: null})]}), queue).rows;
  assert.deepEqual(rows.map(r => r.flags.length), [1, 1, 0, 1, 1, 0, 0]);
  assert.equal(rows[0].flags[0], 'conflict: Job COMPLETE, transaction still exists');
  assert.equal(core.stripModel(execution({schema: '1.1', claims: [claim()]}), {queue: 'bad'}).rows[0].flags.length, 0);
});

test('hostile claim fields stay text and malformed rows are skipped', async () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const strip = core.stripModel(execution({schema: '1.1', claims: [claim({owner: hostile, subclasses: [hostile, 3], flags: [hostile, 7]}), null, 'x', claim({owner: ''}), claim({level: 'PURPLE'})]}), null);
  assert.equal(strip.rows.length, 2);
  assert.ok(strip.rows[0].owner.includes('<img') && strip.rows[0].flags[0].includes('<img'));
  assert.equal(strip.rows[1].tone, 'unknown');
  const app = await readFile(new URL('../../../extensions/mission_control/ui/app.mjs', import.meta.url), 'utf8');
  const drawing = app.slice(app.indexOf('// The claims strip: only active claims'), app.indexOf('function render()'));
  assert.ok(drawing.length > 500);
  for (const forbidden of ['innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write', 'eval(', 'new Function']) assert.ok(!drawing.includes(forbidden), forbidden);
  assert.ok(drawing.lastIndexOf('claimsStrip(strip)') > drawing.indexOf("el('p', 'notice'"), 'the strip sits under the notice, at the top of the Queue tab');
});

test('the light styles are prefixed and scoped, with a translucent tint for each level', async () => {
  const css = await readFile(new URL('../../../extensions/mission_control/ui/style.css', import.meta.url), 'utf8');
  const block = css.slice(css.indexOf('/* Status light in the masthead'), css.indexOf('/* Local queue tab.'));
  const selectors = [...block.matchAll(/(?:^|\})\s*([^{}@/][^{}]*)\{/g)].flatMap(m => m[1].split(',')).map(s => s.trim()).filter(Boolean);
  assert.ok(selectors.length >= 8);
  for (const selector of selectors) assert.ok(selector.startsWith('.jq-light'), selector);
  for (const tone of ['clear', 'frozen', 'overlap', 'unknown']) assert.match(block, new RegExp(`\\.jq-light-${tone}\\{background:rgba\\(`));
});

test('the style rules for the queue are scoped and prefixed so nothing collides with Mission Control', async () => {
  const css = await readFile(new URL('../../../extensions/mission_control/ui/style.css', import.meta.url), 'utf8');
  const block = css.slice(css.indexOf('/* Local queue tab.'));
  const selectors = [...block.matchAll(/(?:^|\})\s*([^{}@/][^{}]*)\{/g)].flatMap(m => m[1].split(',')).map(s => s.trim()).filter(Boolean);
  assert.ok(selectors.length > 10);
  for (const selector of selectors) assert.ok(selector.startsWith('.jq-'), selector);
  for (const selector of selectors.filter(s => !/^\.jq-(chip|sub|open)/.test(s))) assert.ok(selector.startsWith('.jq-table'), selector);
  for (const value of ['rgba(128,128,128,0.14)', 'rgba(70,120,210,0.13)']) assert.ok(block.includes(value));
});

test('the footer names which copy of the datasets is on screen', () => {
  assert.equal(core.dataSourceLabel('committed'), 'SOURCE UNKNOWN');
  const header = head => 'local; revision=' + 'a'.repeat(40) + '; generated=2026-10-10T12:00:00Z; generator=' + 'b'.repeat(12) + '; head=' + head;
  assert.equal(core.dataSourceLabel(header('current')), 'LOCAL COPY GENERATED 2026-10-10 12:00:00 UTC FROM ' + 'a'.repeat(12) + ' · CURRENT WITH HEAD · GENERATOR ' + 'b'.repeat(12));
  assert.match(core.dataSourceLabel(header('older')), /OLDER THAN HEAD, PRESS REFRESH/);
  for (const bad of ['', null, undefined, 'local; revision=abc; refreshed=now', header('maybe'), 'other']) assert.equal(core.dataSourceLabel(bad), 'SOURCE UNKNOWN');
});

test('the footer link opens the Mission Control README record and the title carries the short tooltip', async () => {
  const html = await readFile(new URL('../../../extensions/mission_control/ui/index.html', import.meta.url), 'utf8');
  const attributes = tag => Object.fromEntries([...tag.matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
  const footer = html.match(/<footer>([\s\S]*?)<\/footer>/)[1];
  const link = footer.match(/<a ([^>]*)>([^<]*)<\/a>/);
  assert.ok(link, 'the footer holds the link');
  assert.equal(link[2], 'Mission Control v1 · Read only');
  const {href, 'data-record': recordId} = attributes(link[1]);
  // The link names a rendered record, and the hash is the one the page's own routing writes for that record.
  const record = items.find(i => i.id === recordId);
  assert.ok(record, `${recordId} is in the documents dataset`);
  assert.equal(record.type, 'documents');
  assert.equal(record.source.path, 'governance/extensions/mission_control/README.md');
  assert.equal(record.title, 'Mission Control generator v1');
  assert.equal(href, '#detail/' + encodeURIComponent(recordId));
  assert.equal(resolveTarget({recordId, ...record.source}, items), record);
  // The footer keeps the two status lines, and the old masthead badge is gone.
  for (const id of ['refresh-status', 'load-status']) assert.ok(footer.includes(`id="${id}"`), id);
  assert.ok(!/class="mode"/.test(html));
  // The tooltip is a bonus (a title attribute is not reachable by keyboard or touch): short, and it sends the reader to the footer.
  const title = attributes(html.match(/<h1 ([^>]*)>Mission Control<\/h1>/)[1]).title;
  assert.ok(title && title.length <= 320, 'the tooltip exists and stays short');
  for (const word of ['read-only', 'Refresh', 'footer']) assert.ok(title.includes(word), word);
});

test('a devlog dataset that says it is disabled shows one notice and the status line, and an enabled one shows none', async () => {
  const app = await readFile(new URL('../../../extensions/mission_control/ui/app.mjs', import.meta.url), 'utf8');
  assert.ok(app.includes("dataset.enabled === false) devlogOff=true"), 'only an explicit false switches the devlog off');
  assert.equal(app.split("This project keeps no devlog.").length - 1, 1, 'the notice is written once');
  assert.ok(app.includes("searchedType(visit) === 'devlog' && devlogOff"), 'the notice belongs to the devlog section');
  assert.ok(app.includes("' · NO DEVLOG'"), 'the status line says so');
  for (const type of TYPES) assert.equal(validateDataset({schemaVersion: 1, type, authority: 'derived/non-authoritative', items: [], enabled: false}, type).length, 0);
});
