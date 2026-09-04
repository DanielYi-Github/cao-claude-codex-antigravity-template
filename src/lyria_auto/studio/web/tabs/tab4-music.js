// Tab 4: request-scoped Lyria generation, per-track review, and album mix.

let defaults = null;
let musicBusy = false;

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

async function ensureDefaults(ctx) {
  if (!defaults) defaults = await ctx.api('/api/music/defaults');
  if (!ctx.$('#musicPrompt').value) ctx.$('#musicPrompt').value = defaults.base_prompt;
}

function latestSlots(assets) {
  const slots = new Map();
  assets.filter(a => a.kind === 'music_track' && a.status !== 'rejected')
    .forEach(a => slots.set(Number(a.variant_index), a));
  return slots;
}

export async function renderMusicTab(data, ctx) {
  await ensureDefaults(ctx);
  const { $, $$, STATUS_LABEL } = ctx;
  const assets = data.assets || [];
  const loopApproved = assets.some(a => a.kind === 'loop' && a.status === 'approved');
  const slots = latestSlots(assets);
  const allGenerated = slots.size === 12 && [...slots.values()].every(a => ['awaiting_review', 'approved'].includes(a.status));
  const allApproved = slots.size === 12 && [...slots.values()].every(a => a.status === 'approved');
  const hasRunning = [...slots.values()].some(a => a.status === 'running');
  const hasQueued = [...slots.values()].some(a => a.status === 'queued');
  const hasFailed = [...slots.values()].some(a => a.status === 'failed');
  const mix = assets.filter(a => a.kind === 'music_mix' && a.status !== 'superseded').slice(-1)[0] || null;
  const mixTask = (data.tasks || []).some(t => t.task_type === 'build_music_mix' && ['queued', 'running'].includes(t.status));
  const musicTask = (data.tasks || []).some(t => t.task_type === 'generate_music_tracks' && ['queued', 'running'].includes(t.status));

  $('#musicPrompt').disabled = musicBusy || Boolean(data.episode.music_job_id);
  $('#musicPresetLateNight').disabled = musicBusy || Boolean(data.episode.music_job_id);
  $('#generateMusic').disabled = musicBusy || musicTask || !loopApproved || hasRunning || allGenerated;
  $('#generateMusic').classList.toggle('hidden', allGenerated);
  $('#generateMusic').textContent = (hasFailed || hasQueued) ? '重試未完成曲目' : '生成 12 首音樂';
  $('#buildMusicMix').disabled = !allApproved || Boolean(mix) || mixTask;

  let status = '';
  if (!loopApproved) status = '請先在 Tab 3 核准 1080p 循環片段。';
  else if (musicBusy || musicTask) status = '正在逐首生成與驗證音樂；這可能需要 30 分鐘以上，請勿關閉頁面。';
  else if (mix && mix.status === 'approved') status = '專輯混音已核准，可前往最終成片。';
  else if (mix && mix.status === 'awaiting_review') status = '專輯混音已完成，請試聽並核准。';
  else if (allApproved) status = '12 首皆已核准，請組合專輯混音。';
  else if (allGenerated) status = '12 首皆已產生，請逐首試聽、核准或重生。';
  else if (hasFailed) status = '部分曲目失敗；可按「重試未完成曲目」。';
  else status = '高解析度循環已核准，可以建立 12 首伴讀爵士專輯。';
  $('#musicStatus').textContent = status;

  const cards = [];
  for (let slot = 0; slot < 12; slot += 1) {
    const asset = slots.get(slot);
    if (!asset) {
      cards.push(`<div class="music-card"><strong>Track ${slot + 1}</strong><p class="muted">尚未保留此曲目</p></div>`);
      continue;
    }
    let actions = '';
    if (asset.status === 'awaiting_review') {
      actions = `
        <button class="approve-btn music-approve" data-id="${asset.id}" data-version="${asset.state_version}">核准</button>
        <button class="reject music-regenerate" data-id="${asset.id}" data-version="${asset.state_version}" data-prompt="${escapeHtml(asset.source_prompt)}">重生此首</button>`;
    } else if (asset.status === 'failed') {
      actions = `<button class="reject music-regenerate" data-id="${asset.id}" data-version="${asset.state_version}" data-prompt="${escapeHtml(asset.source_prompt)}">修改提示詞並重試</button>`;
    } else if (asset.status === 'approved') {
      actions = '<div class="approved-badge">✓ 已核准</div>';
    }
    cards.push(`
      <div class="music-card ${asset.status === 'approved' ? 'approved' : ''}">
        <div class="motion-meta"><strong>Track ${slot + 1}</strong><span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span></div>
        ${asset.path ? `<audio controls preload="metadata" src="/api/artifact/${asset.id}"></audio>` : '<div class="placeholder-video">生成中...</div>'}
        <div class="music-prompt-preview">${escapeHtml(asset.source_prompt)}</div>
        ${asset.error ? `<p class="error">${escapeHtml(asset.error)}</p>` : ''}
        <div class="motion-actions">${actions}</div>
      </div>`);
  }
  $('#music-grid').innerHTML = cards.join('');

  if (mix) {
    const action = mix.status === 'awaiting_review'
      ? `<button class="approve-btn music-approve" data-id="${mix.id}" data-version="${mix.state_version}">核准整張混音</button>`
      : mix.status === 'approved' ? '<div class="approved-badge">✓ 混音已核准</div>' : '';
    $('#music-mix').innerHTML = `
      <h4>完整專輯混音</h4>
      <div class="preview-card ${mix.status === 'approved' ? 'approved' : ''}">
        ${mix.path ? `<audio controls preload="metadata" src="/api/artifact/${mix.id}" class="music-mix-player"></audio>` : '<div class="placeholder-video">混音中...</div>'}
        <div class="motion-actions">${action}</div>
      </div>`;
  } else {
    $('#music-mix').innerHTML = '';
  }

  $$('.music-approve').forEach(btn => { btn.onclick = () => approve(ctx, btn); });
  $$('.music-regenerate').forEach(btn => { btn.onclick = () => regenerate(ctx, btn); });
}

