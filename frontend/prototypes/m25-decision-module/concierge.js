import { bindDecisionInteractions, evidenceDisclosure, plans } from "./data.js";

export function renderConcierge() {
  const [recommended, ...alternatives] = plans;
  return `
    <section class="decision-module concierge-module proto-enter" aria-labelledby="concierge-title">
      <div class="butler-message">
        <span class="butler-avatar" aria-hidden="true">H</span>
        <div>
          <h2 id="concierge-title">我更建议“${recommended.title}”</h2>
          <p>${recommended.reason} 另外两套分别更省路程和预算，你可以按今天更在意的取舍来选。</p>
        </div>
      </div>

      <article class="featured-plan plan-surface" data-plan-id="${recommended.id}" data-selected>
        <div class="featured-plan-heading">
          <div><span class="recommendation-label">管家首选</span><h3>${recommended.title}</h3><p>${recommended.difference}</p></div>
          <button type="button" data-plan-select="${recommended.id}" aria-pressed="true">查看这套</button>
        </div>
        <div class="featured-metrics">
          <span><small>总时长</small><strong>${recommended.duration}</strong></span>
          <span><small>全程</small><strong>${recommended.distance}</strong></span>
          <span><small>预算</small><strong>${recommended.budget}</strong></span>
          <span><small>待确认</small><strong>${recommended.warningShort}</strong></span>
        </div>
        <div class="compact-route"><span>${recommended.stops[0]}</span><i aria-hidden="true"></i><span>${recommended.stops[1]}</span><small>${recommended.routeSource}</small></div>
      </article>

      <div class="alternative-heading"><h3>另外两种取舍</h3><p>都满足当前硬约束</p></div>
      <div class="alternative-grid">
        ${alternatives.map((plan, index) => `
          <article class="alternative-plan plan-surface proto-item" style="--item-index:${index + 1}" data-plan-id="${plan.id}">
            <div><span class="strategy-label">${plan.strategy}</span><h3>${plan.title}</h3><p>${plan.difference}</p></div>
            <div class="alternative-metrics"><span>${plan.duration}</span><span>${plan.distance}</span><span>${plan.budgetShort}/人</span></div>
            <small class="plan-warning">${plan.warningShort}</small>
            <button type="button" data-plan-select="${plan.id}" aria-pressed="false">查看这套</button>
          </article>
        `).join("")}
      </div>
      ${evidenceDisclosure()}
      <p class="selection-announcement" data-selection-announcement aria-live="polite"></p>
    </section>
  `;
}

export function setupConcierge(stage) {
  bindDecisionInteractions(stage);
}
