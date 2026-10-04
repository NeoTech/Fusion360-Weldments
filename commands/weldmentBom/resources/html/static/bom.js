// Weldment BOM palette (Phase 4b).
//
// Python pushes the whole view model with sendInfoToHTML('render', json); we
// rebuild both tables from it. Edits post back with adsk.fusionSendData(action,
// json) keyed by mid/jid; Python mutates the registry and re-renders.

const JOINT_KINDS = ['none', 'butt', 'miter', 'cope', 'bend'];

function setStatus(msg) {
    document.getElementById('status').textContent = msg || '';
}

function refresh() {
    adsk.fusionSendData('refresh', JSON.stringify({}));
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
            `<td class="num">${m.length_mm}</td>`;
        mt.appendChild(tr);
    });

    const jt = document.querySelector('#joints tbody');
    jt.innerHTML = '';
    joints.forEach((j) => {
        const opts = JOINT_KINDS.map((k) =>
            `<option value="${k}"${k === j.kind ? ' selected' : ''}>${k}</option>`
        ).join('');
        const params = Object.entries(j.params || {})
            .map(([k, v]) => `${k}=${v}`).join(', ');
        const tr = document.createElement('tr');
        tr.innerHTML =
            `<td>${j.jid}</td>` +
            `<td><select onchange="editJointKind(${j.jid}, this.value)">${opts}</select></td>` +
            `<td>${(j.refs || []).join(', ')}</td>` +
            `<td class="params">${esc(params)}</td>`;
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
