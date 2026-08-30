// Tab 2: motion generation (sleep/lookup 8s clips), individual regeneration,
// and 64s low-resolution macro-loop preview assembly.
//
// Receives a shared `ctx` object from app.js instead of importing app.js's
// internals directly -- keeps every tab module self-contained and avoids
// concurrent file-edit conflicts across agent tasks (studio-console-v2-plan.md 7.0).

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
  const tab2 = $('#tab-2');
  if (!tab2) return;

  if (!$('#motion-players', tab2)) {
    tab2.innerHTML = `
      <div class="card">
        <h3>2. 動作生成與審核 (Motion Test)</h3>
        <p class="muted">檢視 8 秒睡眠 (Sleep) 與抬頭 (Lookup) 動作片段。兩者皆核准後，方可組裝 64 秒低解析度預覽。</p>
        <div id="motionStatus" class="notice" aria-live="polite"></div>
        <div id="motion-players" class="motion-grid"></div>
      </div>

      <div class="card" id="assemble-section">
        <h3>64 秒預覽組裝 (64s Preview Assembly)</h3>
        <p class="muted">以 7×Sleep + 1×Lookup 串接成 64 秒無縫循環預覽影片。</p>
        <div class="inline-form">
          <button id="assemblePreview" disabled>組裝 64 秒預覽 (Assemble 64s Preview)</button>
        </div>
        <div id="assembleStatus" class="notice" aria-live="polite"></div>
        <div id="loop-preview-container"></div>
      </div>
    `;
    bindMotionTab(ctx);
  }
}

