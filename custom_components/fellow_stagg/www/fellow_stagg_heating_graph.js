/* Fellow Stagg EKG Pro – Live Heating Graph card
 * Polls pwmprt data and shows: Current Temp (tempr), Target (setp), Heater Effort (out).
 */

(function () {
  const CARD_VERSION = "1.0.0";
  const POLL_MS = 1000;
  const CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js";

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector('script[src="' + src + '"]')) {
        resolve(window.Chart);
        return;
      }
      const el = document.createElement("script");
      el.src = src;
      el.onload = () => resolve(window.Chart);
      el.onerror = reject;
      document.head.appendChild(el);
    });
  }

  const template = document.createElement("template");
  template.innerHTML = `
    <style>
      .fellow-stagg-graph-card {
        padding: 16px;
      }
      .fellow-stagg-graph-card .message {
        padding: 1em;
        text-align: center;
        color: var(--secondary-text-color);
      }
      .fellow-stagg-graph-card .stability {
        margin-top: 8px;
        padding: 6px 10px;
        border-radius: 4px;
        background: var(--success-color, #4caf50);
        color: white;
        font-size: 0.9em;
      }
      .fellow-stagg-graph-card canvas {
        max-width: 100%;
        height: 280px !important;
      }
    </style>
    <div class="fellow-stagg-graph-card">
      <div class="message" id="message"></div>
      <div id="stability" class="stability" style="display:none;"></div>
      <div id="chartWrap" style="display:none;"><canvas id="chart"></canvas></div>
    </div>
  `;

  class FellowStaggHeatingGraphCard extends HTMLElement {
    constructor() {
      super();
      this.attachShadow({ mode: "open" });
      this.shadowRoot.appendChild(template.content.cloneNode(true));
      this._config = {};
      this._hass = null;
      this._chart = null;
      this._pollTimer = null;
      this._entryId = null;
    }

    setConfig(config) {
      this._config = config || {};
      this._entryId = this._config.entry_id || null;
    }

    set hass(hass) {
      this._hass = hass;
      if (this._config.entity && hass && hass.states[this._config.entity]) {
        const entity = hass.states[this._config.entity];
        const entityReg = hass.entities && hass.entities[this._config.entity];
        const deviceId = entityReg && entityReg.device_id;
        if (deviceId && hass.devices && hass.devices[deviceId]) {
          const device = hass.devices[deviceId];
          const ce = device.config_entries && device.config_entries[0];
          if (ce) this._entryId = ce;
        }
      }
      if (!this._entryId) {
        this._showMessage("Set entry_id in the card config (or an entity from this device).");
        return;
      }
      this._startPolling();
    }

    _showMessage(text) {
      const msg = this.shadowRoot.getElementById("message");
      const wrap = this.shadowRoot.getElementById("chartWrap");
      const stab = this.shadowRoot.getElementById("stability");
      if (msg) msg.textContent = text;
      if (wrap) wrap.style.display = text ? "none" : "block";
      if (stab) stab.style.display = "none";
    }

    _showStability(visible, text) {
      const el = this.shadowRoot.getElementById("stability");
      if (!el) return;
      el.style.display = visible ? "block" : "none";
      el.textContent = text || "Water is perfectly stabilized at target temperature.";
    }

    async _fetchData() {
      if (!this._hass || !this._entryId) return null;
      try {
        const resp = await fetch(
          "/api/fellow_stagg/graph_data?entry_id=" + encodeURIComponent(this._entryId),
          { credentials: "same-origin" }
        );
        if (!resp.ok) return null;
        return await resp.json();
      } catch (e) {
        return null;
      }
    }

    _startPolling() {
      this._stopPolling();
      const poll = () => {
        this._pollTimer = setTimeout(async () => {
          const json = await this._fetchData();
          if (json) {
            if (json.stable) this._showStability(true);
            else this._showStability(false);
            this._updateChart(json.data || []);
          }
          poll();
        }, POLL_MS);
      };
      // First fetch immediately
      this._fetchData().then((json) => {
        if (json) {
          if (json.stable) this._showStability(true);
          this._updateChart(json.data || []);
        } else {
          this._showMessage("Turn on the Live Heating Graph switch and wait a few seconds.");
        }
        poll();
      });
    }

    _stopPolling() {
      if (this._pollTimer) {
        clearTimeout(this._pollTimer);
        this._pollTimer = null;
      }
    }

    async _updateChart(data) {
      if (!data || data.length === 0) {
        this._showMessage("No data yet. Turn on the Live Heating Graph switch.");
        return;
      }
      this._showMessage("");
      const wrap = this.shadowRoot.getElementById("chartWrap");
      if (wrap) wrap.style.display = "block";

      await loadScript(CHART_JS_CDN);
      const canvas = this.shadowRoot.getElementById("chart");
      if (!canvas) return;

      const labels = data.map((d) => d.t);
      const tempr = data.map((d) => (d.tempr != null ? d.tempr : null));
      const setp = data.map((d) => (d.setp != null ? d.setp : null));
      const out = data.map((d) => (d.out != null ? Math.max(0, d.out) : null));

      if (this._chart) this._chart.destroy();
      this._chart = new window.Chart(canvas, {
        type: "line",
        data: {
          labels,
          datasets: [
            {
              label: "Current temp (°C)",
              data: tempr,
              borderColor: "rgb(33, 150, 243)",
              backgroundColor: "rgba(33, 150, 243, 0.1)",
              fill: false,
              yAxisID: "y",
              tension: 0.2,
            },
            {
              label: "Target (°C)",
              data: setp,
              borderColor: "rgb(76, 175, 80)",
              borderDash: [5, 5],
              fill: false,
              yAxisID: "y",
              tension: 0,
            },
            {
              label: "Heater %",
              data: out,
              borderColor: "rgb(255, 152, 0)",
              backgroundColor: "rgba(255, 152, 0, 0.2)",
              fill: true,
              yAxisID: "y1",
              tension: 0.2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: {
            y: {
              type: "linear",
              display: true,
              position: "left",
              title: { display: true, text: "Temperature (°C)" },
              min: (ctx) => {
                const vals = ctx.chart.data.datasets[0].data.filter((v) => v != null);
                return vals.length ? Math.min(...vals) - 5 : 0;
              },
              max: (ctx) => {
                const vals = ctx.chart.data.datasets[0].data.filter((v) => v != null);
                return vals.length ? Math.max(...vals) + 10 : 100;
              },
            },
            y1: {
              type: "linear",
              display: true,
              position: "right",
              title: { display: true, text: "Heater effort %" },
              min: 0,
              max: 100,
              grid: { drawOnChartArea: false },
            },
          },
        },
      });
    }

    getCardSize() {
      return 4;
    }

    disconnectedCallback() {
      this._stopPolling();
      if (this._chart) this._chart.destroy();
    }
  }

  customElements.define("fellow-stagg-heating-graph", FellowStaggHeatingGraphCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "fellow-stagg-heating-graph",
    name: "Fellow Stagg Heating Graph",
    description: "Live PID graph: current temp, target, heater effort.",
    preview: true,
  });
})();
