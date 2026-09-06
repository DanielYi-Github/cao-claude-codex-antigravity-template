import { renderKeyframeTab, bindKeyframeTab } from './tabs/tab1-keyframes.js';
import { renderMotionTab, bindMotionTab } from './tabs/tab2-motion.js';
import { renderClipsTab, bindClipsTab } from './tabs/tab3-clips.js';

export const $ = (s, root = document) => root.querySelector(s);
export const $$ = (s, root = document) => root.querySelectorAll(s);

export const STATUS_LABEL = {
  queued: '排隊中', running: '生成中', ready: '完成', awaiting_review: '待審核',
  approved: '已核准', rejected: '已退回', superseded: '已被取代', failed: '失敗',
};

export async function api(path, options) {
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
let currentEpisodeId = null;

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function showListView() {
  stopPolling();
  currentEpisodeId = null;
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
  $$('.episode-row').forEach((row) => {
    row.onclick = () => openEpisode(Number(row.dataset.id));
  });
}

// Tab Switching
$$('.tab-btn').forEach(btn => {
  btn.onclick = () => {
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-content').forEach(c => c.classList.add('hidden'));
    btn.classList.add('active');
    $(`#tab-${btn.dataset.tab}`).classList.remove('hidden');
  };
});

let lastRenderedSnapshot = null;

// Shared context every per-tab module receives instead of importing this
// file's internals directly -- only app.js touches currentEpisodeId /
// renderEpisode, so tab modules stay self-contained (studio-console-v2-
// plan.md 7.0). getEpisodeId/refresh are functions, not values, since the
// open episode can change after ctx is built.
const ctx = {
  $, $$, api, STATUS_LABEL,
  getEpisodeId: () => currentEpisodeId,
  refresh: () => renderEpisode(currentEpisodeId),
  openLightbox,
};

async function openEpisode(episodeId) {
  currentEpisodeId = episodeId;
  $('#episode-list-view').classList.add('hidden');
  $('#episode-detail-view').classList.remove('hidden');
  lastRenderedSnapshot = null;

  // Reset Tab 1 UI
  $('#posPrompt').value = '';
  $('#negPrompt').value = '';
  $('#keyframeStatus').textContent = '';
  $$('.batch-size-btn').forEach(b => b.classList.remove('active'));

  // Reset Tab 2 UI
  if ($('#sleepPrompt')) $('#sleepPrompt').value = '';
  if ($('#lookupPrompt')) $('#lookupPrompt').value = '';

  await renderEpisode(episodeId);
  stopPolling();
  pollTimer = setInterval(() => renderEpisode(episodeId), 3000);
}

async function renderEpisode(episodeId) {
  if (episodeId !== currentEpisodeId) return;
  const data = await api(`/api/episodes/${episodeId}`);

  const snapshot = JSON.stringify(data);
  if (snapshot === lastRenderedSnapshot) return;
  lastRenderedSnapshot = snapshot;

  $('#episodeTitle').textContent = `${data.episode.title}（${data.episode.status}）`;

  await renderKeyframeTab(data, ctx);
  await renderMotionTab(data, ctx);
  renderClipsTab(data, ctx);
}

export function openLightbox(src) {
  $('#lightboxImg').src = src;
  $('#lightbox').showModal();
}

bindKeyframeTab(ctx);
bindMotionTab(ctx);
bindClipsTab(ctx);

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
