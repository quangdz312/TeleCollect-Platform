(() => {
  const STORAGE_KEY = 'telecollect.local.review.v1';
  const PAGE_SIZE = 50;
  let searchTimer = null;
  let lastShellPath = '';
  const state = {
    episodes: [],
    batches: [],
    project: null,
    selected: new Set(),
    group: 'all',
    search: '',
    source: 'all',
    status: 'all',
    autoLabel: 'all',
    task: 'all',
    page: 1,
    loading: false,
    loaded: false,
  };

  const nativeFetch = window.fetch.bind(window);
  const canonicalTask = (task) => ({lift: 'lift_cube', can: 'pick_place_can', square: 'nut_assembly_square', assemble_square: 'nut_assembly_square'})[String(task || '').toLowerCase()] || String(task || '').toLowerCase();
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? String(input) : input.url, location.origin);
    const method = String(init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
    const isTeleop = method === 'POST' && url.pathname.endsWith('/api/v1/teleop/sessions');
    const isScripted = method === 'POST' && (
      url.pathname.endsWith('/api/v1/labeling/runs')
      || url.pathname.endsWith('/api/v1/labeling/collection-runs')
    );
    if (isTeleop || isScripted) {
      const activeResponse = await nativeFetch('/api/v1/local/batches/active');
      const active = activeResponse.ok ? await activeResponse.json() : null;
      if (!active) return new Response(JSON.stringify({detail: 'Choose a batch before collecting data.'}), {status: 409, headers: {'Content-Type': 'application/json'}});
      let body = {};
      try { body = JSON.parse(String(init.body || '{}')); } catch (_) { /* The source UI always sends JSON. */ }
      const task = canonicalTask(body.task_name || body.task);
      if (task && task !== active.task) return new Response(JSON.stringify({detail: `Active batch "${active.name}" belongs to ${active.task}. Choose a matching batch.`}), {status: 409, headers: {'Content-Type': 'application/json'}});
      if (isScripted) body.collection_batch_id = active.id;
      init = {...init, body: JSON.stringify(body)};
    }
    return nativeFetch(input, init);
  };

  const NativeWebSocket = window.WebSocket;
  const manualEpisodeBatches = new Map();
  window.WebSocket = class TeleCollectLocalWebSocket extends NativeWebSocket {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', (event) => {
        if (typeof event.data !== 'string') return;
        let message;
        try { message = JSON.parse(event.data); } catch (_) { return; }
        if (message?.type === 'recording_started' && message.episode_id) {
          nativeFetch('/api/v1/local/batches/active')
            .then((response) => response.ok ? response.json() : null)
            .then((batch) => { if (batch?.id) manualEpisodeBatches.set(String(message.episode_id), batch.id); })
            .catch(() => {});
        }
        if (message?.type === 'recording_saved' && message.episode_id) {
          const episodeId = String(message.episode_id);
          const remembered = manualEpisodeBatches.get(episodeId);
          manualEpisodeBatches.delete(episodeId);
          void (async () => {
            let batchId = remembered;
            if (!batchId) {
              const response = await nativeFetch('/api/v1/local/batches/active');
              const batch = response.ok ? await response.json() : null;
              batchId = batch?.id;
            }
            if (!batchId) return;
            await nativeFetch(`/api/v1/local/episodes/${encodeURIComponent(episodeId)}/batch`, {
              method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: batchId}),
            });
            await loadReview(true);
          })().catch(() => {});
        }
        if (message?.type === 'recording_discarded' && message.episode_id) manualEpisodeBatches.delete(String(message.episode_id));
      });
    }
  };

  const api = async (url, options = {}) => {
    const response = await nativeFetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.error || `Request failed (${response.status})`);
    return payload;
  };

  const button = (label, className = 'tc-secondary') => {
    const item = document.createElement('button');
    item.type = 'button'; item.className = className; item.textContent = label;
    return item;
  };

  const saveState = () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      selected: [...state.selected], group: state.group, search: state.search,
      source: state.source, status: state.status, autoLabel: state.autoLabel,
      task: state.task, page: state.page,
    }));
  };

  const restoreState = () => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '{}');
      state.selected = new Set(Array.isArray(saved.selected) ? saved.selected : []);
      for (const key of ['group', 'search', 'source', 'status', 'autoLabel', 'task']) {
        if (typeof saved[key] === 'string') state[key] = saved[key];
      }
      if (Number.isInteger(saved.page) && saved.page > 0) state.page = saved.page;
    } catch (_) { /* Ignore a stale desktop session. */ }
  };

  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[char]);

  const compare = (a, b) => String(a).localeCompare(String(b), undefined, {numeric: true, sensitivity: 'base'});
  const groupItems = (key) => {
    if (key === 'all') return state.episodes;
    const [kind, value] = key.split(':', 2);
    if (kind === 'source') return state.episodes.filter((item) => item.source === value);
    if (kind === 'task') return state.episodes.filter((item) => item.task === value);
    if (kind === 'batch') return state.episodes.filter((item) => item.batch_id === value);
    return state.episodes;
  };

  const filteredItems = () => {
    const query = state.search.trim().toLowerCase();
    return groupItems(state.group).filter((item) => {
      if (state.source !== 'all' && item.source !== state.source) return false;
      if (state.status !== 'all' && item.status !== state.status) return false;
      if (state.autoLabel !== 'all' && item.auto_label !== state.autoLabel) return false;
      if (state.task !== 'all' && item.task !== state.task) return false;
      if (query && ![item.title, item.id, item.task, item.source, item.batch_name, ...(item.tags || [])].join(' ').toLowerCase().includes(query)) return false;
      return !item.trashed;
    }).sort((a, b) => compare(a.title, b.title));
  };

  const setCheckboxState = (checkbox, items) => {
    const count = items.reduce((total, item) => total + Number(state.selected.has(item.id)), 0);
    checkbox.checked = items.length > 0 && count === items.length;
    checkbox.indeterminate = count > 0 && count < items.length;
  };

  const setGroupSelected = (items, selected) => {
    items.filter((item) => !item.trashed).forEach((item) => selected ? state.selected.add(item.id) : state.selected.delete(item.id));
    saveState(); renderReview();
  };

  const select = (values, current, label) => {
    const element = document.createElement('select');
    element.setAttribute('aria-label', label);
    values.forEach(([value, text]) => {
      const option = document.createElement('option'); option.value = value; option.textContent = text;
      option.selected = value === current; element.appendChild(option);
    });
    return element;
  };

  const badge = (value, kind = value) => `<span class="tc-badge tc-${esc(kind)}">${esc(value)}</span>`;

  const groups = () => {
    const tasks = [...new Set(state.episodes.map((item) => item.task))].sort(compare);
    return [
      {heading: '', items: [['all', 'All data']]},
      {heading: 'Batches', items: state.batches.map((batch) => [`batch:${batch.id}`, batch.name])},
      {heading: 'Source', items: [['source:teleop', 'Teleop'], ['source:scripted', 'Scripted']]},
      {heading: 'Task', items: tasks.map((value) => [`task:${value}`, value])},
    ];
  };

  const renderSidebar = (host) => {
    groups().forEach((section) => {
      if (section.heading) {
        const heading = document.createElement('div'); heading.className = 'tc-tree-heading'; heading.textContent = section.heading; host.appendChild(heading);
      }
      section.items.forEach(([key, label]) => {
        const items = groupItems(key).filter((item) => !item.trashed);
        if (!items.length && key !== 'all') return;
        const row = document.createElement('div'); row.className = `tc-tree-row${state.group === key ? ' active' : ''}`;
        const check = document.createElement('input'); check.type = 'checkbox'; check.title = `Select all ${label}`;
        setCheckboxState(check, items);
        check.onclick = (event) => event.stopPropagation();
        check.onchange = () => {
          // A folder checkbox both opens that folder and selects its content.
          // This matches the user's File Explorer mental model and avoids the
          // surprising "Teleop checked while Scripted rows remain visible".
          state.group = key; state.page = 1;
          setGroupSelected(items, check.checked);
        };
        const folder = document.createElement('button'); folder.type = 'button'; folder.innerHTML = `<span class="tc-folder">▸</span><span>${esc(label)}</span><span class="tc-count">${items.length}</span>`;
        folder.onclick = () => { state.group = key; state.page = 1; saveState(); renderReview(); };
        row.append(check, folder); host.appendChild(row);
      });
    });
  };

  const openProject = async () => {
    document.getElementById('tc-project-dialog')?.remove();
    const project = await api('/api/v1/local/project');
    const overlay = document.createElement('div'); overlay.id = 'tc-project-dialog'; overlay.className = 'tc-overlay';
    const card = document.createElement('section'); card.className = 'tc-dialog tc-project-dialog';
    card.innerHTML = `<div class="tc-dialog-head"><div><h2>Project folder</h2><p>Review and export only use data inside this folder.</p></div><button class="tc-icon-button" aria-label="Close">×</button></div><div class="tc-path">${esc(project.path)}</div>`;
    card.querySelector('.tc-icon-button').onclick = () => overlay.remove();
    const actions = document.createElement('div'); actions.className = 'tc-dialog-actions';
    const change = button('Change folder…', 'tc-primary');
    const result = document.createElement('span'); result.className = 'tc-muted';
    change.onclick = async () => {
      try {
        const pick = window.pywebview?.api?.choose_data_folder ? window.pywebview.api.choose_data_folder() : window.telecollectLocal?.chooseFolder?.();
        const chosen = await pick; const path = chosen?.path || (typeof chosen === 'string' ? chosen : '');
        if (!path) return;
        change.disabled = true; result.textContent = 'Opening project…';
        await api('/__telecollect_local/switch', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({path})});
        sessionStorage.removeItem(STORAGE_KEY); location.assign('/review');
      } catch (error) { result.textContent = error.message || String(error); change.disabled = false; }
    };
    actions.append(change, result); card.appendChild(actions); overlay.appendChild(card); document.body.appendChild(overlay);
  };

  const openExport = async () => {
    if (!state.selected.size) return;
    document.getElementById('tc-export-dialog')?.remove();
    const selected = state.episodes.filter((item) => state.selected.has(item.id));
    const lerobotReady = selected.filter((item) => item.source === 'teleop' && item.complete_for_lerobot).length;
    const overlay = document.createElement('div'); overlay.id = 'tc-export-dialog'; overlay.className = 'tc-overlay';
    const card = document.createElement('section'); card.className = 'tc-dialog';
    card.innerHTML = `<div class="tc-dialog-head"><div><h2>Export selected data</h2><p>${selected.length.toLocaleString()} episode(s) selected</p></div><button class="tc-icon-button" aria-label="Close">×</button></div>`;
    card.querySelector('.tc-icon-button').onclick = () => overlay.remove();
    const form = document.createElement('div'); form.className = 'tc-export-form';
    const name = document.createElement('input'); name.value = `dataset_${new Date().toISOString().slice(0, 10)}`;
    const format = select([['hdf5', 'HDF5 (RoboMimic)'], ['lerobot', 'LeRobot v3']], 'hdf5', 'Export format');
    const info = document.createElement('div'); info.className = 'tc-export-info';
    const updateInfo = () => {
      info.textContent = format.value === 'hdf5'
        ? `HDF5 will export ${selected.filter((item) => item.has_trajectory).length} trajectory episode(s), split into one file per task when needed.`
        : `LeRobot can export ${lerobotReady} of ${selected.length} selected episode(s). It requires compatible teleop data with top and wrist cameras.`;
    };
    format.onchange = updateInfo; updateInfo();
    const field = (label, control) => { const wrap = document.createElement('label'); wrap.append(label, control); form.appendChild(wrap); };
    field('Dataset name', name); field('Format', format); form.appendChild(info); card.appendChild(form);
    const result = document.createElement('div'); result.className = 'tc-export-result';
    const actions = document.createElement('div'); actions.className = 'tc-dialog-actions';
    const cancel = button('Cancel'); cancel.onclick = () => overlay.remove();
    const run = button('Export', 'tc-primary');
    run.onclick = async () => {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(name.value)) { result.textContent = 'Use letters, digits, dot, underscore or dash for the name.'; return; }
      run.disabled = true; result.textContent = 'Exporting…';
      try {
        const saved = await api('/api/v1/local/exports', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name.value, format: format.value, mode: 'selected', episode_ids: [...state.selected]})});
        result.innerHTML = `<strong>Export complete.</strong><br><span>${esc(saved.episodes)} episode(s), ${esc(saved.frames)} frames</span><div class="tc-path">${esc(saved.path)}</div>`;
      } catch (error) { result.textContent = `Export failed: ${error.message || error}`; }
      finally { run.disabled = false; }
    };
    actions.append(cancel, run); card.append(result, actions); overlay.appendChild(card); document.body.appendChild(overlay);
  };

  const bulkReview = async (decision) => {
    if (!state.selected.size) return;
    const label = decision === 'approved' ? 'approve' : 'reject';
    if (!confirm(`${label[0].toUpperCase() + label.slice(1)} ${state.selected.size} selected episode(s)?`)) return;
    await api('/api/v1/local/review/bulk', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({episode_ids: [...state.selected], decision})});
    await loadReview(true);
  };

  const renderReview = () => {
    const root = document.getElementById('tc-local-review'); if (!root) return;
    if (!state.group.startsWith('batch:')) { renderReviewBatchPicker(); return; }
    const items = filteredItems();
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE)); state.page = Math.min(state.page, pages);
    const start = (state.page - 1) * PAGE_SIZE; const visible = items.slice(start, start + PAGE_SIZE);
    const tasks = [...new Set(state.episodes.map((item) => item.task))].sort(compare);
    const batch = state.batches.find((item) => `batch:${item.id}` === state.group);
    root.innerHTML = `<div class="tc-page-head"><div><button id="tc-back-batches" class="tc-back-button">← Back to batches</button><h1>${esc(batch?.name || 'Review')}</h1><p>${esc(batch?.task || '')} · ${items.length.toLocaleString()} episode(s) · ${esc(state.project?.path || '')}</p></div><button id="tc-change-project" class="tc-secondary">Project folder</button></div><section class="tc-inbox"><div id="tc-filter-bar" class="tc-filter-bar"></div><div id="tc-selection-bar" class="tc-selection-bar"></div><div class="tc-table-wrap"><table class="tc-table"><thead><tr><th class="tc-check-cell"><input id="tc-select-filtered" type="checkbox" title="Select all filtered results"></th><th>Episode</th><th>Task / source</th><th>Frames</th><th>Auto label</th><th>Review</th><th></th></tr></thead><tbody id="tc-review-rows"></tbody></table></div><div id="tc-pagination" class="tc-pagination"></div></section>`;
    document.getElementById('tc-change-project').onclick = () => openProject().catch((error) => alert(error.message));
    document.getElementById('tc-back-batches').onclick = () => { state.group = 'all'; state.selected.clear(); state.page = 1; saveState(); renderReview(); };

    const filterBar = document.getElementById('tc-filter-bar');
    const search = document.createElement('input'); search.type = 'search'; search.placeholder = 'Search episode, task, tag or ID'; search.value = state.search;
    search.oninput = () => {
      state.search = search.value; state.page = 1; saveState();
      clearTimeout(searchTimer); searchTimer = setTimeout(renderReview, 120);
    };
    const controls = [
      select([['all', 'All sources'], ['teleop', 'Teleop'], ['scripted', 'Scripted']], state.source, 'Source'),
      select([['all', 'Any review'], ['unreviewed', 'Needs review'], ['accepted', 'Approved'], ['rejected', 'Rejected']], state.status, 'Review status'),
      select([['all', 'Any auto label'], ['accept', 'Auto accept'], ['review', 'Auto review'], ['reject', 'Auto reject']], state.autoLabel, 'Auto label'),
      select([['all', 'All tasks'], ...tasks.map((task) => [task, task])], state.task, 'Task'),
    ];
    ['source', 'status', 'autoLabel', 'task'].forEach((key, index) => controls[index].onchange = () => { state[key] = controls[index].value; state.page = 1; saveState(); renderReview(); });
    filterBar.append(search, ...controls);

    const headerCheck = document.getElementById('tc-select-filtered'); setCheckboxState(headerCheck, items);
    headerCheck.onchange = () => setGroupSelected(items, headerCheck.checked);
    const selection = document.getElementById('tc-selection-bar');
    if (state.selected.size) {
      selection.classList.add('visible');
      const count = document.createElement('strong'); count.textContent = `${state.selected.size.toLocaleString()} selected`;
      const approve = button('Approve'); approve.onclick = () => bulkReview('approved').catch((error) => alert(error.message));
      const reject = button('Reject'); reject.onclick = () => bulkReview('rejected').catch((error) => alert(error.message));
      const exportButton = button('Export…', 'tc-primary'); exportButton.onclick = openExport;
      const clear = button('Clear selection'); clear.onclick = () => {
        state.selected.clear(); state.page = 1;
        saveState(); renderReview();
      };
      selection.append(count, approve, reject, exportButton, clear);
    } else {
      selection.innerHTML = `<span><strong>${items.length.toLocaleString()}</strong> matching episode(s)</span><span class="tc-muted">Select a batch, filtered results, or individual rows.</span>`;
    }

    const rows = document.getElementById('tc-review-rows');
    visible.forEach((item) => {
      const row = document.createElement('tr'); if (state.selected.has(item.id)) row.classList.add('selected');
      row.innerHTML = `<td class="tc-check-cell"><input type="checkbox" ${state.selected.has(item.id) ? 'checked' : ''}></td><td><strong>${esc(item.title)}</strong><small>${esc(item.id)}</small></td><td>${esc(item.task)}<small>${badge(item.source, item.source)}</small></td><td>${Number(item.frames || 0).toLocaleString()}</td><td title="${esc(item.auto_label_reason || '')}">${badge(item.auto_label || 'review', `auto-${item.auto_label || 'review'}`)}</td><td>${badge(item.status, item.status)}</td><td><button class="tc-open">Open</button></td>`;
      const check = row.querySelector('input'); check.onchange = () => { check.checked ? state.selected.add(item.id) : state.selected.delete(item.id); saveState(); renderReview(); };
      row.querySelector('.tc-open').onclick = () => {
        saveState();
        const params = new URLSearchParams({returnTo: '/review'});
        if (item.source === 'scripted') params.set('source', 'scripted');
        // Keep the authenticated Next.js tree mounted. A full page navigation
        // restores the local user asynchronously and the shared manual-review
        // page cannot safely transition from its signed-out render in place.
        // Native history navigation is integrated with the Next App Router.
        history.pushState(null, '', `/review/${encodeURIComponent(item.id)}?${params}`);
      };
      rows.appendChild(row);
    });
    if (!visible.length) rows.innerHTML = '<tr><td colspan="7" class="tc-empty">No episodes match the current batch and filters.</td></tr>';
    const pagination = document.getElementById('tc-pagination');
    pagination.innerHTML = `<span>${items.length ? start + 1 : 0}–${Math.min(start + PAGE_SIZE, items.length)} of ${items.length.toLocaleString()}</span>`;
    const previous = button('Previous'); previous.disabled = state.page <= 1; previous.onclick = () => { state.page--; saveState(); renderReview(); };
    const next = button('Next'); next.disabled = state.page >= pages; next.onclick = () => { state.page++; saveState(); renderReview(); };
    pagination.append(previous, next);
  };

  const renderReviewBatchPicker = () => {
    const root = document.getElementById('tc-local-review'); if (!root) return;
    root.innerHTML = `<div class="tc-page-head"><div><h1>Review</h1><p>Choose a batch to review its episodes.</p></div><div class="tc-head-actions"><button id="tc-new-batch" class="tc-primary">New batch</button><button id="tc-change-project" class="tc-secondary">Project folder</button></div></div><div id="tc-review-batch-grid" class="tc-review-batch-grid"></div>`;
    document.getElementById('tc-change-project').onclick = () => openProject().catch((error) => alert(error.message));
    document.getElementById('tc-new-batch').onclick = openNewBatchDialog;
    const grid = document.getElementById('tc-review-batch-grid');
    state.batches.forEach((batch) => {
      const card = document.createElement('button'); card.type = 'button'; card.className = 'tc-review-batch-card';
      card.innerHTML = `<div class="tc-review-batch-head"><strong>${esc(batch.name)}</strong>${batch.active ? badge('active', 'open') : ''}</div><p>${esc(batch.task)}</p><div class="tc-review-batch-stats"><span><b>${batch.episodes}</b> episodes</span><span><b>${batch.teleop}</b> teleop</span><span><b>${batch.scripted}</b> scripted</span></div><div class="tc-review-batch-progress"><span class="ok">${batch.approved} approved</span><span class="warn">${batch.pending} pending</span><span class="bad">${batch.rejected} rejected</span></div><span class="tc-review-batch-open">Review episodes →</span>`;
      card.onclick = () => { state.group = `batch:${batch.id}`; state.selected.clear(); state.page = 1; state.search = ''; state.source = 'all'; state.status = 'all'; state.autoLabel = 'all'; state.task = 'all'; saveState(); renderReview(); };
      grid.appendChild(card);
    });
    if (!state.batches.length) grid.innerHTML = '<div class="tc-empty-batches"><h2>No batches yet</h2><p>Create the first batch to start collecting data.</p></div>';
  };

  const openNewBatchDialog = () => {
      const overlay = document.createElement('div'); overlay.className = 'tc-overlay';
      const card = document.createElement('section'); card.className = 'tc-dialog'; card.innerHTML = '<div class="tc-dialog-head"><div><h2>New batch</h2><p>One batch belongs to one robot task and may contain both Teleop and Scripted episodes.</p></div><button class="tc-icon-button">×</button></div>';
      const form = document.createElement('div'); form.className = 'tc-export-form';
      const name = document.createElement('input'); name.placeholder = 'e.g. lift-cube-aug25';
      const task = select([['lift_cube', 'lift_cube'], ['pick_place_can', 'pick_place_can'], ['nut_assembly_square', 'nut_assembly_square'], ['tool_hang', 'tool_hang']], 'lift_cube', 'Task');
      const description = document.createElement('input'); description.placeholder = 'Optional description';
      const field = (label, control) => { const wrap = document.createElement('label'); wrap.append(label, control); form.appendChild(wrap); };
      field('Batch name', name); field('Task', task); field('Description', description); card.appendChild(form);
      const result = document.createElement('div'); result.className = 'tc-export-result';
      const actions = document.createElement('div'); actions.className = 'tc-dialog-actions'; const cancel = button('Cancel'); cancel.onclick = () => overlay.remove(); const create = button('Create batch', 'tc-primary');
      create.onclick = async () => { try { const batch = await api('/api/v1/local/batches', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name.value.trim(), task: task.value, description: description.value.trim()})}); await api('/api/v1/local/batches/active', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: batch.id})}); overlay.remove(); await loadReview(true); } catch (error) { result.textContent = error.message || error; } };
      actions.append(cancel, create); card.append(result, actions); card.querySelector('.tc-icon-button').onclick = () => overlay.remove(); overlay.appendChild(card); document.body.appendChild(overlay); name.focus();
  };

  const renderCurrent = () => renderReview();

  const loadReview = async (reload = false) => {
    if (state.loading) return; state.loading = true;
    try {
      if (reload || !state.episodes.length) {
        state.project = await api('/api/v1/local/project');
        state.episodes = await api('/api/v1/local/library/episodes');
        state.batches = await api('/api/v1/local/batches');
        state.loaded = true;
        const valid = new Set(state.episodes.map((item) => item.id)); state.selected = new Set([...state.selected].filter((id) => valid.has(id)));
      }
      renderCurrent();
      applyCollectBatchPanel();
    } catch (error) {
      const root = document.getElementById('tc-local-review'); if (root) root.innerHTML = `<div class="tc-load-error"><h2>Could not load this project</h2><p>${esc(error.message || error)}</p></div>`;
    } finally { state.loading = false; }
  };

  const showReview = () => {
    document.querySelectorAll('main:not(#tc-local-review)').forEach((main) => { main.dataset.tcHidden = '1'; main.style.display = 'none'; });
    let root = document.getElementById('tc-local-review');
    let created = false;
    if (!root) { root = document.createElement('main'); root.id = 'tc-local-review'; document.body.appendChild(root); created = true; }
    root.style.display = '';
    if (state.loaded && (created || !root.firstElementChild)) renderCurrent();
    else if (!state.loaded && !state.loading) loadReview();
  };

  const hideReview = () => {
    const root = document.getElementById('tc-local-review'); if (root) root.style.display = 'none';
    document.querySelectorAll('main[data-tc-hidden="1"]').forEach((main) => { main.style.display = ''; delete main.dataset.tcHidden; });
  };

  const applyCollectBatchPanel = () => {
    // Returning early only skipped building the bar; one already prepended to
    // `main` stayed there for the rest of the session, so every other page
    // grew a batch picker it has no use for. Remove it on the way out.
    if (location.pathname !== '/collect') { document.getElementById('tc-collect-batch')?.remove(); return; }
    if (!state.loaded && !state.loading) { loadReview(); return; }
    const main = document.querySelector('main:not(#tc-local-review)'); if (!main) return;
    const active = state.batches.find((batch) => batch.active);
    const available = state.batches;
    let panel = document.getElementById('tc-collect-batch');
    if (!panel) { panel = document.createElement('section'); panel.id = 'tc-collect-batch'; main.prepend(panel); }
    const signature = `${active?.id || ''}|${available.map((batch) => `${batch.id}:${batch.name}`).join(',')}`;
    if (panel.dataset.signature !== signature) {
      panel.dataset.signature = signature; panel.className = `tc-collect-batch${active ? '' : ' warning'}`; panel.replaceChildren();
      const copy = document.createElement('div'); copy.innerHTML = active
        ? `<strong>Collection batch: ${esc(active.name)}</strong><span>${esc(active.task)} · new Teleop and Scripted data will be assigned here</span>`
        : '<strong>No active batch</strong><span>Create or choose a batch before collecting data.</span>';
      const picker = select([['', 'Choose a batch…'], ...available.map((batch) => [batch.id, `${batch.name} · ${batch.task}`])], active?.id || '', 'Active collection batch');
      // The collection screens are React and read the active batch once on
      // mount, so switching batches here has to tell them. Without this the
      // task stayed pinned to the batch that was active when the page loaded.
      picker.onchange = async () => {
        if (!picker.value) return;
        await api('/api/v1/local/batches/active', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({batch_id: picker.value})});
        // Flip the flag here rather than waiting for the reload: `loadReview`
        // bails out while another load is in flight, which left the heading
        // naming the old batch while the picker already showed the new one.
        state.batches.forEach((batch) => { batch.active = batch.id === picker.value; });
        applyCollectBatchPanel();
        await loadReview(true);
        window.dispatchEvent(new CustomEvent('telecollect:active-batch-changed'));
      };
      // `/review` is not a route — it rendered a broken page. The batch list
      // lives at `/raw`, which the sidebar calls Review.
      const manage = button('Review batches'); manage.onclick = () => location.assign('/raw');
      panel.append(copy, picker, manage);
    }
    main.querySelectorAll('button').forEach((item) => {
      if (panel.contains(item)) return;
      if (!active) { if (!item.disabled) item.dataset.tcBatchDisabled = '1'; item.disabled = true; }
      else if (item.dataset.tcBatchDisabled === '1') { item.disabled = false; delete item.dataset.tcBatchDisabled; }
    });
    const batchField = [...main.querySelectorAll('label')].find((label) => label.textContent?.includes('Collection batch'));
    if (batchField) {
      batchField.style.display = 'none';
      batchField.parentElement.style.gridTemplateColumns = 'minmax(0,1fr) 140px 140px 140px auto';
    }
  };

  const applyShell = () => {
    const path = location.pathname;
    const pathChanged = path !== lastShellPath;
    lastShellPath = path;
    // Local mode supplies its own token, so the login page is never useful.
    // `/` is now a real Overview page, however, and must not be redirected to
    // Collect.  Keeping the old redirect made the sidebar/brand appear to jump
    // between pages every time Overview was selected.
    if (path === '/login') { location.replace('/'); return; }
    // The shared web moved navigation from a top bar into a left sidebar, so
    // the rail is `aside nav`, not `header nav`. Every link stays visible: the
    // app runs that same web, so Training, Evaluate and the rest work here too.
    const nav = document.querySelector('aside nav');
    if (nav && !document.getElementById('tc-project-button')) {
      const project = button('Project folder', 'rounded-lg px-3.5 py-2 text-left text-[13px] font-medium text-ink-300'); project.id = 'tc-project-button'; project.onclick = () => openProject().catch((error) => alert(error.message)); nav.appendChild(project);
    }
    document.getElementById('tc-batches-button')?.remove();
    if (path === '/review') {
      showReview();
      if (pathChanged && state.loaded && !state.loading) void loadReview(true);
    } else hideReview();
    applyCollectBatchPanel();
  };

  const style = document.createElement('style');
  style.textContent = `
    #tc-local-review{min-height:calc(100vh - 72px);padding:34px 38px 48px;background:#f3f6fb;color:#111b30;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
    .tc-head-actions{display:flex;gap:10px;align-items:center}
    .tc-review-batch-grid{grid-template-columns:repeat(4,minmax(0,1fr))!important}
    .tc-back-button{display:block;margin:0 0 16px;padding:0;border:0;background:transparent;color:#2868ed;font:600 13px Inter,ui-sans-serif,system-ui,sans-serif;cursor:pointer}.tc-back-button:hover{text-decoration:underline}.tc-review-batch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:16px}.tc-review-batch-card{min-height:210px;padding:20px;border:1px solid #dce4ef;border-radius:14px;background:#fff;box-shadow:0 5px 16px #2031500d;color:#172033;text-align:left;cursor:pointer;transition:border-color .15s,box-shadow .15s,transform .15s}.tc-review-batch-card:hover{border-color:#8fb2f5;box-shadow:0 10px 24px #2031501a;transform:translateY(-1px)}.tc-review-batch-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.tc-review-batch-head strong{font-size:17px;overflow-wrap:anywhere}.tc-review-batch-card>p{margin:8px 0 20px;color:#64748b;font:12px ui-monospace,SFMono-Regular,monospace}.tc-review-batch-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;padding:13px 0;border-top:1px solid #edf1f6;border-bottom:1px solid #edf1f6}.tc-review-batch-stats span{display:grid;gap:3px;color:#718096;font-size:11px}.tc-review-batch-stats b{color:#172033;font-size:17px}.tc-review-batch-progress{display:flex;flex-wrap:wrap;gap:12px;margin-top:13px;font-size:11px;font-weight:650}.tc-review-batch-progress .ok{color:#087a5d}.tc-review-batch-progress .warn{color:#b65c12}.tc-review-batch-progress .bad{color:#c73535}.tc-review-batch-open{display:block;margin-top:18px;color:#2868ed;font-size:12px;font-weight:750}.tc-empty-batches{grid-column:1/-1;padding:60px 20px;border:1px dashed #cbd5e1;border-radius:14px;background:#fff;text-align:center;color:#64748b}.tc-empty-batches h2{margin:0 0 7px;color:#172033;font-size:19px}.tc-empty-batches p{margin:0}
    #tc-local-review *{box-sizing:border-box}.tc-page-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:20px}.tc-page-head h1{font-size:27px;line-height:1;margin:0 0 9px}.tc-page-head p{margin:0;color:#64748b;font:12px ui-monospace,SFMono-Regular,monospace}.tc-review-shell{display:grid;grid-template-columns:245px minmax(0,1fr);gap:18px;align-items:start}.tc-review-tree,.tc-inbox{background:#fff;border:1px solid #dce4ef;border-radius:14px;box-shadow:0 5px 16px #2031500d}.tc-review-tree{padding:14px 10px;max-height:calc(100vh - 180px);overflow:auto;position:sticky;top:15px}.tc-tree-heading{padding:17px 10px 6px;color:#718096;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.06em}.tc-tree-row{display:grid;grid-template-columns:25px 1fr;align-items:center;border-radius:8px}.tc-tree-row.active{background:#edf3ff;color:#1859d1}.tc-tree-row>input{margin-left:8px}.tc-tree-row button{display:grid;grid-template-columns:18px 1fr auto;gap:5px;align-items:center;width:100%;padding:9px 9px 9px 2px;border:0;background:transparent;color:inherit;text-align:left;cursor:pointer}.tc-folder{color:#7992ba}.tc-count{color:#74829a;font-size:11px}.tc-inbox{overflow:hidden}.tc-filter-bar{display:grid;grid-template-columns:minmax(230px,1.6fr) repeat(4,minmax(120px,.65fr));gap:8px;padding:15px;border-bottom:1px solid #e2e8f0}.tc-filter-bar input,.tc-filter-bar select,.tc-export-form input,.tc-export-form select{width:100%;height:40px;padding:0 11px;border:1px solid #cfd9e7;border-radius:8px;background:#fff;color:#172033}.tc-selection-bar{min-height:55px;padding:9px 15px;display:flex;align-items:center;gap:9px;border-bottom:1px solid #e2e8f0;color:#475569}.tc-selection-bar.visible{background:#f5f8ff}.tc-selection-bar .tc-muted{margin-left:auto}.tc-primary,.tc-secondary,.tc-open,.tc-icon-button{border:1px solid #cad5e5;border-radius:8px;padding:9px 14px;background:#fff;color:#18233a;font-weight:650;cursor:pointer}.tc-primary{border-color:#2868ed;background:#2868ed;color:#fff}.tc-open{padding:7px 13px}.tc-icon-button{border:0;font-size:25px;padding:3px 9px}.tc-primary:disabled,.tc-secondary:disabled{opacity:.45;cursor:not-allowed}.tc-table-wrap{overflow:auto}.tc-table{width:100%;border-collapse:collapse;font-size:13px}.tc-table th{padding:12px 10px;text-align:left;color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:.04em;background:#fbfcfe;border-bottom:1px solid #e2e8f0}.tc-table td{padding:12px 10px;border-bottom:1px solid #e8edf4;vertical-align:middle}.tc-table tr.selected{background:#f4f7ff}.tc-table small{display:block;margin-top:4px;color:#7c899e;max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tc-check-cell{width:42px;text-align:center!important}.tc-row-actions{display:flex;justify-content:flex-end;gap:5px;white-space:nowrap}.tc-row-actions button{padding:6px 8px;font-size:11px}.tc-badge{display:inline-block;padding:4px 8px;border-radius:6px;background:#eef2f7;color:#4b5a70;font-size:11px;font-weight:700}.tc-teleop{background:#e8efff;color:#2463dd}.tc-scripted{background:#eee9ff;color:#6742dc}.tc-open{background:#e7f8f1;color:#087a5d}.tc-closed{background:#eef2f7;color:#64748b}.tc-auto-accept,.tc-accepted{background:#e7f8f1;color:#087a5d}.tc-auto-reject,.tc-rejected{background:#ffeded;color:#c73535}.tc-auto-review,.tc-unreviewed{background:#fff3e5;color:#b65c12}.tc-pagination{display:flex;align-items:center;justify-content:flex-end;gap:9px;padding:13px 15px;color:#64748b}.tc-pagination span{margin-right:auto}.tc-empty{height:180px;text-align:center;color:#718096}.tc-muted{color:#718096;font-size:12px}.tc-batch-help{margin-bottom:14px;padding:14px 16px;border:1px solid #cddcf4;border-radius:10px;background:#f4f8ff;color:#44546d;font-size:13px}.tc-overlay{position:fixed;inset:0;z-index:10000;display:grid;place-items:center;padding:24px;background:#0b1425a8;font-family:Inter,ui-sans-serif,system-ui;color:#172033}.tc-dialog{width:min(680px,100%);max-height:88vh;overflow:auto;padding:24px;background:#fff;border-radius:16px;box-shadow:0 26px 90px #0006}.tc-project-dialog{width:min(620px,100%)}.tc-dialog-head{display:flex;align-items:flex-start;justify-content:space-between}.tc-dialog h2{margin:0 0 6px}.tc-dialog p{margin:0;color:#64748b}.tc-path{margin-top:16px;padding:12px;border:1px solid #d9e2ee;border-radius:8px;background:#f7f9fc;font:12px ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.tc-dialog-actions{display:flex;align-items:center;justify-content:flex-end;gap:9px;margin-top:18px}.tc-export-form{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:20px}.tc-export-form label{display:grid;gap:6px;font-size:12px;font-weight:700}.tc-export-info{grid-column:1/-1;padding:12px;border-radius:8px;background:#f4f7fc;color:#52627a;font-size:13px}.tc-export-result{margin-top:15px;color:#334155}.tc-load-error{padding:30px;background:#fff;border:1px solid #fecaca;border-radius:12px}.tc-collect-batch{display:grid;grid-template-columns:minmax(260px,1fr) minmax(260px,420px) auto;gap:12px;align-items:center;margin-bottom:18px;padding:14px 16px;border:1px solid #bcd2f5;border-radius:12px;background:#f4f8ff;color:#172033}.tc-collect-batch.warning{border-color:#fdba74;background:#fff7ed}.tc-collect-batch div{display:grid;gap:3px}.tc-collect-batch span{color:#64748b;font-size:12px}.tc-collect-batch select{height:40px;padding:0 10px;border:1px solid #cbd5e1;border-radius:8px;background:#fff}
    @media(max-width:1050px){.tc-review-shell{grid-template-columns:210px minmax(0,1fr)}.tc-filter-bar{grid-template-columns:1fr 1fr}.tc-filter-bar input{grid-column:1/-1}.tc-table th:nth-child(4),.tc-table td:nth-child(4){display:none}}
  `;
  document.head.appendChild(style);
  const start = () => {
    restoreState(); applyShell();
    new MutationObserver(applyShell).observe(document.documentElement, {childList: true, subtree: true});
    window.addEventListener('popstate', applyShell);
  };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once: true});
})();
