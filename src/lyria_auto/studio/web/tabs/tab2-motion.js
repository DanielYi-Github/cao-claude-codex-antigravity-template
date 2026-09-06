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

// Fetched once per page load, not per 3s poll: the catalog is static
// config and re-populating the <select>s on every render would reset
// whatever the reviewer had just picked.
let veoConfig = null;

async function loadVeoConfig(ctx) {
  const { $, api } = ctx;
  if (veoConfig) return veoConfig;
  try {
    veoConfig = await api('/api/veo/config');
  } catch {
    veoConfig = { enabled: false, models: [] };
  }

  const modelSel = $('#motionModel');
  if (modelSel) {
    modelSel.innerHTML = veoConfig.models.map(m => {
      const price = m.usd_per_second != null
        ? `　約 $${m.estimated_usd_per_pair.toFixed(2)} / 兩支`
        : '';
      const loopWarning = m.supports_last_frame ? '' : '　⚠ 未確認可循環';
      return `<option value="${m.id}" ${m.available ? '' : 'disabled'}>`
        + `${escapeHtml(m.label)}${price}${loopWarning}`
        + `${m.available ? '' : '（金鑰取用不到）'}</option>`;
    }).join('');
    if (veoConfig.default_model) modelSel.value = veoConfig.default_model;
  }
  syncResolutionOptions(ctx);
  updateKeyRow(ctx);
  updateCostNote(ctx);
  return veoConfig;
}

// The resolution list is per-model -- offering 720p for a 1080p-only
// model would just produce a 400 from the server-side validator.
function syncResolutionOptions(ctx) {
  const { $ } = ctx;
  const modelSel = $('#motionModel');
  const resSel = $('#motionResolution');
  if (!modelSel || !resSel || !veoConfig) return;
  const entry = veoConfig.models.find(m => m.id === modelSel.value);
  const options = entry ? entry.resolutions : [];
  const previous = resSel.value;
  resSel.innerHTML = options.map(r => `<option value="${r}">${r}</option>`).join('');
  if (options.includes(previous)) {
    resSel.value = previous;
  } else if (options.includes(veoConfig.default_resolution)) {
    resSel.value = veoConfig.default_resolution;
  }
}

function updateKeyRow(ctx) {
  const { $ } = ctx;
  const row = $('#veoKeyRow');
  if (!row || !veoConfig) return;
  const usingVeo = ($('#motionProvider') || {}).value === 'veo';
  // Only asked for when .env did not supply one; a key typed here lives
  // in the server's memory for this process only.
  row.classList.toggle('hidden', !usingVeo || veoConfig.key_configured);
}

function updateCostNote(ctx) {
  const { $ } = ctx;
  const note = $('#veoCostNote');
  if (!note || !veoConfig) return;
  const usingVeo = ($('#motionProvider') || {}).value === 'veo';
  if (!usingVeo) {
    note.textContent = '本機生成不計費，但速度取決於這台機器。';
    return;
  }
  const entry = veoConfig.models.find(m => m.id === ($('#motionModel') || {}).value);
  if (!entry) {
    note.textContent = '';
    return;
  }
  const key = veoConfig.key_configured
    ? `金鑰 ${veoConfig.key_masked}`
    : '尚未設定金鑰';
  // Deliberately worded as an estimate: settings.yaml's per-second
  // figures are an unverified snapshot (studio.veo.pricing_verified).
  note.textContent =
    `預估費用約 US$${entry.estimated_usd_per_pair.toFixed(2)}`
    + `（兩支 × ${veoConfig.duration_seconds} 秒，未校對的估價快照`
    + `${veoConfig.pricing_snapshot_date ? ' ' + veoConfig.pricing_snapshot_date : ''}`
    + `，實際以 Google 帳單為準）。${key}。`;
}

function toggleVeoOnlyFields(ctx) {
  const { $, $$ } = ctx;
  const usingVeo = ($('#motionProvider') || {}).value === 'veo';
  $$('.veo-only', $('#tab-2')).forEach(el => el.classList.toggle('hidden', !usingVeo));
  updateKeyRow(ctx);
  updateCostNote(ctx);
}

// 以 episode 為單位快取，不是整個模組共用一份：動作提示詞現在會跟著這一
// 集的關鍵幀場景走（頁籤 1 的晶片挑了湖畔露台，這裡就該預填湖畔露台的
// 動作文字），共用一份快取會把上一集的場景帶到下一集。
const _cachedMotionDefaults = new Map();

