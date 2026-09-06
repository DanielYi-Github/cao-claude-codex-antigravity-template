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
// module constants/config (the scene composer's default prompt plus
// src/lyria_auto/studio/stages.py's KEYFRAME_NEGATIVE_PROMPT and
// config/settings.yaml's studio.keyframe_batch_size), not per-episode data,
// so one page load only ever needs to ask the backend for them once.
let _cachedDefaults = null;

async function _fetchDefaultsOnce(ctx) {
  if (!_cachedDefaults) {
    _cachedDefaults = await ctx.api('/api/keyframes/defaults');
  }
  return _cachedDefaults;
}

// --- 場景晶片 --------------------------------------------------------
//
// 為什麼每次點擊都要往後端跑一趟，而不是在前端把英文片語接起來：提示詞
// 的**語序**本身就是這次修正的重點。FLUX schnell 的訓練序列長度是 256
// 個 token，CLIP-L 分支更是硬性截斷在 77 個，所以「哪一句排在前面」直接
// 決定模型會遵守什麼。把那個順序交給前端拼接，等於把最容易改壞的一層
// 放到最關鍵的位置上。後端只回組好的整段文字。
let _cachedPresets = null;

// 目前選到的場景，**以 episode 為單位**。不能用單一個模組變數：從 A 集
// （payload 裡有 scene）切回列表再進 B 集（全新、沒有 generate_keyframe
// 任務）時，還原與預設兩條分支都被 `if (!scene)` 擋掉，A 集的選擇就會留
// 在畫面上——晶片顯示 A 的場景、送出的 body.scene 也是 A 的，但提示詞
// 框裡是 B 的預設文字。tab2-motion.js 的 _cachedMotionDefaults 是同一個
// 道理。
const _sceneByEpisode = new Map();

// 上次真的重畫晶片時的狀態指紋。renderKeyframeTab 是輪詢迴圈每一拍都會
// 呼叫的，沒有這個就會每拍都 host.innerHTML = ... 重建整排按鈕，跟正在
// 點擊的人搶焦點。
let _renderedChipsKey = null;

function _currentScene(ctx) {
  return _sceneByEpisode.get(ctx.getEpisodeId()) ?? null;
}

function _setScene(ctx, scene) {
  _sceneByEpisode.set(ctx.getEpisodeId(), scene);
}

async function _fetchPresetsOnce(ctx) {
  // 失敗不會被記進 _cachedPresets（await 直接丟出），所以伺服器重啟後
  // 下一次輪詢就會自己好起來，不必整頁重載。
  if (!_cachedPresets) {
    _cachedPresets = await ctx.api('/api/keyframes/scene-presets');
  }
  return _cachedPresets;
}

function _renderTokenCount($, tokens, limit) {
  const el = $('#tokenCount');
  if (!el) return;
  if (tokens == null) { el.textContent = ''; return; }
  const over = tokens > limit;
  const text = `約 ${tokens} / ${limit} tokens`;
  if (el.textContent === text) return;
  el.textContent = text;
  el.classList.toggle('ok', !over);
  el.classList.toggle('over', over);
  el.title = over
    ? `超過 FLUX schnell 的訓練長度 ${limit}——寫在後段的位置與鏡頭指令會被稀釋掉。`
    : `FLUX schnell 的訓練序列長度是 ${limit} 個 token。`;
}

// 哪些選項在目前的選擇下是不合法的（例如「雪」只能配「冬」）。後端也會
// 擋（回 400），這裡只是不要讓人點了才被拒絕。
function _blockedOptions(constraints, scene) {
  const blocked = new Set();
  for (const rule of constraints || []) {
    if (!rule.needs_any.includes(scene[rule.needs_axis])) {
      blocked.add(`${rule.axis}:${rule.option}`);
    }
  }
  return blocked;
}

