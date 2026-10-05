// Weldment BOM palette (Phase 4b / B2).
//
// Python pushes the whole view model with sendInfoToHTML('render', json); we
// rebuild both tables from it. Edits post back with adsk.fusionSendData(action,
// json) keyed by mid/jid; Python mutates the registry and re-renders.
//
// The panel is the frame's *history*: every member row shows the numbers the
// builder used and every joint row shows its kind + editable parameters, so a
// change here is a change to the record (which the tools then read back).

// Every joint kind the engine or a toolbox tool can record, with a label. The
// dropdown must contain a joint's current kind or the select would silently
// render (and snap) to the first option -- so we cover all of them.
const JOINT_KINDS = [
    { value: 'none', label: 'none' },
    { value: 'butt', label: 'Butt' },
    { value: 'saddle', label: 'Saddled butt' },
    { value: 'through', label: 'Through butt' },
    { value: 'butt_saddle', label: 'Saddled butt' },
    { value: 'miter', label: 'Miter' },
    { value: 'cope', label: 'Cope' },
    { value: 'cope_end', label: 'Cope (end)' },
    { value: 'cope_t', label: 'Cope (T)' },
    { value: 'cope_angle', label: 'Cope (angled)' },
    { value: 'bend', label: 'Bend' },
];

// Which numeric parameters each kind exposes for editing. depth_mm is how deep
// a cope/saddle saddles into the neighbour; clr_mm is a bend's centre-line
// radius. Rendering a fixed set (not just the keys already present) lets the
// user ADD a parameter a joint was built without.
const KIND_PARAMS = {
    cope: ['depth_mm'], cope_end: ['depth_mm'], cope_t: ['depth_mm'],
    cope_angle: ['depth_mm'], saddle: ['depth_mm'], butt_saddle: ['depth_mm'],
    through: [], butt: [], miter: [],
    bend: ['clr_mm'],
};

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

function kindOptions(kind) {
    // Ensure the joint's current kind is selectable even if it is not in the
    // canonical list (a future kind, or a hand-edited value).
    let opts = JOINT_KINDS.slice();
    if (!opts.some((o) => o.value === kind)) {
        opts = opts.concat([{ value: kind, label: kind }]);
    }
    return opts.map((o) =>
        `<option value="${o.value}"${o.value === kind ? ' selected' : ''}>` +
        `${esc(o.label)}</option>`).join('');
}

function paramCells(j) {
    const keys = KIND_PARAMS[j.kind] || [];
    if (keys.length === 0) return '<span class="muted">—</span>';
    return keys.map((k) => {
        const v = (j.params && j.params[k] != null) ? j.params[k] : '';
        return `<label class="param">${k.replace(/_/g, ' ')} ` +
            `<input type="number" step="0.1" value="${esc(v)}" ` +
            `onchange="editJointParam(${j.jid}, '${k}', this.value)"></label>`;
    }).join(' ');
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
            `<td><input type="text" value="${esc(m.name || '')}" ` +
            `onchange="editMember(${m.mid}, 'name', this.value)"></td>` +
            `<td><input type="text" value="${esc(m.designation || '')}" ` +
            `onchange="editMember(${m.mid}, 'designation', this.value)"></td>` +
            `<td>${esc(m.family || '')}</td>` +
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
            `<td><select onchange="editJointKind(${j.jid}, this.value)">` +
            `${kindOptions(j.kind)}</select></td>` +
            `<td>${(j.ref_names || j.refs || []).map(esc).join(' &rarr; ')}</td>` +
            `<td class="params">${paramCells(j)}</td>`;
        jt.appendChild(tr);
    });
    setStatus('');
}

function editMember(mid, field, value) {
    const payload = { mid: mid };
    payload[field] = value;
    adsk.fusionSendData('editMember', JSON.stringify(payload))
        .then((r) => setStatus(r));
}

function editJointKind(jid, kind) {
    adsk.fusionSendData('editJointKind', JSON.stringify({ jid: jid, kind: kind }))
        .then((r) => setStatus(r));
}

function editJointParam(jid, key, value) {
    const v = (value === '' || value == null) ? 0 : parseFloat(value);
    adsk.fusionSendData('editJointParam',
        JSON.stringify({ jid: jid, key: key, value: v }))
        .then((r) => setStatus(r));
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
