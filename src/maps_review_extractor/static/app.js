const form = document.querySelector('#job-form');
const fileInput = document.querySelector('#file');
const fileLabel = document.querySelector('#file-label');
const dropzone = document.querySelector('#dropzone');
const startButton = document.querySelector('#start');
const statusText = document.querySelector('#status');
const message = document.querySelector('#message');
const progress = document.querySelector('#progress');
const previewSection = document.querySelector('#preview-section');
const previewTbody = document.querySelector('#preview-tbody');
const previewSubtitle = document.querySelector('#preview-subtitle');
const previewDownload = document.querySelector('#preview-download');
const reviewsDownload = document.querySelector('#reviews-download');

let activeJob = null;
let pollTimer = null;
let mode = 'single';

document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
  mode = tab.dataset.mode;
  document.querySelectorAll('.tab').forEach(item => item.classList.toggle('active', item === tab));
  document.querySelector('#single-mode').hidden = mode !== 'single';
  document.querySelector('#bulk-mode').hidden = mode !== 'bulk';
}));

fileInput.addEventListener('change', () => { fileLabel.textContent = fileInput.files[0]?.name || 'Choose CSV or Excel file'; });
['dragenter', 'dragover'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.add('dragging'); }));
['dragleave', 'drop'].forEach(name => dropzone.addEventListener(name, event => { event.preventDefault(); dropzone.classList.remove('dragging'); }));
dropzone.addEventListener('drop', event => { if (event.dataTransfer.files.length) { fileInput.files = event.dataTransfer.files; fileInput.dispatchEvent(new Event('change')); } });

function resetControls() { startButton.disabled = false; }
function showError(text) { statusText.textContent = 'Failed'; message.textContent = text; progress.className = ''; progress.style.width = '100%'; progress.style.background = '#c75449'; resetControls(); }

function escapeHtml(str) {
  return String(str || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function renderPreview(jobId, downloadUrl) {
  try {
    const res = await fetch(`/api/jobs/${jobId}/preview`);
    if (!res.ok) return;
    const data = await res.json();
    const reviews = data.reviews || [];
    const total = data.total || reviews.length;

    previewTbody.innerHTML = '';
    if (reviews.length === 0) {
      previewTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:20px; color:#65706a;">No reviews extracted.</td></tr>';
    } else {
      reviews.forEach((r, idx) => {
        const ratingNum = parseFloat(r.rating) || 0;
        let ratingClass = '';
        if (ratingNum >= 4) ratingClass = 'high';
        else if (ratingNum <= 2 && ratingNum > 0) ratingClass = 'low';

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><strong>${idx + 1}</strong></td>
          <td><strong>${escapeHtml(r.company || '-')}</strong><br><small style="color:#65706a;">${escapeHtml(r.address || '')}</small></td>
          <td><strong>${escapeHtml(r.reviewer || 'Anonymous')}</strong></td>
          <td><span class="rating-badge ${ratingClass}">${r.rating ? `⭐ ${escapeHtml(r.rating)}` : 'N/A'}</span></td>
          <td style="color:#65706a; font-size:12px;">${escapeHtml(r.date || '-')}</td>
          <td>${r.review ? escapeHtml(r.review) : '<em style="color:#8f9893;">(Rating only / no text)</em>'}</td>
          <td>${r.owner_reply ? `<div class="owner-reply-text"><strong>Owner:</strong> ${escapeHtml(r.owner_reply)}</div>` : '<span style="color:#8f9893;">-</span>'}</td>
        `;
        previewTbody.appendChild(tr);
      });
    }

    previewSubtitle.textContent = `Showing ${reviews.length} of ${total} extracted rows. Verify the preview below before downloading.`;
    if (downloadUrl) {
      previewDownload.href = downloadUrl;
      previewDownload.hidden = false;
    }
    previewSection.hidden = false;
    previewSection.scrollIntoView({ behavior: 'smooth' });
  } catch (err) {
    console.error('Failed to load preview', err);
  }
}

async function poll() {
  const response = await fetch(`/api/jobs/${activeJob}`);
  const job = await response.json();
  statusText.textContent = job.status;
  message.textContent = job.message || '';
  document.querySelector('#rows').textContent = job.rows ?? '-';
  document.querySelector('#matched').textContent = job.matched ?? '-';
  document.querySelector('#reviews').textContent = job.reviews ?? '-';

  const incomplete = document.querySelector('#incomplete');
  incomplete.hidden = !job.incomplete_companies;
  incomplete.textContent = job.incomplete_companies ? `${job.incomplete_companies} company result(s) may be incomplete.` : '';

  if (['preparing', 'running', 'processing'].includes(job.status)) {
    progress.className = 'working';
    pollTimer = setTimeout(poll, 1500);
    return;
  }

  progress.className = '';
  progress.style.width = '100%';
  resetControls();

  if (job.status === 'complete') {
    progress.style.background = '#9ddb41';
    reviewsDownload.href = job.download;
    reviewsDownload.hidden = false;
    renderPreview(activeJob, job.download);
  } else {
    showError(job.message || 'Job failed.');
  }
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (mode === 'single' && !document.querySelector('#company').value.trim()) { showError('Enter a company name.'); return; }
  if (mode === 'bulk' && !fileInput.files.length) { showError('Choose a CSV or Excel file.'); return; }

  previewSection.hidden = true;
  reviewsDownload.hidden = true;
  previewDownload.hidden = true;
  document.querySelector('#incomplete').hidden = true;
  startButton.disabled = true;
  statusText.textContent = 'Starting';
  message.textContent = 'Opening local Chrome...';
  progress.style = '';
  progress.className = 'working';

  const data = new FormData(form);
  if (mode === 'single') data.delete('file');
  else { data.delete('company'); data.delete('location'); }

  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: data });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The job could not be started.');
    activeJob = result.job_id;
    poll();
  } catch (error) {
    showError(error.message);
  }
});