export function renderMotionTab(data, ctx) {
  const { $, $$, STATUS_LABEL } = ctx;
  ensureTabScaffold(ctx);

  const assets = data.assets || [];
  const tasks = data.tasks || [];

  const hasKeyframeApproved = assets.some(a => a.kind === 'keyframe' && a.status === 'approved');
  const motionAssets = assets.filter(a => a.kind === 'motion_test');
  const loopPreviewAssets = assets.filter(a => a.kind === 'loop_preview');

  const isGeneratingMotion = tasks.some(
    t => t.task_type === 'generate_motion_test' && ['queued', 'running'].includes(t.status)
  );
  const isAssembling = tasks.some(
    t => t.task_type === 'build_loop_preview' && ['queued', 'running'].includes(t.status)
  );

  const roles = [
    {
      role: 'sleep',
      title: '睡眠動作 (Sleep)',
      desc: '8 秒片段，躺平平靜呼吸與微幅環境晃動（循環佔 7/8）',
    },
    {
      role: 'lookup',
      title: '抬頭動作 (Lookup)',
      desc: '8 秒片段，抬頭望向座位後躺回原位（循環佔 1/8）',
    },
  ];

  const roleData = {};
  for (const { role } of roles) {
    const active = motionAssets.filter(a => a.role === role && a.status !== 'superseded');
    const latest = active.length > 0 ? active[active.length - 1] : null;
    const isApproved = active.some(a => a.status === 'approved');
    roleData[role] = {
      active,
      latest,
      isApproved,
    };
  }

  const sleepApproved = roleData.sleep.isApproved;
  const lookupApproved = roleData.lookup.isApproved;
  const bothApproved = sleepApproved && lookupApproved;

  // 1. Update Motion Status message
  let motionStatusText = '';
  if (!hasKeyframeApproved) {
    motionStatusText = '尚未選定關鍵幀。請先在 Tab 1 完成關鍵幀生成與核准。';
  } else if (isGeneratingMotion) {
    motionStatusText = '正在生成動作片段 (Sleep / Lookup)... 請稍候。';
  } else if (bothApproved) {
    motionStatusText = 'Sleep 與 Lookup 動作片段皆已核准。';
  } else {
    motionStatusText = '請審核下方動作片段，可核准或重新生成。';
  }

  const motionStatusEl = $('#motionStatus');
  if (motionStatusEl && motionStatusEl.textContent !== motionStatusText) {
    motionStatusEl.textContent = motionStatusText;
  }

  // 2. Render Motion Players Grid
  const playersContainer = $('#motion-players');
  if (playersContainer) {
    playersContainer.innerHTML = roles.map(({ role, title, desc }) => {
      const info = roleData[role];
      const asset = info.latest;

      if (!asset) {
        return `
          <div class="motion-card">
            <div class="motion-meta">
              <span class="motion-title">${escapeHtml(title)}</span>
              <span class="badge">未生成</span>
            </div>
            <p class="muted">${escapeHtml(desc)}</p>
            <div class="placeholder-video">尚未生成動作片段</div>
            <div class="motion-actions">
              <span class="muted">需先在 Tab 1 完成關鍵幀選定</span>
            </div>
          </div>
        `;
      }

      const isApproved = asset.status === 'approved';
      const isAwaiting = asset.status === 'awaiting_review';
      const isQueuedOrRunning = ['queued', 'running'].includes(asset.status);
      const isRejected = asset.status === 'rejected';
      const errorHtml = asset.error ? `<p class="error">${escapeHtml(asset.error)}</p>` : '';

      let mediaHtml = '';
      if (asset.path) {
        mediaHtml = `<video controls preload="metadata" src="/api/artifact/${asset.id}" class="motion-video"></video>`;
      } else {
        mediaHtml = `<div class="placeholder-video">${STATUS_LABEL[asset.status] || '生成中...'}</div>`;
      }

      let actionsHtml = '';
      if (isAwaiting) {
        actionsHtml = `
          <button class="btn approve-btn motion-approve-btn" data-id="${asset.id}" data-version="${asset.state_version}">核准此動作</button>
          <button class="btn reject motion-reject-btn" data-id="${asset.id}" data-version="${asset.state_version}" data-role="${role}">重新生成 (Regenerate)</button>
        `;
      } else if (isApproved) {
        actionsHtml = `<div class="approved-badge">✓ 已核准</div>`;
      } else if (isQueuedOrRunning) {
        actionsHtml = `<div class="generating-badge">⏳ ${STATUS_LABEL[asset.status] || '生成中'}...</div>`;
      } else if (isRejected) {
        actionsHtml = `<div class="muted">已退回，等待重試中...</div>`;
      } else {
        actionsHtml = `<span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>`;
      }

      return `
        <div class="motion-card ${isApproved ? 'approved' : ''} ${isRejected ? 'rejected' : ''}">
          <div class="motion-meta">
            <span class="motion-title">${escapeHtml(title)}</span>
            <div>
              <span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>
              ${asset.variant_index != null ? `<span class="muted">v${asset.variant_index + 1}</span>` : ''}
            </div>
          </div>
          <p class="muted">${escapeHtml(desc)}</p>
          ${mediaHtml}
          ${errorHtml}
          <div class="motion-actions">${actionsHtml}</div>
        </div>
      `;
    }).join('');

    // Bind Motion Approve buttons
    $$('.motion-approve-btn', playersContainer).forEach(btn => {
      btn.onclick = () => approveMotion(ctx, btn.dataset.id, btn.dataset.version);
    });

    // Bind Motion Reject / Regenerate buttons
    $$('.motion-reject-btn', playersContainer).forEach(btn => {
      btn.onclick = () => regenerateMotion(ctx, btn.dataset.id, btn.dataset.version, btn.dataset.role);
    });
  }

  // 3. Assemble 64s Preview Section
  const activePreviews = loopPreviewAssets.filter(a => a.status !== 'superseded');
  const hasApprovedPreview = activePreviews.some(a => a.status === 'approved');
  const latestPreview = activePreviews.length > 0 ? activePreviews[activePreviews.length - 1] : null;
  // The backend 409s a re-assemble while a preview is sitting at
  // awaiting_review too, not just once it's approved -- there's no
  // supersede-and-pick-another UX for a single 64s result the way tab 1's
  // 12-candidate grid has, and every build writes the same fixed
  // loop_preview-shared-v0.mp4 path (codex_reviewer Phase 2 review). Gate
  // the button on the same condition so it doesn't offer an action that
  // just errors.
  const hasUnresolvedPreview = latestPreview != null && latestPreview.status === 'awaiting_review';

  const assembleBtn = $('#assemblePreview');
  if (assembleBtn) {
    const canAssemble = bothApproved && !isAssembling && !hasApprovedPreview && !hasUnresolvedPreview;
    assembleBtn.disabled = !canAssemble;

    if (isAssembling) {
      assembleBtn.textContent = '正在組裝 64 秒預覽 (處理中)...';
    } else if (hasApprovedPreview) {
      assembleBtn.textContent = '64 秒預覽已核准 (已鎖定)';
    } else if (hasUnresolvedPreview) {
      assembleBtn.textContent = '請先核准下方預覽';
    } else {
      assembleBtn.textContent = '組裝 64 秒預覽 (Assemble 64s Preview)';
    }
  }

  let assembleStatusText = '';
  if (isAssembling) {
    assembleStatusText = '正在組裝 64 秒預覽 (build_loop_preview)，請稍候...';
  } else if (hasApprovedPreview) {
    assembleStatusText = '✓ 64 秒預覽已核准，可繼續前往頁籤 3 (Tab 3: 升頻)。';
  } else if (latestPreview && latestPreview.status === 'awaiting_review') {
    assembleStatusText = '64 秒預覽已組裝完成，請播放檢視並核准。';
  } else if (!bothApproved) {
    assembleStatusText = '需等待 Sleep 與 Lookup 動作片段皆核准後，方可組裝 64 秒預覽。';
  } else {
    assembleStatusText = '雙動作已核准，點擊上方按鈕開始組裝 64 秒預覽。';
  }

  const assembleStatusEl = $('#assembleStatus');
  if (assembleStatusEl && assembleStatusEl.textContent !== assembleStatusText) {
    assembleStatusEl.textContent = assembleStatusText;
  }

  const previewContainer = $('#loop-preview-container');
  if (previewContainer) {
    if (latestPreview) {
      const isPreviewApproved = latestPreview.status === 'approved';
      const isPreviewAwaiting = latestPreview.status === 'awaiting_review';
      const previewError = latestPreview.error ? `<p class="error">${escapeHtml(latestPreview.error)}</p>` : '';

      let previewMedia = '';
      if (latestPreview.path) {
        previewMedia = `<video controls preload="metadata" src="/api/artifact/${latestPreview.id}" class="preview-video"></video>`;
      } else {
        previewMedia = `<div class="placeholder-video">${STATUS_LABEL[latestPreview.status] || '預覽組裝中...'}</div>`;
      }

      let previewActions = '';
      if (isPreviewAwaiting) {
        previewActions = `<button class="btn approve-btn preview-approve-btn" data-id="${latestPreview.id}" data-version="${latestPreview.state_version}">核准 64 秒預覽</button>`;
      } else if (isPreviewApproved) {
        previewActions = `<div class="approved-badge">✓ 64 秒預覽已核准（解鎖頁籤 3）</div>`;
      } else if (['queued', 'running'].includes(latestPreview.status)) {
        previewActions = `<div class="generating-badge">⏳ 組裝中...</div>`;
      }

      previewContainer.innerHTML = `
        <div class="preview-card ${isPreviewApproved ? 'approved' : ''}">
          <div class="motion-meta">
            <span class="motion-title">64 秒低解析度預覽 (64s Preview Macro-Loop)</span>
            <div>
              <span class="badge">${STATUS_LABEL[latestPreview.status] || latestPreview.status}</span>
              ${latestPreview.variant_index != null ? `<span class="muted">v${latestPreview.variant_index + 1}</span>` : ''}
            </div>
          </div>
          ${previewMedia}
          ${previewError}
          <div class="motion-actions">${previewActions}</div>
        </div>
      `;

      $$('.preview-approve-btn', previewContainer).forEach(btn => {
        btn.onclick = () => approvePreview(ctx, btn.dataset.id, btn.dataset.version);
      });
    } else if (isAssembling) {
      previewContainer.innerHTML = '<div class="placeholder-video">正在組裝 64 秒預覽...</div>';
    } else {
      previewContainer.innerHTML = '';
    }
  }
}

