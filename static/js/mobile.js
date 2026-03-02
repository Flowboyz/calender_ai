(function initMobileNav() {
    if (window.innerWidth > 480) return;

    // Build tab bar
    const bar = document.createElement('div');
    bar.className = 'tab-bar';
    bar.innerHTML = `
    <button class="tab-btn active" data-panel="left" data-label="calendar">
        <svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>
        Calendar
    </button>
    <button class="tab-btn" data-panel="right" data-label="add">
        <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
        Add / Search
    </button>
    `;
    document.body.appendChild(bar);

    // Show first panel
    document.querySelector('.left').classList.add('tab-active');

    // Wire up tabs
    bar.addEventListener('click', e => {
    const btn = e.target.closest('.tab-btn');
    if (!btn) return;

    bar.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    const target = btn.dataset.panel;
    document.querySelector('.left').classList.toggle('tab-active',  target === 'left');
    document.querySelector('.right').classList.toggle('tab-active', target === 'right');
    });
    })();
