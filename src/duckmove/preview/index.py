from __future__ import annotations

import html as _html
import json as _json
from string import Template
from typing import Any, Dict, Iterable, List

INDEX_TEMPLATE = Template("""<!doctype html>
<html><head><meta charset='utf-8'><title>duckmove preview</title>
<style>body{font-family:system-ui,sans-serif;margin:20px} table{border-collapse:collapse;width:100%;table-layout:fixed} th,td{padding:10px 8px;border-bottom:1px solid #eee;text-align:left;vertical-align:middle} th{font-weight:600;background:#fff;position:sticky;top:0} a{color:#0a58ca;text-decoration:none} .muted{opacity:.7;font-size:12px} .col-created{width:26%} .col-type{width:10%} .col-link{width:40%} .col-name{width:14%} .col-dsid{width:10%;font-family:ui-monospace,Consolas,monospace} .actions button{margin-right:6px;padding:4px 8px;border:1px solid #ddd;background:#f7f7f7;border-radius:4px;cursor:pointer} .controls{display:flex;gap:10px;align-items:center;margin:10px 0 12px} .controls input[type='text']{padding:6px 8px;border:1px solid #ddd;border-radius:4px;min-width:220px} .pill{display:inline-block;padding:2px 8px;border:1px solid #e0e0e0;border-radius:999px;font-size:12px;margin-left:8px} th.sortable{cursor:pointer;user-select:none} th .ind{opacity:.6;margin-left:4px}</style>
</head><body>
<h2 style='margin:0 0 4px'>duckmove preview</h2>
<div class='muted' style='margin-bottom:4px'>Updated <span id='updatedAt' data-iso='$created'></span> <span id='updatedRel' class='muted'></span><span class='pill' id='tzBadge'>Local</span></div>
<div class='actions' style='margin-bottom:6px'><button onclick="clearAll()">Clear All</button> <button onclick="prune()">Prune Missing</button> <button onclick="refresh()">Refresh</button></div>
<div class='controls'><label>Sort <select id='sortSel'><option value='created_desc'>Newest</option><option value='created_asc'>Oldest</option><option value='type_asc'>Type A-Z</option><option value='type_desc'>Type Z-A</option><option value='name_asc'>Name A-Z</option><option value='name_desc'>Name Z-A</option></select></label><span style='margin-left:12px'><button id='toggleTime' title='Toggle time zone'>Time: <span id='timeMode'>Local</span></button></span><input id='filterBox' type='text' placeholder='Filter by type, name, file...'></div>
<table>
<colgroup><col class='col-created'><col class='col-type'><col class='col-link'><col class='col-name'><col class='col-dsid'></colgroup>
<thead><tr><th class='sortable' data-key='created'>Created <span class='ind'></span></th><th class='sortable' data-key='type'>Type <span class='ind'></span></th><th>Link</th><th class='sortable' data-key='name'>Name <span class='ind'></span></th><th>Dataset</th></tr></thead><tbody id='rows'>
$rows
</tbody></table>
<script>function copy(t){if(navigator.clipboard){navigator.clipboard.writeText(t);}}</script>
<script>function refresh(){location.href='/?t='+Date.now();}</script>
<script>function delItem(el,f){const row=el&&el.closest?el.closest('tr'):null;const label=row?(row.getAttribute('data-name')||row.getAttribute('data-file')||f):f;if(!window.confirm('Delete '+(label||'this item')+'?')){return;}if(row){row.style.opacity='0.5';}fetch('/api/delete?file='+encodeURIComponent(f),{method:'POST'}).then(r=>{if(r.ok){if(row){row.remove();}}else{if(row){row.style.opacity='';}refresh();}}).catch(()=>{if(row){row.style.opacity='';}refresh();});}</script>
<script>function prune(){fetch('/api/prune',{method:'POST'}).then(()=>{refresh();});}</script>
<script>function renameItem(f,cur){var n=prompt('Name',cur||''); if(n!=null){fetch('/api/rename?file='+encodeURIComponent(f)+'&name='+encodeURIComponent(n),{method:'POST'}).then(()=>{refresh();});}}</script>
<script>function clearAll(){fetch('/api/clear',{method:'POST'}).then(()=>{refresh();});}</script>
<script>
(function(){
  const rowsTbody=document.getElementById('rows');
  const filterBox=document.getElementById('filterBox');
  const sortSel=document.getElementById('sortSel');
  const timeModeSpan=document.getElementById('timeMode');
  const tzBadge=document.getElementById('tzBadge');
  const updatedAt=document.getElementById('updatedAt');
  const updatedRel=document.getElementById('updatedRel');
  const ths=[...document.querySelectorAll('th.sortable')];
  const indFor={created:ths[0]?.querySelector('.ind'), type:ths[1]?.querySelector('.ind'), name:ths[3]?.querySelector('.ind')};
  function isLocal(){return (localStorage.getItem('dm_time_mode')||'local')!=='utc';}
  function setLocal(v){localStorage.setItem('dm_time_mode', v?'local':'utc');}
  function fmt(iso){try{const d=new Date(iso);return isLocal()?d.toLocaleString():d.toISOString();}catch(e){return iso}}
  function rel(iso){try{const t=new Date(iso).getTime();const s=Math.max(0,(Date.now()-t)/1000); if(s<30) return 'just now'; if(s<90) return '1m ago'; const m=Math.floor(s/60); if(m<60) return m+'m ago'; const h=Math.floor(m/60); if(h<24) return h+'h ago'; const d=Math.floor(h/24); if(d===1) return 'yesterday'; if(d<30) return d+'d ago'; const mo=Math.floor(d/30); return mo+'mo ago';}catch(e){return ''}}
  function renderTimes(){document.querySelectorAll('.time').forEach(el=>{const iso=el.getAttribute('data-iso'); if(iso){el.textContent=fmt(iso); el.title='UTC: '+new Date(iso).toISOString();}}); document.querySelectorAll('.rel').forEach(el=>{const iso=el.getAttribute('data-iso'); el.textContent=rel(iso)}); if(updatedAt){const iso=updatedAt.getAttribute('data-iso'); updatedAt.textContent=fmt(iso); if(updatedRel) updatedRel.textContent='('+rel(iso)+')';} timeModeSpan.textContent=isLocal()?'Local':'UTC'; tzBadge.textContent=timeModeSpan.textContent;}
  function cmp(a,b,dir){return dir==='asc' ? (a<b?-1:a>b?1:0) : (a>b?-1:a<b?1:0);}
  function currentSort(){const [k,d]=sortSel.value.split('_'); return {k,d};}
  function setSort(k,d){sortSel.value=k+'_'+d; updateInd(k,d); sortRows();}
  function updateInd(k,d){Object.keys(indFor).forEach(key=>{const el=indFor[key]; if(!el) return; el.textContent = (key===k ? (d==='asc'?'^':'v') : '');});}
  function sortRows(){const s=currentSort(); const rows=[...rowsTbody.querySelectorAll('tr')]; rows.sort((ra,rb)=>{if(s.k==='created'){return cmp(new Date(ra.dataset.created), new Date(rb.dataset.created), s.d);} if(s.k==='type'){return cmp((ra.dataset.type||'').toLowerCase(), (rb.dataset.type||'').toLowerCase(), s.d);} if(s.k==='name'){return cmp((ra.dataset.name||'').toLowerCase(), (rb.dataset.name||'').toLowerCase(), s.d);} return 0;}); rows.forEach(r=>rowsTbody.appendChild(r));}
  function applyFilter(){const q=(filterBox.value||'').toLowerCase(); [...rowsTbody.querySelectorAll('tr')].forEach(tr=>{const t=(tr.dataset.type||'').toLowerCase(); const n=(tr.dataset.name||'').toLowerCase(); const f=(tr.dataset.file||'').toLowerCase(); tr.style.display = (t.includes(q)||n.includes(q)||f.includes(q)) ? '' : 'none';});}
  document.getElementById('toggleTime').addEventListener('click',()=>{setLocal(!isLocal()); renderTimes();});
  sortSel.addEventListener('change', ()=>{const [k,d]=sortSel.value.split('_'); updateInd(k,d); sortRows();});
  filterBox.addEventListener('input', applyFilter);
  ths.forEach(th=>{th.addEventListener('click',()=>{const key=th.getAttribute('data-key'); const {k,d}=currentSort(); const next = (k===key && d==='asc') ? 'desc':'asc'; setSort(key,next);});});
  renderTimes(); setSort('created','desc');
})();
</script>
</body></html>
""")