export function bindMotionTab(ctx) {
  const { $ } = ctx;
  const btn = $('#assemblePreview');
  if (btn) {
    btn.onclick = () => assemblePreview(ctx);
  }
}

async function approveMotion(ctx, assetId, expectedVersion) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;

  const statusEl = $('#motionStatus');
  try {
    if (statusEl) statusEl.textContent = '正在核准動作片段...';
    await api(`/api/episodes/${episodeId}/assets/${assetId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(expectedVersion) }),
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    await refresh();
  }
}

async function regenerateMotion(ctx, assetId, expectedVersion, role) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;

  const roleLabel = role === 'sleep' ? '睡眠 (Sleep)' : '抬頭 (Lookup)';
  const reason = prompt(`請輸入重新生成 ${roleLabel} 的原因或提示詞（選填，直接按確定以相同提示詞重試）：`, '');
  if (reason === null) return; // Cancelled

  const statusEl = $('#motionStatus');
  try {
    if (statusEl) statusEl.textContent = `正在請求重新生成 ${roleLabel}...`;
    await api(`/api/episodes/${episodeId}/assets/${assetId}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        expected_version: Number(expectedVersion),
        reason: reason || '使用者要求重新生成',
      }),
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    await refresh();
  }
}

async function assemblePreview(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;

  const btn = $('#assemblePreview');
  const statusEl = $('#assembleStatus');
  try {
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.textContent = '正在請求組裝 64 秒預覽...';
    await api(`/api/episodes/${episodeId}/motion/assemble-preview`, {
      method: 'POST',
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    if (statusEl) statusEl.textContent = err.message;
    if (btn) btn.disabled = false;
    await refresh();
  }
}

async function approvePreview(ctx, assetId, expectedVersion) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;

  const statusEl = $('#assembleStatus');
  try {
    if (statusEl) statusEl.textContent = '正在核准 64 秒預覽...';
    await api(`/api/episodes/${episodeId}/assets/${assetId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(expectedVersion) }),
    });
    await refresh();
  } catch (err) {
    alert(err.message);
    await refresh();
  }
}