async function _renderScenePresets(ctx, { disabled }) {
  const { $ } = ctx;
  const host = $('#scenePresets');
  if (!host) return;
  let presets;
  try {
    presets = await _fetchPresetsOnce(ctx);
  } catch {
    // 退回純文字編輯（跟這個功能出現之前一樣），但要**講出來**。
    // 原本這裡是靜默清空：晶片整排消失，畫面上沒有任何線索，人只會以為
    // 功能沒做。實際遇到的情形是伺服器行程比程式碼舊——靜態檔（HTML/JS）
    // 每次請求都從磁碟讀，所以新版介面看得到，但路由還是舊行程載入的那份，
    // /api/keyframes/scene-presets 回 404。
    host.innerHTML = '';
    const notice = $('#sceneNotice');
    if (notice) {
      notice.textContent = '⚠️ 讀不到場景預設選項（/api/keyframes/scene-presets）。'
        + '最常見的原因是伺服器行程比程式碼舊——重新啟動 lyria-auto studio 後再重新整理。'
        + '在那之前仍可直接編輯下面的提示詞文字。';
      notice.classList.add('warn');
    }
    return;
  }
  // 走到這裡代表預設選項讀得到了——把上面那段「讀不到」的警告清掉，
  // 否則伺服器重啟後訊息會留在畫面上，變成另一種誤導。
  const notice = $('#sceneNotice');
  if (notice && notice.classList.contains('warn')) {
    notice.textContent = '';
    notice.classList.remove('warn');
  }

  let scene = _currentScene(ctx);
  if (!scene) {
    scene = { ...presets.defaults };
    _setScene(ctx, scene);
  }

  // 狀態沒變就別重建 DOM——輪詢迴圈每一拍都會走到這裡。
  const key = `${ctx.getEpisodeId()}|${disabled}|${JSON.stringify(scene)}`;
  if (_renderedChipsKey === key) return;

  const blocked = _blockedOptions(presets.constraints, scene);
  host.innerHTML = presets.axes.map(axis => `
    <div class="scene-axis">
      <span class="scene-axis-label">${axis.label_zh}</span>
      ${axis.options.map(opt => {
        const isBlocked = blocked.has(`${axis.id}:${opt.id}`);
        return `<button type="button" class="scene-chip${scene[axis.id] === opt.id ? ' active' : ''}"
                 data-axis="${axis.id}" data-option="${opt.id}"
                 ${isBlocked || disabled ? 'disabled' : ''}>${opt.zh}</button>`;
      }).join('')}
    </div>
  `).join('');
  _renderedChipsKey = key;

  host.querySelectorAll('.scene-chip').forEach(btn => {
    btn.onclick = () => _pickSceneOption(ctx, btn.dataset.axis, btn.dataset.option);
  });
}

async function _pickSceneOption(ctx, axis, option) {
  const { $ } = ctx;
  const previous = _currentScene(ctx);
  _setScene(ctx, { ...previous, [axis]: option });
  try {
    const composed = await ctx.api('/api/keyframes/compose', {
      method: 'POST',
      body: JSON.stringify({ scene: _currentScene(ctx) }),
    });
    _setScene(ctx, composed.scene);
    $('#posPrompt').value = composed.positive_prompt;
    _renderTokenCount($, composed.estimated_tokens, (_cachedPresets?.limits?.training_limit) || 256);
    $('#sceneNotice').textContent = composed.warnings.join(' ');
  } catch (err) {
    // 不合法的組合（例如夏天下雪）回捲到上一個狀態，而不是把畫面留在
    // 一個後端根本不接受的選擇上。
    _setScene(ctx, previous);
    $('#sceneNotice').textContent = err.message;
  }
  await _renderScenePresets(ctx, { disabled: false });
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
    // 這一集上次是用哪組晶片生的就把晶片還原成那組。功能出現之前生的
    // 集數 payload 裡沒有 scene，此時維持 null，晶片顯示為後端預設但不
    // 會覆蓋已經填好的文字。
    if (!_currentScene(ctx) && payload.scene) _setScene(ctx, payload.scene);
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
      if (!_currentScene(ctx) && defaults.scene) _setScene(ctx, defaults.scene);
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
  // 晶片跟其他控制項一樣，在生成中或已定案之後就不該再動。
  await _renderScenePresets(ctx, { disabled: isGenerating || hasApproved });
  _renderTokenCount($, _estimateTokens($('#posPrompt').value), 256);

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
  // 手動編輯時也要更新計數器——晶片組出來的一定在預算內，會超標的一律是
  // 人自己貼進去的文字。
  $('#posPrompt').addEventListener('input', () => {
    _renderTokenCount($, _estimateTokens($('#posPrompt').value), 256);
  });
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
  // 把場景一起送上去，後端會順便把配套的兩段動作提示詞存進 task
  // payload，頁籤 2 才拿得到跟這張關鍵幀同場景的動作文字。
  const scene = _currentScene(ctx);
  if (scene) body.scene = scene;
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


// scene_composer.py 的 estimate_t5_tokens() 的前端版本，係數必須跟後端
// 一致（1.25/字 + 1.6/標點，是拿 ComfyUI 自帶的 T5 tokenizer 回歸出來
// 的，刻意偏高）。只用在人手動編輯時的即時回饋；晶片組出來的數字一律
// 以後端回傳的為準。
function _estimateTokens(text) {
  if (!text) return null;
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  const punctuation = (text.match(/[.,;:()/-]/g) || []).length;
  return Math.ceil(words * 1.25 + punctuation * 1.6);
}
