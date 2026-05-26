document.addEventListener('DOMContentLoaded', function () {
    updateDateTime();
    setInterval(updateDateTime, 1000);

    document.querySelectorAll('[data-logout-confirm]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            e.preventDefault();
            var m = document.getElementById('logoutModal');
            if (m) bootstrap.Modal.getOrCreateInstance(m).show();
        });
    });

    var searchInput = document.getElementById('tableSearch');
    if (searchInput) searchInput.addEventListener('keyup', filterTableRows);

    var pendingForm = null;
    document.querySelectorAll('[data-confirm-action]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            if (this.disabled) return;
            pendingForm = this.closest('form');
            var m = document.getElementById('confirmModal');
            if (m && pendingForm) bootstrap.Modal.getOrCreateInstance(m).show();
        });
    });
    var confirmBtn = document.getElementById('confirmActionBtn');
    if (confirmBtn) {
        confirmBtn.addEventListener('click', function () {
            if (pendingForm) { pendingForm.submit(); pendingForm = null; }
        });
    }

    var roleSelect = document.getElementById('role');
    var userIdInput = document.getElementById('user_id');
    if (roleSelect && userIdInput) {
        roleSelect.addEventListener('change', function () {
            var p = { student: '220145', teacher: 'CSE101', admin: 'ADMIN01' };
            if (p[this.value]) userIdInput.placeholder = p[this.value];
        });
    }

    loadReportsChart();
});

function updateDateTime() {
    var now = new Date();
    var d = document.getElementById('currentDate');
    var t = document.getElementById('currentTime');
    if (d) d.textContent = now.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
    if (t) t.textContent = now.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
}

function filterTableRows() {
    var input = document.getElementById('tableSearch');
    var table = document.getElementById('dataTable');
    if (!input || !table) return;
    var f = input.value.toLowerCase();
    table.querySelectorAll('tbody tr').forEach(function (row) {
        row.style.display = row.textContent.toLowerCase().includes(f) ? '' : 'none';
    });
}

function loadReportsChart() {
    var el = document.getElementById('reports-chart-data');
    var ctx = document.getElementById('reportsChart');
    if (!el || !ctx || typeof Chart === 'undefined') return;
    try {
        var data = JSON.parse(el.textContent);
        if (!data.labels || data.labels.length === 0) return;
        new Chart(ctx, {
            type: 'bar',
            data: {
                labels: data.labels,
                datasets: [{ label: 'Bookings', data: data.values, backgroundColor: '#6c757d' }]
            },
            options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { stepSize: 1 } } } }
        });
    } catch (e) { console.error(e); }
}
