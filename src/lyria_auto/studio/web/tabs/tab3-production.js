// Tab 3: explicit production render -> review -> upscale -> 64s 1080p loop.

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function latestFor(assets, kind, role) {
  return assets.filter(a => a.kind === kind && a.role === role && a.status !== 'superseded').slice(-1)[0] || null;
}

function reviewActions(asset, label) {
  if (!asset) return '';
  if (asset.status === 'awaiting_review') {
    return `
      <button class="approve-btn production-approve" data-id="${asset.id}" data-version="${asset.state_version}">核准 ${label}</button>
      <button class="reject production-regenerate" data-id="${asset.id}" data-version="${asset.state_version}">重新生成</button>`;
  }
  if (asset.status === 'failed') {
    return `<button class="reject production-regenerate" data-id="${asset.id}" data-version="${asset.state_version}">重試</button>`;
  }
  if (asset.status === 'approved') return '<div class="approved-badge">✓ 已核准</div>';
  return '';
}

function renderAsset(asset, label, ctx) {
  if (!asset) {
    return `<div class="motion-card"><strong>${label}</strong><div class="placeholder-video">尚未產生</div></div>`;
  }
  const media = asset.path
    ? `<video controls preload="metadata" src="/api/artifact/${asset.id}" class="motion-video"></video>`
    : `<div class="placeholder-video">${ctx.STATUS_LABEL[asset.status] || asset.status}</div>`;
  return `
    <div class="motion-card ${asset.status === 'approved' ? 'approved' : ''}">
      <div class="motion-meta"><strong>${label}</strong><span class="badge">${ctx.STATUS_LABEL[asset.status] || asset.status}</span></div>
      ${media}
      ${asset.error ? `<p class="error">${escapeHtml(asset.error)}</p>` : ''}
      <div class="motion-actions">${reviewActions(asset, label)}</div>
    </div>`;
}

export function renderProductionTab(data, ctx) {
  const { $, $$ } = ctx;
  const assets = data.assets || [];
  const tasks = data.tasks || [];
  const previewApproved = assets.some(a => a.kind === 'loop_preview' && a.status === 'approved');
  const inFlight = tasks.some(t => ['generate_clip', 'upscale_clip', 'build_loop'].includes(t.task_type) && ['queued', 'running'].includes(t.status));
  const clips = ['sleep', 'lookup'].map(role => latestFor(assets, 'clip', role));
  const upscaled = ['sleep', 'lookup'].map(role => latestFor(assets, 'clip_1080p', role));
  const loop = latestFor(assets, 'loop', 'shared');
  const hasAnyClip = clips.some(Boolean);
  const allRolesHaveClip = clips.every(Boolean);

  $('#startProduction').disabled = !previewApproved || inFlight || allRolesHaveClip;
  $('#startProduction').classList.toggle('hidden', allRolesHaveClip || Boolean(loop));
  $('#cancelProduction').classList.toggle('hidden', !inFlight);

  let status = '';
  if (!previewApproved) status = '請先在 Tab 2 核准 64 秒低解析度預覽。';
  else if (inFlight) status = '正式片段正在排隊或處理中；完成後會出現在下方供審核。';
  else if (loop && loop.status === 'approved') status = '1080p 循環片段已核准，可前往音樂階段。';
  else if (loop && loop.status === 'awaiting_review') status = '高解析度循環已完成，請播放並核准。';
  else if (hasAnyClip) status = '請依序核准正式片段與 1080p 升頻結果。';
  else status = '預覽已核准，可以開始正式處理。';
  $('#productionStatus').textContent = status;

  $('#production-grid').innerHTML = [
    renderAsset(clips[0], 'Sleep 正式片段 1024×576', ctx),
    renderAsset(clips[1], 'Lookup 正式片段 1024×576', ctx),
    renderAsset(upscaled[0], 'Sleep 升頻片段 1920×1080', ctx),
    renderAsset(upscaled[1], 'Lookup 升頻片段 1920×1080', ctx),
  ].join('');

  if (loop) {
    const action = loop.status === 'awaiting_review'
      ? `<button class="approve-btn production-approve" data-id="${loop.id}" data-version="${loop.state_version}">核准 1080p 循環</button>`
      : loop.status === 'approved' ? '<div class="approved-badge">✓ 已核准，可前往 Tab 4</div>' : '';
    $('#production-loop').innerHTML = `
      <h4 class="production-section-title">64 秒 1080p 高解析度循環</h4>
      <div class="preview-card ${loop.status === 'approved' ? 'approved' : ''}">
        ${loop.path ? `<video controls preload="metadata" src="/api/artifact/${loop.id}" class="preview-video"></video>` : '<div class="placeholder-video">組裝中...</div>'}
        ${loop.error ? `<p class="error">${escapeHtml(loop.error)}</p>` : ''}
        <div class="motion-actions">${action}</div>
      </div>`;
  } else {
    $('#production-loop').innerHTML = '';
  }

  $$('.production-approve').forEach(btn => {
    btn.onclick = () => approve(ctx, btn.dataset.id, btn.dataset.version);
  });
  $$('.production-regenerate').forEach(btn => {
    btn.onclick = () => regenerate(ctx, btn.dataset.id, btn.dataset.version);
  });
}

export function bindProductionTab(ctx) {
  ctx.$('#startProduction').onclick = () => start(ctx);
  ctx.$('#cancelProduction').onclick = () => cancel(ctx);
}

async function start(ctx) {
  if (!confirm('開始正式片段處理？若 Studio 設定為遠端 GPU，此操作可能產生費用。')) return;
  try {
    ctx.$('#startProduction').disabled = true;
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/production/start`, { method: 'POST' });
    await ctx.refresh();
  } catch (err) {
    alert(err.message);
    await ctx.refresh();
  }
}

async function cancel(ctx) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/production/cancel`, { method: 'POST' });
    await ctx.refresh();
  } catch (err) {
    alert(err.message);
  }
}

async function approve(ctx, id, version) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/assets/${id}/approve`, {
      method: 'POST', body: JSON.stringify({ expected_version: Number(version) }),
    });
    await ctx.refresh();
  } catch (err) {
    alert(err.message);
    await ctx.refresh();
  }
}

async function regenerate(ctx, id, version) {
  const reason = prompt('請輸入重生原因或新的提示詞（可留空使用原設定）：', '');
  if (reason === null) return;
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/assets/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(version), reason: reason || '使用者要求重新生成' }),
    });
    await ctx.refresh();
  } catch (err) {
    alert(err.message);
    await ctx.refresh();
  }
}
