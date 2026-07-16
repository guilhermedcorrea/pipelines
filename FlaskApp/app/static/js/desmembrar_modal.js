// app/static/js/desmembrar_modal.js
(function () {
  const DEBUG = true;
  const log = (...a) => DEBUG && console.log('[desmembrar]', ...a);
  window.__DESMEMBRAR_EXTERNAL__ = true;

  const $  = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));
  const money = n => Number(n || 0).toFixed(2);
  const moneyBR = n => money(n).replace('.', ','); // helper para exibir BRL

  function parseData() {
    try {
      const el = document.getElementById('__desm_data__');
      const parsed = JSON.parse(el?.textContent || '{}');
      return parsed || {};
    } catch (e) {
      console.error('[desmembrar] erro parse __desm_data__:', e);
      return {};
    }
  }

  const pickIdItem     = (it) => (it?.IDItem ?? it?.pitem?.IDItem);
  const pickPedidoIten = (it) => (it?.IDPedidoIten ?? it?.pitem?.IDPedidoIten);
  const pickKey        = (it) => {
    const idpi = pickPedidoIten(it);
    if (idpi != null) return `PI:${idpi}`;
    const idItem = pickIdItem(it);
    return (idItem != null) ? `ITEM:${idItem}` : '';
  };
  const pickNome    = (it, id) => (it?.NomeProduto ?? it?.produto?.NomeProduto ?? ('ID ' + id));
  const pickQtd     = (it) => Number(it?.Quantidade ?? it?.pitem?.Quantidade ?? 0);
  const pickVlrUnit = (it) => Number(it?.ValorUnitario ?? it?.pitem?.ValorUnitario ?? 0);
  const pickSerie   = (it) => (it?.NumeroSerie ?? it?.NumerodeSerie ?? it?.pitem?.NumeroSerie ?? it?.pitem?.NumerodeSerie ?? '');

  function renderTabs(modalEl, nWanted) {
    if (!modalEl) return;

    const DESM_DATA = parseData();

    const detailed   = Array.isArray(DESM_DATA?.itens) ? DESM_DATA.itens : [];
    const aggregated = Array.isArray(DESM_DATA?.parent_items_simple) ? DESM_DATA.parent_items_simple : [];
    const detailedIds = new Set(
      detailed.map(it => pickIdItem(it)).filter(v => v !== undefined && v !== null)
    );
    const aggregatedFiltered = aggregated.filter(it => !detailedIds.has(pickIdItem(it)));
    const SOURCE_ITEMS = [...detailed, ...aggregatedFiltered];

    log('DESM_DATA:', DESM_DATA);
    log('Fonte dos itens:', SOURCE_ITEMS);
    log('Qtde itens na fonte:', SOURCE_ITEMS.length);

    const input = $('#qtdPedidosInput', modalEl);
    const root  = $('#wizardTabsRoot', modalEl);
    if (!input || !root) return;

    const min = parseInt(input.min || '1', 10);
    const max = parseInt(input.max || '999999', 10);

    let n = parseInt(nWanted, 10);
    if (!Number.isFinite(n)) n = min;
    if (n < min) n = min;
    if (Number.isFinite(max) && n > max) n = max;

    if (root.dataset.lastN && parseInt(root.dataset.lastN, 10) === n) {
      return;
    }
    root.dataset.lastN = String(n);
    root.innerHTML = '';

    const initialRemaining = {};
    (SOURCE_ITEMS || []).forEach(it => {
      const key = pickKey(it);
      if (!key) return;
      const q   = pickQtd(it);
      initialRemaining[key] = (initialRemaining[key] || 0) + (Number.isFinite(q) ? q : 0);
    });
    log('initialRemaining:', initialRemaining);

    const allocated = {};
    Object.keys(initialRemaining).forEach(k => allocated[k] = 0);

    const bar = document.createElement('div');
    bar.className = 'win-tabs';
    bar.setAttribute('role', 'tablist');

    const content = document.createElement('div');
    content.className = 'win-tab-content';

    function activate(originValue) {
      $$('.win-tabs .win-tab', content.parentElement).forEach(b => b.classList.remove('active'));
      $$('.tab-pane-like[data-target]', content).forEach(p => p.style.display = 'none');

      $$(`.win-tabs .win-tab[data-origin="${originValue}"]`, content.parentElement)
        .forEach(b => b.classList.add('active'));
      $$(`.tab-pane-like[data-target="${originValue}"]`, content)
        .forEach(p => p.style.display = 'block');
    }

    const lastId = Number(modalEl.getAttribute('data-last-id') || DESM_DATA.last_id || 0);

    function getRemaining(key){
      const base = initialRemaining[key] || 0;
      const used = allocated[key] || 0;
      return Math.max(0, base - used);
    }
    function getItemByKey(key) {
      return (SOURCE_ITEMS || []).find(x => pickKey(x) === key);
    }

    function buildOptionsHtml(allowKeys = new Set()) {
      const seen = new Set();
      let html = '';
      (SOURCE_ITEMS || []).forEach(it => {
        const key = pickKey(it);
        if (!key || seen.has(key)) return;
        seen.add(key);

        const rem = getRemaining(key);
        if (rem <= 0 && !allowKeys.has(key)) return;

        const idItem = pickIdItem(it);
        const nome   = pickNome(it, idItem);
        const serie  = pickSerie(it);
        const label  = serie
          ? `${idItem} — ${nome} — Série: ${serie}`
          : `${idItem} — ${nome}`;

        html += `<option value="${key}" data-iditem="${idItem}">${label}</option>`;
      });
      log('options geradas:', seen.size);
      return html;
    }

    function lineTemplate(optionsHtml) {
      return `
        <div class="row g-3 align-items-start js-line mb-3">
          <div class="col-12 col-lg-6">
            <select class="form-select js-sel">
              <option value="">-- selecione um item --</option>
              ${optionsHtml}
            </select>
          </div>
          <div class="col-6 col-lg-2">
            <div class="d-flex flex-column">
              <input type="number" class="form-control js-qty" min="0" placeholder="Qtd">
              <div class="form-text js-max-help mt-1"></div>
            </div>
          </div>
          <div class="col-6 col-lg-2">
            <input type="text" class="form-control js-unit" placeholder="Vlr Unit." readonly>
          </div>
          <div class="col-6 col-lg-2">
            <div class="d-flex gap-2">
              <input type="text" class="form-control js-total" placeholder="Total" readonly>
              <button type="button" class="btn btn-outline-danger js-remove" title="Remover linha">×</button>
            </div>
          </div>
        </div>
      `;
    }

    function updateAllMax() {
      // Mantido: função genérica; ignora campos inexistentes sem quebrar
      $$('.tab-pane-like', content).forEach(panel => {
        const sel   = $('.js-item-select', panel);
        const qty   = $('.js-qty', panel);
        const unit  = $('.js-unit', panel);
        const total = $('.js-total', panel);
        const idit  = $('.js-iditem', panel);
        const nome  = $('.js-nome', panel);
        const help  = $('.js-max-help', panel);

        if (!sel || !qty) return;
        const key = sel.value || '';
        if (!key) {
          qty.value = '';
          qty.max = 0;
          if (unit)  unit.value = '';
          if (total) total.value = '';
          if (idit)  idit.value = '';
          if (nome)  nome.value = '';
          if (help)  help.textContent = '';
          return;
        }

        const it = getItemByKey(key);
        const rem = getRemaining(key);
        qty.max = rem;
        if (help) help.textContent = 'Disponível: ' + rem;

        if (Number(qty.value) > rem) {
          const old = Number(qty.value) || 0;
          allocated[key] = Math.max(0, (allocated[key] || 0) - old + rem);
          qty.value = rem;
        }

        const nomeProd = pickNome(it, pickIdItem(it));
        const vu       = pickVlrUnit(it);

        if (idit)  idit.value  = pickIdItem(it) || '';
        if (nome)  nome.value  = nomeProd || '';
        if (unit)  unit.value  = money(vu);
        if (total) total.value = money((Number(qty.value||0)) * vu);
      });
    }

    function refreshPanelLines(panel) {
      const linesBox = $('[data-lines]', panel);
      if (!linesBox) return;

      const allowKeys = new Set(
        $$('.js-line .js-sel', linesBox).map(s => s.value).filter(Boolean)
      );

      const optionHtml = buildOptionsHtml(allowKeys);

      $$('.js-line', linesBox).forEach(line => {
        const sel = $('.js-sel', line);
        const cur = sel.value || '';

        sel.innerHTML = `<option value="">-- selecione um item --</option>${optionHtml}`;

        if (cur && !Array.from(sel.options).some(o => (o.value || '') === cur)) {
          const it  = getItemByKey(cur);
          if (it) {
            const idItem = pickIdItem(it);
            const nome   = pickNome(it, idItem);
            const serie  = pickSerie(it);
            const lbl = serie ? `${idItem} — ${nome} — Série: ${serie}` : `${idItem} — ${nome}`;
            const opt = document.createElement('option');
            opt.value = cur;
            opt.textContent = lbl;
            sel.appendChild(opt);
          }
        }

        if (cur) sel.value = cur;
        updateLine(line);
      });
    }

    function refreshAllPanelsUI() {
      $$('.tab-pane-like', content).forEach(panel => refreshPanelLines(panel));
    }

    function onSelectChange(panel){
      const sel = $('.js-item-select', panel);
      const qty = $('.js-qty', panel);

      const oldKey  = panel.dataset.currKey || '';
      const oldQty  = Number(panel.dataset.currQty || 0);
      if (oldKey) allocated[oldKey] = Math.max(0, (allocated[oldKey]||0) - oldQty);

      panel.dataset.currKey = sel ? (sel.value || '') : '';
      panel.dataset.currQty = '0';

      updateAllMax();
      refreshAllPanelsUI();
    }

    function onQtyInput(panel){
      const sel   = $('.js-item-select', panel);
      const qty   = $('.js-qty', panel);
      const unit  = $('.js-unit', panel);
      const total = $('.js-total', panel);

      const key = sel?.value || '';
      const max = Number(qty?.max || 0);
      let qn = Math.max(0, Math.min(Number(qty?.value||0), max));

      const oldKey = panel.dataset.currKey || '';
      const oldQty = Number(panel.dataset.currQty || 0);
      if (oldKey) allocated[oldKey] = Math.max(0, (allocated[oldKey]||0) - oldQty);
      if (key)    allocated[key]    = (allocated[key]||0) + qn;

      panel.dataset.currKey = String(key || '');
      panel.dataset.currQty = String(qn || 0);

      const vu = Number(unit?.value || 0);
      if (total) total.value = money(qn * vu);

      updateAllMax();
      refreshAllPanelsUI();
    }

    // ===== NOVO: função para calcular o total de UMA aba (somando os campos .js-total) =====
    function recalcTabTotal(panel) {
      const lines = panel.querySelectorAll('.js-line');
      let sum = 0;
      lines.forEach(line => {
        const totStr = (line.querySelector('.js-total')?.value || '').trim();
        if (!totStr) return;
        // Se vier em formato BR ("24.000,00"), normaliza; caso contrário, parseFloat direto.
        let val;
        if (/,/.test(totStr) && /\d,\d{2}$/.test(totStr)) {
          val = parseFloat(totStr.replace(/\./g, '').replace(',', '.'));
        } else {
          val = parseFloat(totStr);
        }
        if (Number.isFinite(val)) sum += val;
      });
      const out = panel.querySelector('.js-tab-total');
      if (out) out.textContent = moneyBR(sum);
    }

    for (let i = 1; i <= n; i++) {
      const origin = `pedido-${i}`;

      const tab = document.createElement('button');
      tab.type = 'button';
      tab.className = 'win-tab' + (i === 1 ? ' active' : '');
      tab.dataset.origin = origin;
      tab.textContent = `Pedido ${i}`;
      tab.addEventListener('click', () => activate(origin));
      bar.appendChild(tab);

      const panel = document.createElement('div');
      panel.className = 'tab-pane-like';
      panel.dataset.target = origin;
      panel.style.display = (i === 1 ? 'block' : 'none');

      const previewId = (lastId || 0) + i;

      // ======= Mantido + AJUSTE: adiciona DataAgendado + bloco de total =======
      panel.innerHTML = `
        <div class="row g-3 align-items-start">
          <div class="col-12 col-md-3">
            <label class="form-label">Pedido (preview)</label>
            <input type="text" class="form-control js-preview-id" value="${previewId}" readonly>
            <div class="small text-muted">Somente visualização.</div>

            <!-- NOVO CAMPO: DataAgendado -->
            <label class="form-label mt-3">Data agendada</label>
            <input type="date" class="form-control js-date" placeholder="AAAA-MM-DD">
            <div class="small text-muted">Opcional: data prevista para este pedido.</div>
          </div>

          <div class="col-12 col-md-9">
            <label class="form-label">Observação do pedido</label>
            <textarea class="form-control js-obs" rows="2"
              placeholder="Observação do pedido"></textarea>
          </div>
        </div>

        <div class="row g-3 align-items-start mt-2">
          <div class="col-12">
            <label class="form-label d-flex align-items-center justify-content-between">
              <span>Itens do Pedido Pai</span>
              <button type="button" class="btn btn-sm btn-outline-primary js-add-line">+ Selecionar mais</button>
            </label>

            <div class="border rounded p-2" data-lines></div>

            <div class="small text-muted mt-1">
              Apenas itens do pedido pai; cada item pode ser alocado em apenas um pedido filho.
            </div>

            <!-- NOVO: total desta aba (dinâmico) -->
            <div class="mt-2 text-end">
              <span class="fw-bold">Total desta aba:</span>
              <span class="fw-bold"> R$ <span class="js-tab-total">0,00</span></span>
            </div>
          </div>
        </div>

        <div class="mt-2">
        
        </div>
      `;

      const linesBox = $('[data-lines]', panel);
      const addBtn   = $('.js-add-line', panel);

      function updateLine(line) {
        const sel   = $('.js-sel', line);
        const qty   = $('.js-qty', line);
        const unit  = $('.js-unit', line);
        const total = $('.js-total', line);
        const help  = $('.js-max-help', line);

        const key = sel.value || '';
        if (!key) {
          qty.value   = '';
          qty.max     = 0;
          unit.value  = '';
          total.value = '';
          help && (help.textContent = '');
          recalcTabTotal(panel); // recalcula total ao limpar linha
          return;
        }

        const it   = getItemByKey(key);
        const currentLineQty = Number(qty.value || 0);
        const usedGlobal     = (allocated[key] || 0) - currentLineQty;
        const rem            = Math.max(0, (initialRemaining[key] || 0) - usedGlobal);

        qty.max = rem;
        help && (help.textContent = 'Disponível: ' + rem);

        const vu = pickVlrUnit(it);
        unit.value = money(vu);

        let qn = Math.max(0, Math.min(Number(qty.value || 0), rem));
        if (qn !== Number(qty.value || 0)) qty.value = qn;

        total.value = money(qn * vu);

        recalcTabTotal(panel); // recalcula sempre que a linha é atualizada
      }

      function applyAllocationChange(line) {
        const sel = $('.js-sel', line);
        const qty = $('.js-qty', line);
        const key = sel.value || '';

        const oldKey = line.dataset.lastKey || '';
        const oldQty = Number(line.dataset.lastQty || 0);
        if (oldKey) allocated[oldKey] = Math.max(0, (allocated[oldKey] || 0) - oldQty);

        const newQty = Number(qty.value || 0);
        if (key) allocated[key] = (allocated[key] || 0) + newQty;

        line.dataset.lastKey = String(key || '');
        line.dataset.lastQty = String(newQty || 0);
      }

      function bindLine(line) {
        const sel = $('.js-sel', line);
        const qty = $('.js-qty', line);
        const btn = $('.js-remove', line);

        sel.addEventListener('change', () => {
          qty.value = '';
          applyAllocationChange(line);
          updateLine(line);
          applyAllocationChange(line);
          refreshAllPanelsUI();
          recalcTabTotal(panel);
        });

        qty.addEventListener('input', () => {
          updateLine(line);
          applyAllocationChange(line);
          refreshAllPanelsUI();
          recalcTabTotal(panel);
        });

        btn.addEventListener('click', () => {
          const key = sel.value || '';
          const qn  = Number(qty.value || 0);
          if (key) allocated[key] = Math.max(0, (allocated[key] || 0) - qn);
          line.remove();
          refreshAllPanelsUI();
          recalcTabTotal(panel);
        });
      }

      function addLine() {
        const allowKeys = new Set(
          $$('.js-line .js-sel', linesBox).map(s => s.value).filter(Boolean)
        );
        const options  = buildOptionsHtml(allowKeys);

        const wrapper = document.createElement('div');
        wrapper.innerHTML = `
          <div class="row g-3 align-items-start js-line mb-3">
            <div class="col-12 col-lg-6">
              <select class="form-select js-sel">
                <option value="">-- selecione um item --</option>
                ${options}
              </select>
            </div>
            <div class="col-6 col-lg-2">
              <div class="d-flex flex-column">
                <input type="number" class="form-control js-qty" min="0" placeholder="Qtd">
                <div class="form-text js-max-help mt-1"></div>
              </div>
            </div>
            <div class="col-6 col-lg-2">
              <input type="text" class="form-control js-unit" placeholder="Vlr Unit." readonly>
            </div>
            <div class="col-6 col-lg-2">
              <div class="d-flex gap-2">
                <input type="text" class="form-control js-total" placeholder="Total" readonly>
                <button type="button" class="btn btn-outline-danger js-remove" title="Remover linha">×</button>
              </div>
            </div>
          </div>
        `;
        const line = wrapper.firstElementChild;
        linesBox.appendChild(line);
        bindLine(line);
        refreshAllPanelsUI();
        recalcTabTotal(panel);
      }

      addLine();
      if (addBtn) addBtn.addEventListener('click', addLine);

      content.appendChild(panel);
      recalcTabTotal(panel); // garantia inicial
    }

    root.appendChild(bar);
    root.appendChild(content);

    log('renderTabs ->', n);
    updateAllMax();
  }

  // ---------- coleta de payload ----------
  function collectChildrenPayload(modalEl){
    const panels = modalEl.querySelectorAll('.tab-pane-like');
    const children = [];
    panels.forEach(panel => {
      const lines = [];
      panel.querySelectorAll('.js-line').forEach(line => {
        const key = (line.querySelector('.js-sel')?.value || '').trim();
        const qty = parseInt(line.querySelector('.js-qty')?.value || '0', 10);
        const unitField = (line.querySelector('.js-unit')?.value || '')
          .replace(/\./g,'')
          .replace(',', '.');
        const unit = parseFloat(unitField || '0');
        if (key && qty > 0) {
          const ln = { key, qty };
          if (isFinite(unit) && unit > 0) ln.unit = unit;
          lines.push(ln);
        }
      });

      // NOVO: pega a observação e a DataAgendado desta aba
      const observacao = (panel.querySelector('.js-obs')?.value || '').trim();
      const data_agendado = (panel.querySelector('.js-date')?.value || '').trim();

      // inclui no objeto do filho (undefined se vazios)
      children.push({
        lines,
        observacao: observacao || undefined,
        data_agendado: data_agendado || undefined
      });
    });
    while (children.length && !(children[children.length-1].lines||[]).length) children.pop();
    return { children };
  }

  // ---------- resolver URL do POST (corrige 404 por falta de prefixo) ----------
  function resolveSaveUrl(modalEl, pedidoId){
    const btn = modalEl.querySelector('#saveSplitBtn');

    const explicit = btn?.dataset.saveUrl;
    if (explicit) return explicit;

    const modalPrefix = modalEl.getAttribute('data-url-prefix');
    if (modalPrefix) return `${modalPrefix}/pedidos/${pedidoId}/desmembrar_salvar`;

    const appPrefix = window.__APP_PREFIX__ || document.body?.dataset?.appPrefix || '';
    if (appPrefix) return `${appPrefix.replace(/\/+$/,'')}/pedidos/${pedidoId}/desmembrar_salvar`;

    try {
      const path = window.location.pathname || '';
      const idx = path.indexOf('/pedidos/');
      if (idx > 0) {
        const prefix = path.slice(0, idx);
        if (prefix) return `${prefix}/pedidos/${pedidoId}/desmembrar_salvar`;
      }
    } catch(_) {}

    return `/pedidos/${pedidoId}/desmembrar_salvar`;
  }

  // ---------- binding do botão SALVAR ----------
  function bindSave(modalEl){
    const btn = modalEl.querySelector('#saveSplitBtn');
    if (!btn || btn.__boundSave__) return;
    btn.__boundSave__ = true;

    btn.addEventListener('click', async () => {
      const pedidoIdAttr = btn.dataset.pedidoId;
      const pedidoId = parseInt(pedidoIdAttr, 10);
      if (!Number.isFinite(pedidoId)) {
        alert('Pedido inválido para salvar.');
        return;
      }

      const payload  = collectChildrenPayload(modalEl);
      if (!payload.children.length) {
        alert('Selecione pelo menos um item em alguma aba.');
        return;
      }

      const url = resolveSaveUrl(modalEl, pedidoId);
      if (DEBUG) {
        log('POST payload =>', payload);
        log('POST url =>', url);
      }

      btn.disabled = true;
      try {
        const res = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            // 'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
          },
          credentials: 'same-origin',
          body: JSON.stringify(payload)
        });

        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          const msg = data?.error || `Falha ao salvar (HTTP ${res.status})`;
          throw new Error(msg);
        }

        alert(`Sucesso! Pedidos filhos criados: ${data.filhos.join(', ')}`);
        try { window.bootstrap?.Modal?.getOrCreateInstance(modalEl)?.hide(); } catch(_) {}
        location.reload();
      } catch (e) {
        console.error('[desmembrar] salvar erro:', e);
        alert('Erro: ' + (e.message || e));
      } finally {
        btn.disabled = false;
      }
    });
  }

  // ---------- binding idempotente do modal ----------
  function bindModal(modalEl) {
    if (!modalEl || modalEl.__desmBound__) return;
    modalEl.__desmBound__ = true;

    const input = $('#qtdPedidosInput', modalEl);
    if (input) {
      renderTabs(modalEl, input.value || '1');
      input.addEventListener('input',  e => renderTabs(modalEl, e.target.value));
      input.addEventListener('change', e => renderTabs(modalEl, e.target.value));

      const moVal = new MutationObserver(() => renderTabs(modalEl, input.value));
      moVal.observe(input, { attributes: true, attributeFilter: ['value'] });
    }

    modalEl.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.win-tab[data-origin]');
      if (!btn) return;
      const originValue = btn.dataset.origin;
      $$('.win-tabs .win-tab', modalEl).forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      $$('.tab-pane-like', modalEl).forEach(p => p.style.display = (p.dataset.target === originValue ? 'block' : 'none'));
    });

    const go = $('#goToWizardBtn', modalEl);
    if (go && !go.__go__) {
      go.__go__ = true;
      go.addEventListener('click', (ev) => {
        ev.preventDefault();
        const n = parseInt(($('#qtdPedidosInput', modalEl)?.value) || '0', 10);
        if (!Number.isFinite(n) || n < 1) {
          alert('Informe um número de pedidos válido (mínimo 1).');
          $('#qtdPedidosInput', modalEl)?.focus();
          return;
        }
        try { window.bootstrap?.Modal?.getOrCreateInstance(modalEl)?.hide(); } catch(_) {}
        // TODO: ligar na rota do wizard
      });
    }

    const label = modalEl.querySelector('label.form-label');
    if (label && !label.getAttribute('for')) {
      label.setAttribute('for', 'qtdPedidosInput');
    }

    bindSave(modalEl);

    log('bindModal OK');
  }

  // ---------- inicialização robusta ----------
  const mo = new MutationObserver(() => {
    const modal = $('#desmembrarModal');
    if (modal) bindModal(modal);
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });

  document.addEventListener('shown.bs.modal', (ev) => {
    if (ev.target && ev.target.id === 'desmembrarModal') {
      bindModal(ev.target);
    }
  });

  const m0 = $('#desmembrarModal');
  if (m0) bindModal(m0);

  log('script loaded');

  window.__desmembrarInit = function(el) {
    try {
      const modalEl = el && el.id === 'desmembrarModal' ? el : (el ? el.querySelector('#desmembrarModal') : $('#desmembrarModal'));
      if (modalEl) bindModal(modalEl);
    } catch (e) {
      console.error('[desmembrar] init error:', e);
    }
  };
})();
