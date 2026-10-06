// Weldment BOM palette (Phase 4b / B2) -- a read-only bill of materials.
//
// Python pushes the whole view model with sendInfoToHTML('render', json); we
// rebuild both tables from it. The panel lists what the tools recorded; the
// tools (builder, Weld Cope/Butt/Miter/Bend) are the only write path, so there
// are no input fields here -- every cell is text. Refresh re-reads the
// registry, Rebuild re-creates the geometry from it.
//
// Member rows show the LIVE BODY NAME when Python could resolve one (the
// browser-tree name the user actually sees), falling back to the record's
// designation and finally the bare id. Joint rows use the ready-made `label`
// ("Cope (T) - Tube-1 onto Tube-2 (depth 5 mm)") instead of a kind code, so the
// list reads as sentences rather than internal vocabulary.

function setStatus(msg) {
    document.getElementById('status').textContent = msg || '';
}

function refresh() {
    adsk.fusionSendData('refresh', JSON.stringify({}));
}

function rebuild() {
    setStatus('Rebuilding...');
    adsk.fusionSendData('rebuild', JSON.stringify({}))
        .then((r) => setStatus(r));
}

// The catalogue designation is terse ("20x1" means OD x wall for a round
// tube), so prefix the family and mark a round section's diameter --
// "CHS \u00d820x1" reads as a 20 mm OD, 1 mm wall tube; "SHS 10x10x1.0" as a
// square one.
function profileText(m) {
    const fam = m.family || '';
    const des = m.designation || '';
    if (!des) return fam || '\u2014';
    return fam === 'CHS' ? `${fam} \u00d8${des}` : `${fam} ${des}`.trim();
}

function render(dataJson) {
    const data = JSON.parse(dataJson);
    if (data.error) {
        setStatus(data.error);
        return;
    }
    const members = data.members || [];
    const joints = data.joints || [];
    document.getElementById('empty').style.display =
        (members.length === 0 && joints.length === 0) ? 'block' : 'none';

    const mt = document.querySelector('#members tbody');
    mt.innerHTML = '';
    members.forEach((m) => {
        const tr = document.createElement('tr');
        tr.innerHTML =
            `<td>${m.mid}</td>` +
            `<td>${esc(m.display_name || m.name || `#${m.mid}`)}</td>` +
            `<td>${esc(profileText(m))}</td>` +
            `<td class="num" title="drawn ${m.drawn_mm} mm">${m.length_mm}</td>` +
            `<td class="num">${m.angle_deg || 0}</td>`;
        mt.appendChild(tr);
    });

    const jt = document.querySelector('#joints tbody');
    jt.innerHTML = '';
    joints.forEach((j) => {
        const tr = document.createElement('tr');
        tr.innerHTML =
            `<td>${j.jid}</td>` +
            `<td>${esc(j.label || j.kind)}</td>` +
            `<td>${(j.ref_names || j.refs || []).map(esc).join(' &rarr; ')}</td>`;
        jt.appendChild(tr);
    });
    setStatus('');
}

function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

window.fusionJavaScriptHandler = {
    handle: function (action, data) {
        if (action === 'render') {
            render(data);
        } else if (action === 'debugger') {
            debugger;
        } else {
            return `Unexpected action: ${action}`;
        }
        return 'OK';
    },
};
