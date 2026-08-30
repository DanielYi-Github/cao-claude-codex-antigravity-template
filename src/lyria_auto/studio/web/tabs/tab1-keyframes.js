// Tab 1: keyframe generation, prompt editing, and candidate approval.
//
// Receives a shared `ctx` object from app.js instead of importing app.js's
// internals directly -- this keeps every tab module self-contained, so
// adding tabs 2-5 later never means two agents editing the same file at
// the same time (studio-console-v2-plan.md 7.0). `ctx.getEpisodeId()` and
// `ctx.refresh()` are functions, not snapshotted values, because the
// current episode can change between when ctx is built and when a handler
// in here actually runs.

// Cached across every episode after the first fetch -- these are global
// module constants/config (src/lyria_auto/studio/stages.py's DEFAULT_
// KEYFRAME_PROMPT/KEYFRAME_NEGATIVE_PROMPT plus config/settings.yaml's
// studio.keyframe_batch_size), not per-episode data, so one page load only
// ever needs to ask the backend for them once.
let _cachedDefaults = null;

async function _fetchDefaultsOnce(ctx) {
  if (!_cachedDefaults) {
    _cachedDefaults = await ctx.api('/api/keyframes/defaults');
  }
  return _cachedDefaults;
}

function _selectedBatchSize($) {
  const active = $('.batch-size-btn.active');
  return active ? Number(active.dataset.size) : null;
}

function _selectBatchSize($$, size) {
  $$('.batch-size-btn').forEach(btn => {
    btn.classList.toggle('active', Number(btn.dataset.size) === size);
  });
}

export async function renderKeyframeTab(data, ctx) {
  const { $, $$, STATUS_LABEL } = ctx;
  const keyframes = data.assets.filter(a => a.kind === 'keyframe');
  const activeKeyframes = keyframes.filter(a => a.status !== 'superseded');

  const hasApproved = activeKeyframes.some(a => a.status === 'approved');
  const isGenerating = data.tasks.some(t => t.task_type === 'generate_keyframe' && ['queued', 'running'].includes(t.status));
  const hasCandidates = activeKeyframes.some(a => a.status === 'awaiting_review');

  // Show what prompt actually produced the candidates below, and let it
  // double as the starting point for an edit -> Regenerate All. Only fills
  // blank fields so it never clobbers text the reviewer is mid-typing.
  const latestKeyframeTask = data.tasks
    .filter(t => t.task_type === 'generate_keyframe')
    .slice(-1)[0];
  if (latestKeyframeTask && latestKeyframeTask.payload_json) {
    const payload = JSON.parse(latestKeyframeTask.payload_json);
    if (!$('#posPrompt').value && payload.positive_prompt) {
      $('#posPrompt').value = payload.positive_prompt;
    }
    if (!$('#negPrompt').value && payload.negative_prompt) {
      $('#negPrompt').value = payload.negative_prompt;
    }
    if (_selectedBatchSize($) == null && payload.batch_size) {
      _selectBatchSize($$, payload.batch_size);
    }
  } else if (!$('#posPrompt').value || !$('#negPrompt').value || _selectedBatchSize($) == null) {
    // A brand-new episode has no generate_keyframe task yet, so there's no
    // payload_json to read "what actually produced these candidates" from
    // -- without this, the boxes/buttons just stayed blank until after the
    // first Generate click, with nothing to see or edit beforehand (user-
    // reported gap, 2026-08-31). Falls back to the same defaults the
    // backend itself falls back to when a field/batch_size is left unset.
    try {
      const defaults = await _fetchDefaultsOnce(ctx);
      if (!$('#posPrompt').value) $('#posPrompt').value = defaults.positive_prompt;
      if (!$('#negPrompt').value) $('#negPrompt').value = defaults.negative_prompt;
      if (_selectedBatchSize($) == null) _selectBatchSize($$, defaults.batch_size);
    } catch {
      // Non-critical -- worst case the boxes/buttons stay blank until
      // Generate succeeds once and the branch above takes over.
    }
  }

  // Update buttons. Once a keyframe is approved the backend rejects any
  // further regenerate call (409) -- don't offer a button that always errors.
  $('#generateKeyframes').disabled = isGenerating;
  $('#regenerateKeyframes').disabled = isGenerating || hasApproved;
  $$('.batch-size-btn').forEach(btn => { btn.disabled = isGenerating || hasApproved; });

  if (hasApproved) {
    $('#generateKeyframes').classList.add('hidden');
    $('#regenerateKeyframes').classList.add('hidden');
  } else if (hasCandidates || isGenerating) {
    $('#generateKeyframes').classList.add('hidden');
    $('#regenerateKeyframes').classList.remove('hidden');
  } else {
    $('#generateKeyframes').classList.remove('hidden');
    $('#regenerateKeyframes').classList.add('hidden');
  }
  // Only offer Stop while something's actually running -- there's nothing
  // to cancel otherwise.
  $('#cancelKeyframes').classList.toggle('hidden', !isGenerating);

  // Update status
  let statusText = '';
  if (isGenerating) statusText = '正在生成關鍵幀...';
  else if (hasApproved) statusText = '已選定關鍵幀，可繼續前往 Tab 2。';
  else if (hasCandidates) statusText = '請選擇一個關鍵幀，或重新生成。';

  if ($('#keyframeStatus').textContent !== statusText) {
    $('#keyframeStatus').textContent = statusText;
  }

  // Render Grid
  const grid = $('#keyframe-grid');
  grid.innerHTML = activeKeyframes.map(asset => {
    const isApproved = asset.status === 'approved';
    // While isGenerating, some candidates from the same batch may have
    // published already (awaiting_review) while siblings are still coming --
    // the backend now 409s an approve attempt mid-batch, so don't show a
    // button that would just error.
    const isAwaiting = asset.status === 'awaiting_review' && !isGenerating;
    const error = asset.error ? `<p class="error">${asset.error}</p>` : '';

    // Only allow clicking if awaiting review
    let reviewOverlay = '';
    if (isAwaiting) {
      reviewOverlay = `<button class="approve-btn" data-id="${asset.id}" data-version="${asset.state_version}">選擇此關鍵幀</button>`;
    } else if (isApproved) {
      reviewOverlay = `<div class="approved-badge">已選定</div>`;
    }

    return `
      <div class="kf-card ${isApproved ? 'approved' : ''}">
        <div class="kf-meta">
          <span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>
          ${asset.variant_index ? `<span class="muted">v${asset.variant_index + 1}</span>` : ''}
        </div>
        ${asset.path ? `<img src="/api/artifact/${asset.id}" alt="Keyframe candidate" class="kf-img">` : '<div class="placeholder-img">生成中...</div>'}
        ${error}
        <div class="kf-actions">${reviewOverlay}</div>
      </div>
    `;
  }).join('') || '<p class="muted">尚未生成關鍵幀</p>';

  // Bind Lightbox
  $$('.kf-img', grid).forEach(img => {
    img.onclick = () => ctx.openLightbox(img.src);
  });

  // Bind Approve
  $$('.approve-btn', grid).forEach(btn => {
    btn.onclick = () => approveKeyframe(ctx, btn.dataset.id, btn.dataset.version);
  });
}

