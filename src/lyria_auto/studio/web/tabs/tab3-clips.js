// Tab 3: the 1080p production run.
//
// On the Veo path this replaces the ComfyUI cloud-upscale stage entirely
// -- Veo renders 1080p natively, so the flow is "cheap 720p test in tab
// 2, approve, re-run the same prompts at full resolution here", writing
// straight into clip_1080p. Approving both roles then feeds app.py's
// existing clip_1080p fan-in, which fires build_loop with no extra
// wiring.
//
// Receives the shared `ctx` from app.js rather than importing its
// internals, same contract as the other tab modules.

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function ensureTabScaffold(ctx) {
  const { $ } = ctx;
  const tab3 = $('#tab-3');
  if (!tab3) return;
  if ($('#clip-players', tab3)) return;

  tab3.innerHTML = `
    <div class="card">
      <h3>3. 1080p 正式版 (Production Clips)</h3>
      <p class="muted">
        兩支低解析度測試都核准後，用同樣的提示詞重跑一次 1080p 正式版。
        Veo 直接輸出 1080p，因此這一步不需要雲端升頻服務。
      </p>
      <div class="generate-controls">
        <div class="form-group">
          <label for="clipResolution">解析度</label>
          <select id="clipResolution"></select>
        </div>
      </div>
      <p id="clipCostNote" class="muted" aria-live="polite"></p>
      <button id="generateClips">開始生成 1080p 正式版</button>
      <button id="cancelClips" class="stop-btn hidden">終止 (Stop)</button>
      <div id="clipStatus" class="notice" aria-live="polite"></div>
      <div id="clip-players" class="motion-grid"></div>
    </div>
  `;
  bindClipsTab(ctx);
}

export function renderClipsTab(data, ctx) {
  const { $, $$, STATUS_LABEL } = ctx;
  ensureTabScaffold(ctx);

  const assets = data.assets || [];
  const tasks = data.tasks || [];
  const clips = assets.filter(a => a.kind === 'clip_1080p' && a.status !== 'superseded');
  const approvedMotion = new Set(
    assets.filter(a => a.kind === 'motion_test' && a.status === 'approved').map(a => a.role)
  );
  const testsReady = approvedMotion.has('sleep') && approvedMotion.has('lookup');

  const CLIP_TASK_TYPES = ['start_veo_clip', 'poll_veo_clip'];
  const isGenerating = tasks.some(
    t => CLIP_TASK_TYPES.includes(t.task_type) && ['queued', 'running'].includes(t.status)
  );
  const hasLiveClips = clips.some(
    a => ['queued', 'running', 'awaiting_review', 'approved'].includes(a.status)
  );

  const btn = $('#generateClips');
  if (btn) btn.disabled = !testsReady || isGenerating || hasLiveClips;
  const cancelBtn = $('#cancelClips');
  if (cancelBtn) cancelBtn.classList.toggle('hidden', !isGenerating);

  let statusText;
  if (!testsReady) {
    statusText = '需先在頁籤 2 讓 Sleep 與 Lookup 兩支測試都通過核准。';
  } else if (isGenerating) {
    statusText = '正在生成 1080p 正式版... Veo 兩支影片同時在雲端處理中。';
  } else if (clips.every(a => a.status === 'approved') && clips.length === 2) {
    statusText = '✓ 兩支 1080p 正式版皆已核准，已自動排入 64 秒循環組裝。';
  } else if (hasLiveClips) {
    statusText = '請播放檢視下方 1080p 片段並核准。';
  } else {
    statusText = '兩支測試已核准，可以開始生成 1080p 正式版。';
  }
  const statusEl = $('#clipStatus');
  if (statusEl && statusEl.textContent !== statusText) statusEl.textContent = statusText;

  const container = $('#clip-players');
  if (!container) return;
  const roles = [
    { role: 'sleep', title: '睡眠動作 1080p (Sleep)' },
    { role: 'lookup', title: '抬頭動作 1080p (Lookup)' },
  ];
  container.innerHTML = roles.map(({ role, title }) => {
    const active = clips.filter(a => a.role === role);
    const asset = active.length ? active[active.length - 1] : null;
    if (!asset) {
      return `
        <div class="motion-card">
          <div class="motion-meta"><span class="motion-title">${escapeHtml(title)}</span>
          <span class="badge">未生成</span></div>
          <div class="placeholder-video">尚未生成 1080p 正式版</div>
        </div>`;
    }
    const media = asset.path
      ? `<video controls preload="metadata" src="/api/artifact/${asset.id}" class="motion-video"></video>`
      : `<div class="placeholder-video">${STATUS_LABEL[asset.status] || '生成中...'}</div>`;
    const bits = [];
    if (asset.width && asset.height) bits.push(`${asset.width}×${asset.height}`);
    if (asset.estimated_cost_usd != null) {
      bits.push(`預估 US$${Number(asset.estimated_cost_usd).toFixed(2)}`);
    }
    if (asset.seam_score != null) {
      const ok = asset.seam_score <= 0.06;
      bits.push(`${ok ? '✓' : '⚠'} 循環接縫 ${Number(asset.seam_score).toFixed(4)}`);
    }
    let actions = '';
    if (asset.status === 'awaiting_review') {
      actions = `<button class="btn approve-btn clip-approve-btn" data-id="${asset.id}"
        data-version="${asset.state_version}">核准此片段</button>
        <button class="btn reject clip-reject-btn" data-id="${asset.id}"
        data-version="${asset.state_version}">重新生成</button>`;
    } else if (asset.status === 'approved') {
      actions = '<div class="approved-badge">✓ 已核准</div>';
    } else if (['queued', 'running'].includes(asset.status)) {
      actions = `<div class="generating-badge">⏳ ${STATUS_LABEL[asset.status] || '生成中'}...</div>`;
    } else if (asset.status === 'failed') {
      actions = `<button class="btn reject clip-reject-btn" data-id="${asset.id}"
        data-version="${asset.state_version}">重新生成</button>`;
    }
    return `
      <div class="motion-card ${asset.status === 'approved' ? 'approved' : ''}">
        <div class="motion-meta">
          <span class="motion-title">${escapeHtml(title)}</span>
          <span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>
        </div>
        ${media}
        ${bits.length ? `<p class="muted provenance">${bits.join('　·　')}</p>` : ''}
        ${asset.error ? `<p class="error">${escapeHtml(asset.error)}</p>` : ''}
        <div class="motion-actions">${actions}</div>
      </div>`;
  }).join('');

  $$('.clip-approve-btn', container).forEach(b => {
    b.onclick = () => approveClip(ctx, b.dataset.id, b.dataset.version);
  });
  $$('.clip-reject-btn', container).forEach(b => {
    b.onclick = () => rejectClip(ctx, b.dataset.id, b.dataset.version);
  });
}