ROW_TEMPLATE = Template(
    """<tr data-created='$created' data-type='$kind' data-name='$dsname' data-file='$file'>"""
    """<td><span class='time' data-iso='$created'></span> <span class='muted rel' data-iso='$created'></span></td>"""
    """<td class='type'>$kind</td>"""
    """<td><a href='$url' target='_blank'>$file</a> <a href='$url' download>Download</a> """
    """<button onclick=\"renameItem($file_js,$dsname_js)\">Rename</button> """
    """<button onclick=\"delItem(this,$file_js)\">Delete</button></td>"""
    """<td class='name'>$dsname</td>"""
    """<td class='muted dsid'>$dsid_short</td></tr>"""
)


def _row_html(entry: Dict[str, Any]) -> str:
    meta = entry.get("meta") or {}
    dsid = str(meta.get("dataset_id") or "")
    dsname = str(meta.get("dataset_name") or "")
    file = str(entry.get("file") or "")

    def esc(v: Any) -> str:
        # Manifest values can contain user input (e.g. via /api/rename);
        # escape for HTML text and attribute contexts.
        return _html.escape(str(v), quote=True)

    def js(v: str) -> str:
        # JSON-encode for the JS string inside the onclick attribute, then
        # HTML-escape; the parser decodes entities before JS evaluation.
        return _html.escape(_json.dumps(v), quote=True)

    return ROW_TEMPLATE.substitute(
        created=esc(entry.get("created_at", "")),
        kind=esc(entry.get("kind", "")),
        file=esc(file),
        url=esc(entry.get("url", "")),
        dsname=esc(dsname),
        dsid_short=esc(dsid[:12]),
        file_js=js(file),
        dsname_js=js(dsname),
    )


def render_index_html(items: Iterable[Dict[str, Any]], created_iso: str) -> str:
    rows: List[str] = [_row_html(item) for item in items]
    rows_html = "\n".join(rows)
    return INDEX_TEMPLATE.substitute(created=created_iso, rows=rows_html)