export function bindKeyframeTab(ctx) {
  const { $, $$ } = ctx;
  $('#generateKeyframes').onclick = () => generateKeyframes(ctx);
  $('#regenerateKeyframes').onclick = () => generateKeyframes(ctx);
  $('#cancelKeyframes').onclick = () => cancelKeyframes(ctx);
  $$('.batch-size-btn').forEach(btn => {
    btn.onclick = () => _selectBatchSize($$, Number(btn.dataset.size));
  });
}

async function approveKeyframe(ctx, assetId, expectedVersion) {
  const { $, api, refresh } = ctx;
  try {
    $('#keyframeStatus').textContent = '正在核准...';
    await api(`/api/episodes/${ctx.getEpisodeId()}/assets/${assetId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(expectedVersion) }),
    });
    // Let polling refresh the UI and mark siblings as superseded
    await refresh();
  } catch (err) {
    alert(err.message);
    await refresh();
  }
}

async function generateKeyframes(ctx) {
  const { $, $$, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const pos = $('#posPrompt').value.trim();
  const neg = $('#negPrompt').value.trim();
  const body = {};
  if (pos) body.positive_prompt = pos;
  if (neg) body.negative_prompt = neg;
  const batchSize = _selectedBatchSize($);
  if (batchSize) body.batch_size = batchSize;

  try {
    $('#keyframeStatus').textContent = '開始生成...';
    $('#generateKeyframes').disabled = true;
    $('#regenerateKeyframes').disabled = true;
    $$('.batch-size-btn').forEach(btn => { btn.disabled = true; });
    await api(`/api/episodes/${episodeId}/keyframes/generate`, {
      method: 'POST',
      body: JSON.stringify(body)
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    $('#generateKeyframes').disabled = false;
    $('#regenerateKeyframes').disabled = false;
    $$('.batch-size-btn').forEach(btn => { btn.disabled = false; });
  }
}

async function cancelKeyframes(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  try {
    $('#cancelKeyframes').disabled = true;
    $('#keyframeStatus').textContent = '正在終止...';
    await api(`/api/episodes/${episodeId}/keyframes/cancel`, { method: 'POST' });
    await refresh();
  } catch (err) {
    alert(err.message);
    await refresh();
  } finally {
    $('#cancelKeyframes').disabled = false;
  }
}
