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
// module constants (src/lyria_auto/studio/stages.py's DEFAULT_KEYFRAME_
// PROMPT/KEYFRAME_NEGATIVE_PROMPT), not per-episode data, so one page load
// only ever needs to ask the backend for them once.
let _cachedDefaultPrompts = null;

async function _fetchDefaultPromptsOnce(ctx) {
  if (!_cachedDefaultPrompts) {
    _cachedDefaultPrompts = await ctx.api('/api/keyframes/defaults');
  }
  return _cachedDefaultPrompts;
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
  } else if (!$('#posPrompt').value || !$('#negPrompt').value) {
    // A brand-new episode has no generate_keyframe task yet, so there's no
    // payload_json to read "what actually produced these candidates" from
    // -- without this, the boxes just stayed blank until after the first
    // Generate click, with nothing to see or edit beforehand (user-reported
    // gap, 2026-08-31). Falls back to the same module-level defaults the
    // backend itself falls back to when a field is left blank.
    try {
      const defaults = await _fetchDefaultPromptsOnce(ctx);
      if (!$('#posPrompt').value) $('#posPrompt').value = defaults.positive_prompt;
      if (!$('#negPrompt').value) $('#negPrompt').value = defaults.negative_prompt;
    } catch {
      // Non-critical -- worst case the boxes stay blank until Generate
      // succeeds once and the branch above takes over.
    }
  }

  // Update buttons. Once a keyframe is approved the backend rejects any
  // further regenerate call (409) -- don't offer a button that always errors.
  $('#generateKeyframes').disabled = isGenerating;
  $('#regenerateKeyframes').disabled = isGenerating || hasApproved;

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
  const { $ } = ctx;
  $('#generateKeyframes').onclick = () => generateKeyframes(ctx);
  $('#regenerateKeyframes').onclick = () => generateKeyframes(ctx);
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
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const pos = $('#posPrompt').value.trim();
  const neg = $('#negPrompt').value.trim();
  const body = {};
  if (pos) body.positive_prompt = pos;
  if (neg) body.negative_prompt = neg;

  try {
    $('#keyframeStatus').textContent = '開始生成...';
    $('#generateKeyframes').disabled = true;
    $('#regenerateKeyframes').disabled = true;
    await api(`/api/episodes/${episodeId}/keyframes/generate`, {
      method: 'POST',
      body: JSON.stringify(body)
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    $('#generateKeyframes').disabled = false;
    $('#regenerateKeyframes').disabled = false;
  }
}
