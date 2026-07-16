// ============================================================================
// Plugin Chart.js para desenhar a “agulha” nos gauges
// ============================================================================
const gaugeNeedle = {
  id: 'gaugeNeedle',
  afterDatasetDraw(chart, args, options) {
    const { ctx } = chart;
    const value = options.value;
    const total = chart.data.datasets[0].data.reduce((a, b) => a + b, 0);
    const angle = Math.PI + (value / total) * Math.PI;
    const arcMeta = chart.getDatasetMeta(0).data[0];
    const { x, y, outerRadius, innerRadius } = arcMeta;
    const length = (outerRadius + innerRadius) / 2;

    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    ctx.beginPath();
    ctx.moveTo(-5, 0);
    ctx.lineTo(length, 0);
    ctx.lineTo(-5, 0);
    ctx.fillStyle = getComputedStyle(document.documentElement)
                     .getPropertyValue('--primary').trim();
    ctx.fill();
    ctx.restore();

    ctx.beginPath();
    ctx.arc(x, y, 6, 0, 2 * Math.PI);
    ctx.fill();
  }
};
Chart.register(gaugeNeedle);

// ============================================================================
// Plugin para desenhar texto central no gauge
// ============================================================================
const gaugeCenterText = {
  id: 'gaugeCenterText',
  afterDraw(chart) {
    const opts = chart.config.options.elements?.center;
    if (!opts) return;
    const ctx = chart.ctx;
    const cx  = (chart.chartArea.left + chart.chartArea.right) / 2;
    const cy  = chart.chartArea.bottom - (chart.chartArea.height * 0.3);

    ctx.save();
    ctx.font         = `bold ${opts.fontSize || 24}px ${opts.fontFamily || 'Arial'}`;
    ctx.fillStyle    = opts.color || '#000';
    ctx.textAlign    = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(opts.text[0], cx, cy);
    if (opts.text[1]) {
      ctx.font = `${(opts.fontSize || 24) * 0.75}px ${opts.fontFamily}`;
      ctx.fillText(opts.text[1], cx, cy + (opts.fontSize || 24) * 0.8);
    }
    ctx.restore();
  }
};
Chart.register(gaugeCenterText);

// ============================================================================
// Renderiza bar-chart de “Clientes Pedidos”
// ============================================================================
function renderBarChart(data) {
  const c = document.getElementById('bar-chart');
  if (!c) return;
  if (c._chart) c._chart.destroy();

  const ctx = c.getContext('2d');
  c._chart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: data.map(d => d.label),
      datasets: [{
        label: 'Total Pedido (R$)',
        data: data.map(d => d.value),
        backgroundColor: 'rgba(0,170,255,0.8)',
        borderColor:   '#004100',
        borderWidth:    1,
        borderRadius:   4,
        barPercentage:  0.6
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          ticks:       { precision: 0 },
          grid:        { color: 'rgba(0,0,0,0.05)' }
        },
        x: { grid: { display: false } }
      },
      layout: { padding: 10 },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => `R$ ${ctx.parsed.y.toLocaleString('pt-BR',{minimumFractionDigits:2})}`
          }
        }
      }
    }
  });
}

// ============================================================================
// Renderiza metas (progress bars)
// ============================================================================
function renderGoals(goals) {
  goals.forEach((g, i) => {
    const p = document.getElementById(`goal${i+1}`);
    const s = p?.nextElementSibling;
    if (!p || !s) return;
    p.value       = g.current;
    s.textContent = `${g.current}/${g.total}`;
  });
}

// ============================================================================
// Inicializa Leaflet + MarkerCluster e plota ativos já geocodificados
// ============================================================================
let mapInstance, markerCluster;
function initBrazilMap() {
  if (mapInstance) return;

  // Zoom inicial em 5 para já exibir SP e RJ separados
  mapInstance = L.map('world-map', {
    center: [-14.2350, -51.9253],
    zoom:    5,
    minZoom: 4,
    maxZoom: 12
  });

  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; OpenStreetMap contributors'
  }).addTo(mapInstance);

  markerCluster = L.markerClusterGroup({
    showCoverageOnHover:     false,
    maxClusterRadius:        30,  // agrupa somente pontos muito próximos
    spiderfyOnMaxZoom:       true,
    disableClusteringAtZoom: 5    // a partir do zoom 5, não agrupa
  });
  mapInstance.addLayer(markerCluster);

  window.assetsLocations.forEach(loc => {
    const marker = L.marker([loc.lat, loc.lon], {
      title: `${loc.cidade} - ${loc.uf}`
    });
    marker.bindTooltip(
      `IDs Ativos:<br>${loc.ids.join(', ')}`,
      { direction: 'top', offset: [0, -10], opacity: 0.9 }
    );
    markerCluster.addLayer(marker);
  });
}

// ============================================================================
// Inicializa gauges, metas e bar-chart após o DOM carregar
// ============================================================================
function initializeDashboard(serverData) {
  renderBarChart(window.clientOrders);
  renderGoals(serverData.goals || []);
  if (document.getElementById('gauge1')) {
    createGauge('gauge1', serverData.metrics.actives,    'Ativos');
    createGauge('gauge2', serverData.metrics.notes,      'Notas Débito');
    createGauge('gauge3', serverData.metrics.cancelled,  'Cancelados');
    createGauge('gauge4', serverData.metrics.production, 'Produção');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initBrazilMap();
  fetch('/admin/shempo/v1/data')
    .then(r => r.ok ? r.json() : Promise.reject())
    .then(initializeDashboard)
    .catch(err => {
      console.error(err);
      initializeDashboard({
        metrics: { actives:0, notes:0, cancelled:0, production:0 },
        goals: []
      });
    });
});