// 目前核准的是哪一張關鍵幀。快取鍵要帶上它，不能只用 episode id：頁籤 2
// 在還沒有關鍵幀時就會渲染一次（此時後端只能回全域預設），只用 episode id
// 當鍵的話那一份預設會一直被沿用——人後來在頁籤 1 挑了湖畔戶外露台、核准、
// 再回到頁籤 2，四個按鈕仍會餵給 Veo 森林木屋的室內木地板文字，正是這次要
// 修的東西。關鍵幀換了就換鍵，快取自然失效。
let _approvedKeyframeId = null;

// 我們上一次自動填進兩個欄位的字。用來分辨「使用者沒動過」與「使用者改過」，
// 只有前者可以被新的推導結果覆寫。
const _prefilled = {};

async function fetchMotionDefaults(ctx) {
  const episodeId = ctx.getEpisodeId();
  const key = `${episodeId ?? 'global'}|${_approvedKeyframeId ?? 'none'}`;
  if (_cachedMotionDefaults.has(key)) return _cachedMotionDefaults.get(key);
  let defaults;
  try {
    const query = episodeId ? `?episode_id=${episodeId}` : '';
    defaults = await ctx.api(`/api/motion/defaults${query}`);
  } catch {
    // 同 tab1：拿不到就退回手動編輯，但四個環境主題按鈕會變成按了沒反應，
    // 所以要講出來而不是靜默降級。
    defaults = { sleep_prompt: '', lookup_prompt: '', presets: {}, unavailable: true };
  }
  // 失敗的結果不進快取——否則重啟伺服器後，這一頁會一直拿著那份失敗結果，
  // 直到使用者整頁重載為止。
  if (!defaults.unavailable) _cachedMotionDefaults.set(key, defaults);
  return defaults;
}

// 套用一個環境主題。模板已經不在前端了——後端會依這一集的晶片選擇把
// 姿勢、地面、開口形式、光線填好再回傳，前端只負責放進 textarea。
// 這裡原本各自寫死一份 sleep/lookup 長模板，無論頁籤 1 選了什麼都宣稱狗
// 趴平在室內咖啡館地板、外面有溫暖陽光（artifacts/spec.md「動作提示詞與
// 關鍵幀場景的接縫」A、C、D、E）。
function applyEnvironmentalClause(ctx, preset) {
  const { $ } = ctx;
  const sleepPromptEl = $('#sleepPrompt');
  const lookupPromptEl = $('#lookupPrompt');
  const statusEl = $('#motionStatus');
  if (!sleepPromptEl || !lookupPromptEl) return;
  if (!preset.sleep_prompt || !preset.lookup_prompt) return;

  sleepPromptEl.value = preset.sleep_prompt;
  lookupPromptEl.value = preset.lookup_prompt;
  if (statusEl) statusEl.textContent = `✓ 已套用「${preset.label}」環境動態提示詞。`;
}

