import { bindDecisionInteractions, evidenceDisclosure, plans } from "./data.js";

const rows = [
  ["定位", (plan) => plan.difference],
  ["总时长", (plan) => plan.duration],
  ["全程距离", (plan) => plan.distance],
  ["人均预算", (plan) => plan.budget],
  ["主要提醒", (plan) => plan.warningShort],
  ["路线事实", (plan) => plan.routeSource],
];

export function renderCompare() {
  return `
    <section class="decision-module compare-module proto-enter" aria-labelledby="compare-title">
      <div class="compare-intro">
        <div><h2 id="compare-title">三套方案都可行，区别在路程、预算和确定性</h2><p>综合排序仍推荐“${plans[0].title}”；如果你更在意少走路或少花钱，另外两套会更合适。</p></div>
        <span>已通过硬约束校验</span>
      </div>

      <div class="comparison-scroll" role="region" aria-label="三方案平行比较" tabindex="0">
        <div class="comparison-matrix">
          <div class="matrix-corner">比较项</div>
          ${plans.map((plan, index) => `
            <div class="matrix-plan-head" data-plan-id="${plan.id}" ${index === 0 ? "data-selected" : ""}>
              <span>${index === 0 ? "综合推荐" : plan.strategy}</span>
              <strong>${plan.title}</strong>
              <button type="button" data-plan-select="${plan.id}" aria-pressed="${index === 0}">选择</button>
            </div>
          `).join("")}
          ${rows.map(([label, value]) => `
            <div class="matrix-label">${label}</div>
            ${plans.map((plan) => `<div class="matrix-value" data-plan-id="${plan.id}">${value(plan)}</div>`).join("")}
          `).join("")}
          <div class="matrix-label">站点</div>
          ${plans.map((plan) => `<div class="matrix-value route-value" data-plan-id="${plan.id}">${plan.stops.join(" → ")}</div>`).join("")}
        </div>
      </div>
      ${evidenceDisclosure()}
      <p class="selection-announcement" data-selection-announcement aria-live="polite"></p>
    </section>
  `;
}

export function setupCompare(stage) {
  bindDecisionInteractions(stage);
}