export function bindMusicTab(ctx) {
  ctx.$('#musicPresetLateNight').onclick = async () => {
    await ensureDefaults(ctx);
    ctx.$('#musicPrompt').value = defaults.base_prompt;
  };
  ctx.$('#generateMusic').onclick = () => generate(ctx);
  ctx.$('#buildMusicMix').onclick = () => buildMix(ctx);
}

function credentials(ctx) {
  return ctx.$('#musicApiKey').value.trim();
}

async function generate(ctx) {
  if (!confirm('確定生成／重試音樂嗎？這可能會產生 Lyria API 費用。')) return;
  musicBusy = true;
  ctx.pausePolling();
  ctx.$('#generateMusic').disabled = true;
  ctx.$('#musicStatus').textContent = '正在生成，完成的曲目會逐首安全保存...';
  try {
    const result = await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/music/generate`, {
      method: 'POST',
      body: JSON.stringify({ base_prompt: ctx.$('#musicPrompt').value.trim(), api_key: credentials(ctx) || null }),
    });
    ctx.$('#musicStatus').textContent = result.failed.length
      ? `完成 ${result.generated.length} 首，失敗 ${result.failed.length} 首。`
      : `已完成 ${result.generated.length} 首。`;
  } catch (err) {
    alert(err.message);
  } finally {
    ctx.$('#musicApiKey').value = '';
    musicBusy = false;
    try { await ctx.refresh(); }
    finally { ctx.resumePolling(); }
  }
}

async function approve(ctx, btn) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/assets/${btn.dataset.id}/approve`, {
      method: 'POST', body: JSON.stringify({ expected_version: Number(btn.dataset.version) }),
    });
    await ctx.refresh();
  } catch (err) { alert(err.message); await ctx.refresh(); }
}

async function regenerate(ctx, btn) {
  const nextPrompt = prompt('編輯這一首的新提示詞：', btn.dataset.prompt || '');
  if (nextPrompt === null || !nextPrompt.trim()) return;
  if (!confirm('重生這一首可能產生 API 費用，確定繼續？')) return;
  musicBusy = true;
  ctx.pausePolling();
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/music/tracks/${btn.dataset.id}/regenerate`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: Number(btn.dataset.version), prompt: nextPrompt.trim(), api_key: credentials(ctx) || null }),
    });
  } catch (err) { alert(err.message); }
  finally {
    ctx.$('#musicApiKey').value = '';
    musicBusy = false;
    try { await ctx.refresh(); }
    finally { ctx.resumePolling(); }
  }
}

async function buildMix(ctx) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/music/build-mix`, { method: 'POST' });
    await ctx.refresh();
  } catch (err) { alert(err.message); await ctx.refresh(); }
}
