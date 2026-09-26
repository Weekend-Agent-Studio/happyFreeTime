import { bindDecisionInteractions, evidenceDisclosure, plans } from "./data.js";

export function renderLedger() {
  return `
    <section class="decision-module ledger-module proto-enter" aria-labelledby="ledger-title">
      <div class="ledger-intro">
        <div><h2 id="ledger-title">先看每套怎么走，再决定取舍</h2><p>路线账本把站点、移动和风险放在同一行；第一套在体验与返程确定性之间最平衡。</p></div>
        <div class="ledger-legend"><span><i class="legend-stop"></i>停靠点</span><span><i class="legend-route"></i>移动</span></div>
      </div>

      <div class="ledger-list">
        ${plans.map((plan, index) => `
          <article class="ledger-row proto-item" style="--item-index:${index}" data-plan-id="${plan.id}" ${index === 0 ? "data-selected" : ""}>
            <div class="ledger-plan-name"><span>${index === 0 ? "推荐" : plan.strategy}</span><h3>${plan.title}</h3><p>${plan.difference}</p></div>
            <div class="route-ledger" aria-label="${plan.stops.join("到")}">
              <div class="route-ledger-stop"><i>${plan.times[0]}</i><strong>${plan.stops[0]}</strong></div>
              <div class="route-ledger-line"><span>${plan.distance}</span></div>
              <div class="route-ledger-stop"><i>${plan.times[1]}</i><strong>${plan.stops[1]}</strong></div>
            </div>
            <div class="ledger-numbers"><span><small>时长</small><strong>${plan.duration}</strong></span><span><small>预算</small><strong>${plan.budgetShort}/人</strong></span></div>
            <div class="ledger-action"><small>${plan.warningShort}</small><button type="button" data-plan-select="${plan.id}" aria-pressed="${index === 0}">查看行程</button></div>
          </article>
        `).join("")}
      </div>
      ${evidenceDisclosure()}
      <p class="selection-announcement" data-selection-announcement aria-live="polite"></p>
    </section>
  `;
}

export function setupLedger(stage) {
  bindDecisionInteractions(stage);
}
