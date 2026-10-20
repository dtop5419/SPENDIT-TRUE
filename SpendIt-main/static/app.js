document.addEventListener('DOMContentLoaded', () => {
  const flash = document.querySelectorAll('.toast-spend');
  flash.forEach((el, i) => setTimeout(() => el.remove(), 4200 + i * 300));

  document.querySelectorAll('[data-add-cart]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.addCart;
      const old = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<i class="bi bi-check2"></i> Добавляем';
      try {
        const r = await fetch(`/cart/add/${id}`, {method:'POST', headers:{'X-Requested-With':'XMLHttpRequest'}});
        const data = await r.json();
        if (data.requires_auth && data.login_url) { window.location.href = data.login_url; return; }
        if (!data.success) throw new Error(data.message);
        document.querySelectorAll('[data-cart-count]').forEach(x => x.textContent = data.cart_count);
        btn.innerHTML = '<i class="bi bi-check2-circle"></i> В корзине';
        setTimeout(() => { btn.innerHTML = old; btn.disabled = false; }, 1300);
      } catch (e) {
        btn.innerHTML = '<i class="bi bi-exclamation-triangle"></i> Ошибка';
        setTimeout(() => { btn.innerHTML = old; btn.disabled = false; }, 1600);
      }
    });
  });

  document.querySelectorAll('[data-favorite]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.favorite;
      const r = await fetch(`/favorite/toggle/${id}`, {method:'POST'});
      const data = await r.json();
      if (data.requires_auth && data.login_url) { window.location.href = data.login_url; return; }
      if (data.success) btn.classList.toggle('active', data.favorite);
    });
  });

  document.querySelectorAll('[data-budget]').forEach(chip => {
    chip.addEventListener('click', () => {
      const input = document.querySelector('#budget');
      if (input) { input.value = chip.dataset.budget; input.form.submit(); }
    });
  });

  const aiInput = document.querySelector('#aiQuery');
  const aiClear = document.querySelector('#aiClear');
  if (aiClear && aiInput) aiClear.addEventListener('click', () => { aiInput.value = ''; aiInput.focus(); });
  if (aiInput) {
    document.querySelectorAll('[data-query]').forEach(chip => chip.addEventListener('click', () => {
      aiInput.value = chip.dataset.query;
      aiInput.focus();
    }));
  }

  const assistantForm = document.querySelector('#assistantForm');
  const responseBox = document.querySelector('#assistantResponse');
  if (assistantForm && aiInput && responseBox) {
    assistantForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = aiInput.value.trim();
      if (!message) return;
      responseBox.classList.remove('d-none');
      responseBox.innerHTML = '<span class="assistant-label">SpendIt Assistant</span><div class="assistant-thinking"><span></span><span></span><span></span> Анализирую запрос…</div>';
      try {
        const result = await fetch('/api/assistant', {
          method: 'POST', headers: {'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
          body: JSON.stringify({message})
        });
        const data = await result.json();
        if (!data.ok) throw new Error(data.answer || 'Ошибка');
        let html = `<span class="assistant-label">SpendIt Assistant · ${data.kind === 'products' ? 'каталог' : 'ответ'}</span><div>${escapeHtml(data.answer || '')}</div>`;
        if (data.marketplaces?.corrected_query) html += `<div class="assistant-correction">Искал по запросу: <strong>${escapeHtml(data.marketplaces.corrected_query)}</strong></div>`;
        if (data.products?.length) {
          html += '<div class="assistant-products">';
          for (const p of data.products.slice(0, 100)) {
            const safeUrl = safeProductUrl(p.url);
            const source = p.source ? ` · ${escapeHtml(p.source)}` : '';
            const image = p.image ? `<img class="assistant-product-image" src="${escapeHtml(p.image)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : '';
            html += `<div class="assistant-product">${image}<div class="assistant-product-info"><a href="${safeUrl}" target="${p.source && p.source !== 'Spendit' ? '_blank' : '_self'}" rel="noopener">${escapeHtml(p.name)}</a><div class="muted small">${escapeHtml(p.why || 'подходит по запросу')}${source}</div></div><span class="price">${formatPrice(p.price)} ₽</span></div>`;
          }
          const providers = data.marketplaces?.providers;
          if (providers && Object.keys(providers).length) {
            const counts = Object.entries(providers).map(([name, info]) => `${escapeHtml(name)}: ${info.count || 0}`).join(' · ');
            html += `<div class="muted small mt-2">Результаты маркетплейсов · ${counts}${data.marketplaces.cached ? ' · из кэша' : ''}</div>`;
          }
          html += '</div>';
        }
        if (data.marketplaces?.search_links && Object.keys(data.marketplaces.search_links).length) {
          const links = Object.entries(data.marketplaces.search_links).map(([name, url]) => `<a class="small-link me-3" href="${safeProductUrl(url)}" target="_blank" rel="noopener">Искать на ${escapeHtml(name)} <i class="bi bi-box-arrow-up-right"></i></a>`).join('');
          html += `<div class="mt-3">${links}</div>`;
        }
        if (data.link) {
          const label = data.kind === 'weather' ? 'Открыть погоду' : 'Открыть сайт';
          html += `<a class="btn btn-sm btn-gradient rounded-pill assistant-link" href="${data.link}" target="_blank" rel="noopener">${label} <i class="bi bi-box-arrow-up-right ms-1"></i></a>`;
        }
        responseBox.innerHTML = html;
      } catch (error) {
        responseBox.innerHTML = '<span class="assistant-label">SpendIt Assistant</span><div>Не получилось обработать запрос. Попробуйте ещё раз.</div>';
      }
    });
  }

  function safeProductUrl(value) {
    try {
      const parsed = new URL(value, window.location.origin);
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return escapeHtml(parsed.href);
    } catch (_) {}
    return '#';
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }
  function formatPrice(value) { return Number(value).toLocaleString('ru-RU'); }
});

(() => {
  const body = document.body;
  const buttons = document.querySelectorAll('[data-theme]');
  const stored = localStorage.getItem('spendit-theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const apply = (theme) => {
    body.dataset.theme = theme;
    localStorage.setItem('spendit-theme', theme);
    document.querySelectorAll('.theme-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.theme === theme));
  };
  apply(stored || (prefersDark ? 'dark' : 'light'));
  document.querySelectorAll('.theme-btn').forEach(btn => btn.addEventListener('click', () => apply(btn.dataset.theme)));
})();
