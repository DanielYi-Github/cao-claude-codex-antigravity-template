// Tab 5: render, review, download, and explicitly-confirmed YouTube upload.

let metadataEpisodeId = null;
let uploadBusy = false;
let uploadReady = false;

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

async function ensureMetadata(ctx) {
  const episodeId = ctx.getEpisodeId();
  if (!episodeId || metadataEpisodeId === episodeId) return;
  const defaults = await ctx.api(`/api/episodes/${episodeId}/final/metadata-defaults`);
  ctx.$('#youtubeTitle').value = defaults.title;
  ctx.$('#youtubeDescription').value = defaults.description;
  ctx.$('#youtubeTags').value = defaults.tags.join(', ');
  ctx.$('#youtubePrivacy').value = defaults.privacy_status;
  ctx.$('#confirmYoutubeUpload').checked = false;
  metadataEpisodeId = episodeId;
}

export async function renderFinalTab(data, ctx) {
  await ensureMetadata(ctx);
  const { $, $$, STATUS_LABEL } = ctx;
  const assets = data.assets || [];
  const mixApproved = assets.some(a => a.kind === 'music_mix' && a.status === 'approved');
  const finalAsset = assets.filter(a => a.kind === 'final' && a.status !== 'superseded').slice(-1)[0] || null;
  const rendering = (data.tasks || []).some(t => t.task_type === 'render_final' && ['queued', 'running'].includes(t.status));
  const finalApproved = finalAsset && finalAsset.status === 'approved';
  uploadReady = Boolean(finalApproved);
  const uploaded = data.publication && data.publication.youtube_video_id;

  $('#renderFinal').disabled = !mixApproved || rendering || Boolean(finalAsset);
  $('#renderFinal').classList.toggle('hidden', Boolean(finalAsset));
  let status = '';
  if (!mixApproved) status = '請先在 Tab 4 核准完整專輯混音。';
  else if (rendering) status = '正在組裝長影片；使用 stream copy，仍可能需要幾分鐘。';
  else if (finalApproved) status = '最終影片已核准，可直接下載。';
  else if (finalAsset && finalAsset.status === 'awaiting_review') status = '最終影片已完成，請播放檢查並核准。';
  else status = '素材已就緒，可以建立最終影片。';
  $('#finalStatus').textContent = status;

  if (finalAsset) {
    let actions = '';
    if (finalAsset.status === 'awaiting_review') {
      actions = `<button class="approve-btn final-approve" data-id="${finalAsset.id}" data-version="${finalAsset.state_version}">核准最終影片</button>`;
    } else if (finalApproved) {
      actions = `<a class="download-link" href="/api/artifact/${finalAsset.id}" download>下載 MP4</a><span class="approved-badge">✓ 已核准</span>`;
    }
    $('#final-player').innerHTML = `
      <div class="preview-card ${finalApproved ? 'approved' : ''}">
        ${finalAsset.path ? `<video controls preload="metadata" src="/api/artifact/${finalAsset.id}" class="final-video"></video>` : '<div class="placeholder-video">渲染中...</div>'}
        ${finalAsset.error ? `<p class="error">${escapeHtml(finalAsset.error)}</p>` : ''}
        <div class="motion-actions">${actions}</div>
      </div>`;
  } else {
    $('#final-player').innerHTML = '';
  }

  $$('.final-approve').forEach(btn => {
    btn.onclick = () => approveFinal(ctx, btn.dataset.id, btn.dataset.version);
  });

  $('#uploadYoutube').disabled = !finalApproved || !$('#confirmYoutubeUpload').checked || uploadBusy || Boolean(uploaded);
  if (uploaded) {
    $('#youtubeStatus').innerHTML = `已上傳：<a href="https://youtu.be/${encodeURIComponent(uploaded)}" target="_blank" rel="noopener">https://youtu.be/${escapeHtml(uploaded)}</a>`;
  }
}

export function bindFinalTab(ctx) {
  ctx.$('#renderFinal').onclick = () => render(ctx);
  ctx.$('#confirmYoutubeUpload').onchange = () => {
    ctx.$('#uploadYoutube').disabled = !uploadReady || !ctx.$('#confirmYoutubeUpload').checked;
  };
  ctx.$('#uploadYoutube').onclick = () => upload(ctx);
}

async function render(ctx) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/final/render`, { method: 'POST' });
    await ctx.refresh();
  } catch (err) { alert(err.message); await ctx.refresh(); }
}

async function approveFinal(ctx, id, version) {
  try {
    await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/assets/${id}/approve`, {
      method: 'POST', body: JSON.stringify({ expected_version: Number(version) }),
    });
    await ctx.refresh();
  } catch (err) { alert(err.message); await ctx.refresh(); }
}

async function upload(ctx) {
  if (!ctx.$('#confirmYoutubeUpload').checked) return;
  if (!confirm('最後確認：現在要把影片上傳到設定的 YouTube 頻道嗎？')) return;
  uploadBusy = true;
  ctx.pausePolling();
  ctx.$('#uploadYoutube').disabled = true;
  ctx.$('#youtubeStatus').textContent = '正在上傳 YouTube，請勿重複操作...';
  try {
    const result = await ctx.api(`/api/episodes/${ctx.getEpisodeId()}/final/upload-youtube`, {
      method: 'POST',
      body: JSON.stringify({
        title: ctx.$('#youtubeTitle').value.trim(),
        description: ctx.$('#youtubeDescription').value.trim(),
        tags: ctx.$('#youtubeTags').value.split(',').map(v => v.trim()).filter(Boolean),
        privacy_status: ctx.$('#youtubePrivacy').value,
        confirm_upload: true,
        channel_name: 'main',
      }),
    });
    ctx.$('#youtubeStatus').textContent = `上傳完成：${result.youtube_video_id}`;
  } catch (err) {
    alert(err.message);
    ctx.$('#youtubeStatus').textContent = err.message;
  } finally {
    uploadBusy = false;
    ctx.$('#confirmYoutubeUpload').checked = false;
    try { await ctx.refresh(); }
    finally { ctx.resumePolling(); }
  }
}
