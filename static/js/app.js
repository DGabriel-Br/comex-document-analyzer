const state = { sessionId: null };

async function ensureSession() {
  if (state.sessionId) return state.sessionId;
  const res = await fetch('/api/session', { method: 'POST' });
  const data = await res.json();
  state.sessionId = data.session_id;
  return state.sessionId;
}

function getCard(docType) {
  return document.querySelector(`.card[data-doc="${docType}"]`);
}

async function processDocument(docType) {
  const card = getCard(docType);
  const input = card.querySelector('input[type="file"]');
  const output = card.querySelector('.json-output');

  if (!input.files?.length) {
    output.textContent = 'Selecione um PDF antes de processar.';
    return;
  }

  const sessionId = await ensureSession();
  const formData = new FormData();
  formData.append('session_id', sessionId);
  formData.append('file', input.files[0]);

  output.textContent = 'Processando PDF e extraindo dados...';

  const res = await fetch(`/api/process/${docType}`, {
    method: 'POST',
    body: formData,
  });

  const data = await res.json();
  if (!res.ok) {
    output.textContent = data.error || 'Erro ao processar documento.';
    return;
  }

  output.textContent = JSON.stringify(data.document, null, 2);
}

function renderAnalysis(result) {
  const status = document.getElementById('status');
  status.textContent = `Status da análise: ${result.status}`;
  status.className = result.divergences.length ? 'warn' : 'ok';

  const tbody = document.querySelector('#compare-table tbody');
  tbody.innerHTML = '';

  result.matrix.forEach((row) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${row.field || ''}</td>
      <td>${row.invoice || ''}</td>
      <td>${row.packing_list || ''}</td>
      <td>${row.bl || ''}</td>
    `;
    tbody.appendChild(tr);
  });

  const ul = document.getElementById('divergences');
  ul.innerHTML = '';
  if (!result.divergences.length) {
    const li = document.createElement('li');
    li.textContent = 'Nenhuma divergência identificada.';
    ul.appendChild(li);
    return;
  }
  result.divergences.forEach((item) => {
    const li = document.createElement('li');
    li.textContent = item;
    ul.appendChild(li);
  });
}

async function analyze() {
  const sessionId = await ensureSession();
  const res = await fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  });
  const data = await res.json();

  if (!res.ok) {
    alert(data.error || 'Erro na análise crítica.');
    return;
  }
  renderAnalysis(data);
}

async function downloadReport() {
  const sessionId = await ensureSession();
  window.location.href = `/api/report/${sessionId}`;
}

['invoice', 'packing_list', 'bl'].forEach((docType) => {
  getCard(docType)
    .querySelector('.process-btn')
    .addEventListener('click', () => processDocument(docType));
});

document.getElementById('analyze-btn').addEventListener('click', analyze);
document.getElementById('download-btn').addEventListener('click', downloadReport);