// Populated once per page load from the same catalog tab 2 uses; the
// default here is 1080p because that is what this stage is for.
async function loadClipOptions(ctx) {
  const { $, api } = ctx;
  const sel = $('#clipResolution');
  if (!sel || sel.options.length) return;
  try {
    const config = await api('/api/veo/config');
    const entry = config.models.find(m => m.id === config.default_model) || config.models[0];
    const options = entry ? entry.resolutions : ['1080p'];
    sel.innerHTML = options.map(r => `<option value="${r}">${r}</option>`).join('');
    if (options.includes('1080p')) sel.value = '1080p';
    const note = $('#clipCostNote');
    if (note && entry) {
      note.textContent =
        `預估費用約 US$${entry.estimated_usd_per_pair.toFixed(2)}`
        + `（兩支 × ${config.duration_seconds} 秒，未校對的估價快照，`
        + '實際以 Google 帳單為準）。';
    }
  } catch {
    sel.innerHTML = '<option value="1080p">1080p</option>';
  }
}

export function bindClipsTab(ctx) {
  const { $ } = ctx;
  const btn = $('#generateClips');
  if (btn) btn.onclick = () => generateClips(ctx);
  const cancelBtn = $('#cancelClips');
  if (cancelBtn) cancelBtn.onclick = () => cancelClips(ctx);
  if ($('#clipResolution')) loadClipOptions(ctx);
}

async function generateClips(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const btn = $('#generateClips');
  const statusEl = $('#clipStatus');
  const resolution = ($('#clipResolution') || {}).value || '1080p';
  if (!confirm(`即將以 ${resolution} 生成兩支正式片段，這會產生 Google 的費用。要繼續嗎？`)) {
    return;
  }
  try {
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.textContent = '正在送出 1080p 生成請求...';
    await api(`/api/episodes/${episodeId}/clips/generate`, {
      method: 'POST',
      body: JSON.stringify({ provider: 'veo', resolution }),
    });
    await refresh();
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function cancelClips(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const statusEl = $('#clipStatus');
  try {
    const result = await api(`/api/episodes/${episodeId}/clips/cancel`, { method: 'POST' });
    await refresh();
    if (result && result.abandoned_paid_operations > 0 && statusEl) {
      statusEl.textContent =
        `已停止接收結果，但其中 ${result.abandoned_paid_operations} 次 Veo 生成`
        + '已經送出並計費，取消無法退費。';
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
    await refresh();
  }
}

async function approveClip(ctx, assetId, expectedVersion) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  const statusEl = $('#clipStatus');
  try {
    await api(`/api/episodes/${episodeId}/assets/${assetId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(expectedVersion) }),
    });
    await refresh();
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
    await refresh();
  }
}

async function rejectClip(ctx, assetId, expectedVersion) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  const statusEl = $('#clipStatus');
  if (!confirm('重新生成會再產生一次 Google 費用。要繼續嗎？')) return;
  try {
    await api(`/api/episodes/${episodeId}/assets/${assetId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(expectedVersion), reason: '人工退回' }),
    });
    await refresh();
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
    await refresh();
  }
}
