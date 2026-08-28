const $ = (s, root = document) => root.querySelector(s);

const KIND_LABEL = {
  keyframe: '關鍵幀',
  motion_test: '動作試拍',
  clip: '正式生成',
  clip_1080p: '1080p 升頻',
  loop: '64 秒循環',
  final: '最終成片',
};
const ROLE_LABEL = { shared: '共用', sleep: '睡覺搖尾巴', lookup: '抬頭看主人' };
const STATUS_LABEL = {
  queued: '排隊中', running: '生成中', ready: '完成', awaiting_review: '待審核',
  approved: '已核准', rejected: '已退回', superseded: '已被取代', failed: '失敗',
};

async function api(path, options) {
  const resp = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || resp.statusText);
  }
  return resp.status === 204 ? null : resp.json();
}

let pollTimer = null;

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function showListView() {
  stopPolling();
  $('#episode-list-view').classList.remove('hidden');
  $('#episode-detail-view').classList.add('hidden');
  loadEpisodeList();
}

async function loadEpisodeList() {
  const episodes = await api('/api/episodes');
  $('#episodeList').innerHTML = episodes.map((ep) => `
    <div class="episode-row" data-id="${ep.id}">
      <strong>${ep.title}</strong> <code>${ep.slug}</code>
      <span class="badge">${ep.status}</span>
    </div>
  `).join('') || '<p>還沒有集數。</p>';
  document.querySelectorAll('.episode-row').forEach((row) => {
    row.onclick = () => openEpisode(Number(row.dataset.id));
  });
}

function artifactElement(asset) {
  if (!asset.path) return '<span class="muted">尚未產出</span>';
  const src = `/api/artifact/${asset.id}`;
  return asset.kind === 'keyframe'
    ? `<img src="${src}" alt="keyframe">`
    : `<video src="${src}" controls preload="metadata"></video>`;
}

function assetCard(asset) {
  const reviewButtons = asset.status === 'awaiting_review' ? `
    <button class="approve" data-id="${asset.id}" data-version="${asset.state_version}">核准</button>
    <button class="reject" data-id="${asset.id}" data-version="${asset.state_version}">退回重做</button>
  ` : '';
  const error = asset.error ? `<p class="error">${asset.error}</p>` : '';
  return `
    <div class="asset-card">
      <div class="asset-meta">
        <span class="role">${ROLE_LABEL[asset.role] || asset.role}</span>
        <span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>
        ${asset.variant_index ? `<span class="muted">v${asset.variant_index + 1}</span>` : ''}
      </div>
      ${artifactElement(asset)}
      ${error}
      ${reviewButtons}
    </div>
  `;
}

let lastRenderedSnapshot = null;

async function openEpisode(episodeId) {
  $('#episode-list-view').classList.add('hidden');
  $('#episode-detail-view').classList.remove('hidden');
  lastRenderedSnapshot = null;
  await renderEpisode(episodeId);
  // Assets flip queued -> running -> awaiting_review in the background
  // (ComfyUI generation, no websocket push), so poll while this episode is
  // open instead of asking the reviewer to guess when to refresh.
  stopPolling();
  pollTimer = setInterval(() => renderEpisode(episodeId), 3000);
}

async function renderEpisode(episodeId) {
  const data = await api(`/api/episodes/${episodeId}`);

  // Skip the redraw entirely when nothing changed since the last poll --
  // rebuilding the asset cards on every tick would tear down and recreate
  // any <video> the reviewer currently has playing, resetting it to 0:00
  // every 3s and making playback look broken.
  const snapshot = JSON.stringify(data);
  if (snapshot === lastRenderedSnapshot) return;
  lastRenderedSnapshot = snapshot;

  $('#episodeTitle').textContent = `${data.episode.title}（${data.episode.status}）`;

  const groups = {};
  for (const asset of data.assets) {
    (groups[asset.kind] = groups[asset.kind] || []).push(asset);
  }
  const order = ['keyframe', 'motion_test', 'clip', 'clip_1080p', 'loop', 'final'];
  $('#assetGroups').innerHTML = order
    .filter((kind) => groups[kind])
    .map((kind) => `
      <div class="card">
        <h3>${KIND_LABEL[kind]}</h3>
        <div class="asset-row">${groups[kind].map(assetCard).join('')}</div>
      </div>
    `).join('');

  document.querySelectorAll('.asset-card img').forEach((img) => {
    img.onclick = () => openLightbox(img.src);
  });
  document.querySelectorAll('.approve').forEach((btn) => {
    btn.onclick = () => decide(episodeId, btn.dataset.id, 'approve', { expected_version: Number(btn.dataset.version) });
  });
  document.querySelectorAll('.reject').forEach((btn) => {
    btn.onclick = () => {
      const reason = prompt('退回原因（可留空）：') || '';
      decide(episodeId, btn.dataset.id, 'reject', { expected_version: Number(btn.dataset.version), reason });
    };
  });
}

function openLightbox(src) {
  $('#lightboxImg').src = src;
  $('#lightbox').classList.remove('hidden');
}

$('#lightbox').onclick = () => $('#lightbox').classList.add('hidden');

async function decide(episodeId, assetId, action, body) {
  try {
    await api(`/api/episodes/${episodeId}/assets/${assetId}/${action}`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    await renderEpisode(episodeId);
  } catch (err) {
    alert(err.message);
    await renderEpisode(episodeId);
  }
}

$('#createEpisode').onclick = async () => {
  const slug = $('#newSlug').value.trim();
  const title = $('#newTitle').value.trim();
  if (!slug || !title) {
    $('#createNotice').textContent = '請填 slug 和標題';
    return;
  }
  try {
    await api('/api/episodes', { method: 'POST', body: JSON.stringify({ slug, title }) });
    $('#newSlug').value = '';
    $('#newTitle').value = '';
    $('#createNotice').textContent = '';
    loadEpisodeList();
  } catch (err) {
    $('#createNotice').textContent = err.message;
  }
};

$('#backToList').onclick = showListView;

showListView();