function ensureTabScaffold(ctx) {
  const { $ } = ctx;
  const tab2 = $('#tab-2');
  if (!tab2) return;

  if (!$('#motion-players', tab2)) {
    tab2.innerHTML = `
      <div class="card">
        <h3>2. 動作生成與審核 (Motion Test)</h3>
        <p class="muted">從已核准的關鍵幀生成 8 秒睡眠 (Sleep) 與抬頭 (Lookup) 兩支循環片段。兩者皆核准後，方可組裝 64 秒低解析度預覽。</p>

        <div class="generate-controls">
          <div class="form-group">
            <label for="motionProvider">生成來源</label>
            <select id="motionProvider">
              <option value="veo">Google Veo（雲端，較快）</option>
              <option value="comfyui">本機 ComfyUI（免費，較慢）</option>
            </select>
          </div>
          <div class="form-group veo-only">
            <label for="motionModel">影片模型</label>
            <select id="motionModel"></select>
          </div>
          <div class="form-group veo-only">
            <label for="motionResolution">解析度</label>
            <select id="motionResolution"></select>
          </div>
        </div>

        <div id="veoKeyRow" class="hidden">
          <div class="inline-form">
            <label for="veoApiKey">GEMINI_API_KEY：</label>
            <input id="veoApiKey" type="password" autocomplete="off"
                   placeholder="尚未從 .env 讀到金鑰，請在此輸入">
            <button id="saveVeoKey" type="button">儲存</button>
          </div>
          <p class="muted">
            ⚠ 在這裡輸入的金鑰只存在伺服器記憶體，重啟後就消失。
            重啟後若有還沒收回的付費生成，程式無法續接，那筆錢就白花了。
            要讓已付費的生成能在重啟後續跑，請把金鑰寫進專案根目錄的
            <code>.env</code>（<code>GEMINI_API_KEY=...</code>）。
          </p>
        </div>

        <p id="veoCostNote" class="muted" aria-live="polite"></p>

        <div class="preset-row" style="margin: 1rem 0 0.75rem; display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center;">
          <span class="muted" style="font-size: 0.9em; font-weight: 500;">環境動態預設：</span>
          <button type="button" class="btn btn-sm env-preset-btn" data-preset="nature_breeze">🍃 微風山水湖光</button>
          <button type="button" class="btn btn-sm env-preset-btn" data-preset="rainy_window">🌧️ 窗外細雨</button>
          <button type="button" class="btn btn-sm env-preset-btn" data-preset="urban_sunset">🏙️ 城市暮光街景</button>
          <button type="button" class="btn btn-sm env-preset-btn" data-preset="forest_woods">🌲 森林微風</button>
          <button type="button" class="btn btn-sm" id="suggestMotionPrompts" style="margin-left: auto;">🔍 從關鍵幀分析環境動態</button>
        </div>

        <p id="sceneStaleNotice" class="field-hint warn" hidden></p>

        <div class="prompt-grid">
          <div class="form-group">
            <label for="sleepPrompt">睡眠動作提示詞 (Sleep Prompt，佔循環 7/8)</label>
            <textarea id="sleepPrompt" rows="6" placeholder="8 秒睡眠動作提示詞..."></textarea>
          </div>
          <div class="form-group">
            <label for="lookupPrompt">抬頭動作提示詞 (Lookup Prompt，佔循環 1/8)</label>
            <textarea id="lookupPrompt" rows="6" placeholder="8 秒抬頭動作提示詞..."></textarea>
          </div>
        </div>

        <button id="generateMotion">開始生成兩支動作片段</button>
        <button id="cancelMotion" class="stop-btn hidden">終止 (Stop)</button>
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
  const approvedKeyframes = assets.filter(a => a.kind === 'keyframe' && a.status === 'approved');
  _approvedKeyframeId = approvedKeyframes.length
    ? approvedKeyframes[approvedKeyframes.length - 1].id
    : null;
  const motionAssets = assets.filter(a => a.kind === 'motion_test');
  const loopPreviewAssets = assets.filter(a => a.kind === 'loop_preview');

  // Covers both backends: the Veo path runs as a start task followed by
  // repeated poll tasks, and the tab is "busy" for the whole sequence.
  const MOTION_TASK_TYPES = ['generate_motion_test', 'start_veo_motion', 'poll_veo_motion'];
  const isGeneratingMotion = tasks.some(
    t => MOTION_TASK_TYPES.includes(t.task_type) && ['queued', 'running'].includes(t.status)
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

  // Pre-fill prompt textareas if empty (from existing assets or defaults)
  const activeSleep = motionAssets.find(a => a.role === 'sleep' && a.status !== 'superseded');
  const activeLookup = motionAssets.find(a => a.role === 'lookup' && a.status !== 'superseded');
  if (activeSleep && activeSleep.source_prompt && !$('#sleepPrompt').value) {
    $('#sleepPrompt').value = activeSleep.source_prompt;
  }
  if (activeLookup && activeLookup.source_prompt && !$('#lookupPrompt').value) {
    $('#lookupPrompt').value = activeLookup.source_prompt;
  }
  fetchMotionDefaults(ctx).then(defaults => {
    // 只有「空的」或「還是我們上次填進去的那串、使用者沒動過」才覆寫。
    // 少了後半條，在關鍵幀核准之前渲染過一次頁籤 2 的人會一直看著全域預設
    // （森林木屋／室內木地板），因為欄位已經不是空的了——快取換鍵也救不了。
    // 使用者自己改過的字則絕不覆蓋。
    const fill = (sel, next) => {
      const el = $(sel);
      if (!el || !next) return;
      if (el.value && el.value !== _prefilled[sel]) return;
      el.value = next;
      _prefilled[sel] = next;
    };
    fill('#sleepPrompt', defaults.sleep_prompt);
    fill('#lookupPrompt', defaults.lookup_prompt);
    // 動作提示詞是從頁籤 1 的晶片選擇推導的。如果那邊的提示詞被手動改過
    // 而晶片沒動，推導出來的字可能與實際畫面不符，且不會有任何跡象——
    // 所以講出來，而不是安靜地填進去。
    const notice = $('#sceneStaleNotice');
    if (notice && defaults.unavailable) {
      notice.hidden = false;
      notice.textContent = '⚠️ 讀不到動作提示詞預設（/api/motion/defaults）。'
        + '最常見的原因是伺服器行程比程式碼舊——重新啟動 lyria-auto studio 後再重新整理。'
        + '在那之前環境主題按鈕會沒有反應，請直接手動編輯下面的文字。';
    } else if (notice) {
      notice.hidden = !defaults.scene_stale;
      notice.textContent = defaults.scene_stale
        ? '⚠️ 頁籤 1 的關鍵幀提示詞被手動改過，但場景晶片沒有跟著改。'
          + '下面的動作提示詞是依晶片推導的，請自行確認是否與實際畫面相符。'
        : '';
    }
  });

  const sleepPromptEl = $('#sleepPrompt');
  const lookupPromptEl = $('#lookupPrompt');
  if (sleepPromptEl) sleepPromptEl.disabled = isGeneratingMotion || bothApproved;
  if (lookupPromptEl) lookupPromptEl.disabled = isGeneratingMotion || bothApproved;
  const suggestBtn = $('#suggestMotionPrompts');
  if (suggestBtn) suggestBtn.disabled = !hasKeyframeApproved || isGeneratingMotion || bothApproved;
  $$('.env-preset-btn').forEach(b => { b.disabled = isGeneratingMotion || bothApproved; });

  // 1. Update Motion Status message
  const hasAnyMotion = motionAssets.some(a => a.status !== 'superseded');

  // Approving a keyframe no longer starts generation on its own -- the
  // backend needs a provider/model/resolution first, so this tab owns
  // the trigger (see app.py's _continue_after_approval keyframe branch).
  const generateBtn = $('#generateMotion');
  if (generateBtn) {
    generateBtn.disabled = !hasKeyframeApproved || isGeneratingMotion || bothApproved;
    generateBtn.textContent = hasAnyMotion
      ? '重新生成兩支動作片段'
      : '開始生成兩支動作片段';
  }

  let motionStatusText = '';
  if (!hasKeyframeApproved) {
    motionStatusText = '尚未選定關鍵幀。請先在 Tab 1 完成關鍵幀生成與核准。';
  } else if (isGeneratingMotion) {
    motionStatusText = '正在生成動作片段 (Sleep / Lookup)... 請稍候。';
  } else if (!hasAnyMotion) {
    motionStatusText = '關鍵幀已核准。請在上方選好生成來源與模型後開始生成。';
  } else if (bothApproved) {
    motionStatusText = 'Sleep 與 Lookup 動作片段皆已核准。';
  } else {
    motionStatusText = '請審核下方動作片段，可核准或重新生成。';
  }

  const motionStatusEl = $('#motionStatus');
  if (motionStatusEl && motionStatusEl.textContent !== motionStatusText) {
    motionStatusEl.textContent = motionStatusText;
  }

  // Only offer Stop while something's actually running -- there's nothing
  // to cancel otherwise.
  const cancelBtn = $('#cancelMotion');
  if (cancelBtn) cancelBtn.classList.toggle('hidden', !isGeneratingMotion);

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
              <span class="muted">${hasKeyframeApproved
                ? '請按上方「開始生成兩支動作片段」'
                : '需先在 Tab 1 完成關鍵幀選定'}</span>
            </div>
          </div>
        `;
      }

      const isApproved = asset.status === 'approved';
      const isAwaiting = asset.status === 'awaiting_review';
      const isQueuedOrRunning = ['queued', 'running'].includes(asset.status);
      const isRejected = asset.status === 'rejected';
      // A cancelled-mid-generation asset lands here too (cancel_motion_
      // generation in app.py transitions it to 'failed') -- the backend's
      // reject endpoint accepts 'failed' the same as 'awaiting_review', so
      // Regenerate needs to be offered here too, not just for a completed
      // batch the reviewer didn't like.
      const isFailed = asset.status === 'failed';
      const errorHtml = asset.error ? `<p class="error">${escapeHtml(asset.error)}</p>` : '';

      let mediaHtml = '';
      if (asset.path) {
        mediaHtml = `<video controls preload="metadata" src="/api/artifact/${asset.id}" class="motion-video"></video>`;
      } else {
        mediaHtml = `<div class="placeholder-video">${STATUS_LABEL[asset.status] || '生成中...'}</div>`;
      }

      // seam_score is the first/last frame difference: near 0 means the
      // clip really does end where it started. It is the only signal that
      // catches a model quietly ignoring last_frame, which would leave a
      // clip that does not loop -- so it is shown, not hidden.
      const provenanceBits = [];
      if (asset.provider === 'google-veo') {
        provenanceBits.push(`來源：Google Veo${asset.model ? ' / ' + escapeHtml(asset.model) : ''}`);
        if (asset.width && asset.height) provenanceBits.push(`${asset.width}×${asset.height}`);
        if (asset.estimated_cost_usd != null) {
          provenanceBits.push(`預估 US$${Number(asset.estimated_cost_usd).toFixed(2)}`);
        }
      } else if (asset.path) {
        provenanceBits.push('來源：本機 ComfyUI');
      }
      if (asset.seam_score != null) {
        const seamOk = asset.seam_score <= 0.06;
        provenanceBits.push(
          `${seamOk ? '✓' : '⚠'} 循環接縫 ${Number(asset.seam_score).toFixed(4)}`
          + `${seamOk ? '' : '（首尾差異偏大，可能沒有真的循環）'}`
        );
      }
      const provenanceHtml = provenanceBits.length
        ? `<p class="muted provenance">${provenanceBits.join('　·　')}</p>`
        : '';

      let actionsHtml = '';
      if (isAwaiting || isFailed) {
        actionsHtml = `
          ${isAwaiting ? `<button class="btn approve-btn motion-approve-btn" data-id="${asset.id}" data-version="${asset.state_version}">核准此動作</button>` : ''}
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
        <div class="motion-card ${isApproved ? 'approved' : ''} ${isRejected || isFailed ? 'rejected' : ''}">
          <div class="motion-meta">
            <span class="motion-title">${escapeHtml(title)}</span>
            <div>
              <span class="badge">${STATUS_LABEL[asset.status] || asset.status}</span>
              ${asset.variant_index != null ? `<span class="muted">v${asset.variant_index + 1}</span>` : ''}
            </div>
          </div>
          <p class="muted">${escapeHtml(desc)}</p>
          ${mediaHtml}
          ${provenanceHtml}
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
  const cancelBtn = $('#cancelMotion');
  if (cancelBtn) {
    cancelBtn.onclick = () => cancelMotion(ctx);
  }

  const providerSel = $('#motionProvider');
  if (providerSel) providerSel.onchange = () => toggleVeoOnlyFields(ctx);
  const modelSel = $('#motionModel');
  if (modelSel) {
    modelSel.onchange = () => {
      syncResolutionOptions(ctx);
      updateCostNote(ctx);
    };
  }
  const generateBtn = $('#generateMotion');
  if (generateBtn) generateBtn.onclick = () => generateMotion(ctx);
  const saveKeyBtn = $('#saveVeoKey');
  if (saveKeyBtn) saveKeyBtn.onclick = () => saveVeoKey(ctx);

  // Environmental presets buttons
  const { $$ } = ctx;
  $$('.env-preset-btn').forEach(btn => {
    btn.onclick = async () => {
      const key = btn.dataset.preset;
      const defaults = await fetchMotionDefaults(ctx);
      const preset = defaults?.presets?.[key];
      if (preset) {
        applyEnvironmentalClause(ctx, preset);
      }
    };
  });

  // Suggest motion prompts via keyframe / vision analysis
  const suggestBtn = $('#suggestMotionPrompts');
  if (suggestBtn) {
    suggestBtn.onclick = async () => {
      const episodeId = ctx.getEpisodeId();
      if (!episodeId) return;
      const statusEl = $('#motionStatus');
      suggestBtn.disabled = true;
      if (statusEl) statusEl.textContent = '正在依據當前關鍵幀分析環境動態...';
      try {
        const result = await ctx.api(`/api/episodes/${episodeId}/motion/suggest-prompts`, {
          method: 'POST',
        });
        if ($('#sleepPrompt') && result.sleep_prompt) $('#sleepPrompt').value = result.sleep_prompt;
        if ($('#lookupPrompt') && result.lookup_prompt) $('#lookupPrompt').value = result.lookup_prompt;
        const sourceText = result.source === 'gemini-vision' ? 'Gemini 視覺辨識' : '關鍵幀特徵分析';
        if (statusEl) statusEl.textContent = `✓ 已依據【${result.scene_detected}】（${sourceText}）更新提示詞！`;
      } catch (err) {
        if (statusEl) statusEl.textContent = err.message;
      } finally {
        suggestBtn.disabled = false;
      }
    };
  }

  if ($('#motionProvider')) loadVeoConfig(ctx).then(() => toggleVeoOnlyFields(ctx));
}

async function saveVeoKey(ctx) {
  const { $, api } = ctx;
  const input = $('#veoApiKey');
  const statusEl = $('#motionStatus');
  const key = (input?.value || '').trim();
  if (!key) {
    if (statusEl) statusEl.textContent = '請先輸入 API 金鑰。';
    return;
  }
  try {
    const result = await api('/api/veo/credential', {
      method: 'POST',
      body: JSON.stringify({ api_key: key }),
    });
    // Clear the field immediately: the key now lives in the server's
    // memory and there is no reason for it to stay on screen.
    input.value = '';
    veoConfig.key_configured = result.key_configured;
    veoConfig.key_masked = result.key_masked;
    updateKeyRow(ctx);
    updateCostNote(ctx);
    if (statusEl) statusEl.textContent = `金鑰已設定（${result.key_masked}）。`;
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
  }
}

async function generateMotion(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const btn = $('#generateMotion');
  const statusEl = $('#motionStatus');
  const sleepPrompt = ($('#sleepPrompt')?.value || '').trim();
  const lookupPrompt = ($('#lookupPrompt')?.value || '').trim();
  // Read straight off the controls at click time rather than from any
  // cached render state -- app.js skips re-rendering when the episode
  // snapshot is unchanged, so a cached copy could easily be stale.
  const body = {
    provider: $('#motionProvider').value,
    model: $('#motionModel').value || null,
    resolution: $('#motionResolution').value || null,
    sleep_prompt: sleepPrompt || null,
    lookup_prompt: lookupPrompt || null,
  };
  try {
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.textContent = '正在送出生成請求...';
    await api(`/api/episodes/${episodeId}/motion/generate`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    await refresh();
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function cancelMotion(ctx) {
  const { $, api, getEpisodeId, refresh } = ctx;
  const episodeId = getEpisodeId();
  if (!episodeId) return;
  const btn = $('#cancelMotion');
  const statusEl = $('#motionStatus');
  try {
    if (btn) btn.disabled = true;
    if (statusEl) statusEl.textContent = '正在終止...';
    const result = await api(`/api/episodes/${episodeId}/motion/cancel`, { method: 'POST' });
    await refresh();
    // A started Veo operation is already billed and cannot be called
    // off -- say so rather than letting "terminated" imply a refund.
    if (result && result.abandoned_paid_operations > 0 && statusEl) {
      statusEl.textContent =
        `已停止接收結果，但其中 ${result.abandoned_paid_operations} 次 Veo 生成`
        + '已經送出並計費，取消無法退費。';
    }
  } catch (err) {
    alert(err.message);
    await refresh();
  } finally {
    if (btn) btn.disabled = false;
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
  const editedPrompt = (role === 'sleep' ? $('#sleepPrompt')?.value : $('#lookupPrompt')?.value)?.trim() || null;
  const reason = prompt(`請輸入重新生成 ${roleLabel} 的原因（選填，直接按確定將套用上方編輯之提示詞）：`, '');
  if (reason === null) return; // Cancelled

  const statusEl = $('#motionStatus');
  try {
    if (statusEl) statusEl.textContent = `正在請求重新生成 ${roleLabel}...`;
    await api(`/api/episodes/${episodeId}/assets/${assetId}/reject`, {
      method: 'POST',
      body: JSON.stringify({
        expected_version: Number(expectedVersion),
        reason: reason || '使用者要求重新生成',
        new_prompt: editedPrompt,
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
