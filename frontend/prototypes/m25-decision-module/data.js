export const plans = [
  {
    id: "calm",
    title: "雨天也从容",
    strategy: "综合推荐",
    duration: "3 小时 30 分",
    distance: "12.4 km",
    budget: "约 ¥168/人",
    budgetShort: "¥168",
    difference: "室内为主，返程最稳",
    reason: "时间、路程和体验最均衡，预计 17:45 回到出发地。",
    warning: "晚餐动态可用性待出发前确认。",
    warningShort: "1 项待确认",
    routeSource: "高德 Replay 已核验",
    stops: ["首都博物馆", "功德林"],
    times: ["14:20", "16:35"],
  },
  {
    id: "nearby",
    title: "少走路，慢慢逛",
    strategy: "少通勤",
    duration: "3 小时 05 分",
    distance: "6.8 km",
    budget: "约 ¥210/人",
    budgetShort: "¥210",
    difference: "路程最短，节奏更松",
    reason: "两站集中在朝阳，通勤少 18 分钟，但预算更高。",
    warning: "户外停留受临时天气变化影响。",
    warningShort: "天气需留意",
    routeSource: "高德实时路线",
    stops: ["日坛公园", "Bleu Marine"],
    times: ["14:15", "16:10"],
  },
  {
    id: "value",
    title: "预算轻一点",
    strategy: "低预算",
    duration: "3 小时 15 分",
    distance: "9.1 km",
    budget: "约 ¥92/人",
    budgetShort: "¥92",
    difference: "人均最低，返程为估算",
    reason: "保留完整活动和晚餐，人均少约 76 元。",
    warning: "返程使用本地估算，实际耗时可能变化。",
    warningShort: "路线为估算",
    routeSource: "本地估算 · 已降级",
    stops: ["北京警察博物馆", "老石水饺"],
    times: ["14:10", "16:20"],
  },
];

export function bindDecisionInteractions(stage) {
  const controls = [...stage.querySelectorAll("[data-plan-select]")];
  const select = (planId) => {
    const plan = plans.find((item) => item.id === planId);
    if (!plan) return;

    controls.forEach((control) => {
      const selected = control.dataset.planSelect === planId;
      control.setAttribute("aria-pressed", String(selected));
    });
    stage.querySelectorAll("[data-plan-id]").forEach((element) => {
      element.toggleAttribute("data-selected", element.dataset.planId === planId);
    });

    document.querySelector("[data-detail-title]").textContent = plan.title;
    document.querySelector("[data-detail-summary]").textContent = `${plan.duration} · ${plan.distance} · ${plan.budget}`;
    document.querySelector("[data-detail-warning]").textContent = plan.warning;
    document.querySelector("[data-detail-route]").innerHTML = plan.stops
      .map((stop, index) => `
        <div class="prototype-route-stop">
          <span>${index + 1}</span>
          <div><strong>${stop}</strong><small>${plan.times[index]}</small></div>
        </div>
      `)
      .join("");

    const announcement = stage.querySelector("[data-selection-announcement]");
    if (announcement) announcement.textContent = `已选择“${plan.title}”，右侧已更新。`;
  };

  controls.forEach((control) => {
    control.addEventListener("click", () => select(control.dataset.planSelect));
  });
  select(controls.find((control) => control.getAttribute("aria-pressed") === "true")?.dataset.planSelect || plans[0].id);
}

export function evidenceDisclosure() {
  return `
    <details class="evidence-disclosure">
      <summary>查看规划依据</summary>
      <div class="evidence-content">
        <div><strong>关键约束</strong><span>14:00–18:00 · 朝阳区出发 · 全程不超过 15 km</span></div>
        <div><strong>外部事实</strong><span>天气与路线均保留来源；降级方案已单独标注</span></div>
        <div><strong>筛选结果</strong><span>3 个方案均已通过返程、营业和距离校验</span></div>
      </div>
    </details>
  `;
}
