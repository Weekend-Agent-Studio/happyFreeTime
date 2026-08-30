import { renderConcierge, setupConcierge } from "./concierge.js";
import { renderCompare, setupCompare } from "./compare.js";
import { renderLedger, setupLedger } from "./ledger.js";

const variants = [
  { render: renderConcierge, setup: setupConcierge },
  { render: renderCompare, setup: setupCompare },
  { render: renderLedger, setup: setupLedger },
];

const stage = document.getElementById("stage");
const picker = document.querySelector(".proto-picker");
const highlight = picker.querySelector(".proto-picker-highlight");
const items = [...picker.querySelectorAll(".proto-picker-item:not(.proto-picker-replay)")];
const replay = picker.querySelector(".proto-picker-replay");
let current = 0;

function moveHighlight() {
  const el = items[current];
  highlight.style.width = `${el.offsetWidth}px`;
  highlight.style.transform = `translateX(${el.offsetLeft}px)`;
}

function mount(i) {
  stage.innerHTML = "";
  requestAnimationFrame(() => {
    stage.innerHTML = variants[i].render();
    variants[i].setup(stage);
  });
}

function setActive(i) {
  if (i < 0 || i >= variants.length) return;
  current = i;
  items.forEach((el, j) => {
    el.toggleAttribute("data-active", j === i);
    if (j === i) el.setAttribute("aria-current", "true");
    else el.removeAttribute("aria-current");
  });
  moveHighlight();
  const url = new URL(location);
  url.searchParams.set("v", i + 1);
  history.replaceState(null, "", url);
  mount(i);
}

items.forEach((el, i) => el.addEventListener("click", () => setActive(i)));
replay?.addEventListener("click", () => mount(current));
window.addEventListener("resize", moveHighlight);

document.addEventListener("keydown", (event) => {
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) || event.target.isContentEditable) return;
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const num = Number.parseInt(event.key, 10);
  if (num >= 1 && num <= variants.length) setActive(num - 1);
  else if (event.key === "ArrowRight") setActive((current + 1) % variants.length);
  else if (event.key === "ArrowLeft") setActive((current - 1 + variants.length) % variants.length);
  else if (event.key === "r" || event.key === "R") mount(current);
});

setActive((Number.parseInt(new URLSearchParams(location.search).get("v"), 10) || 1) - 1);
requestAnimationFrame(() => requestAnimationFrame(() => picker.setAttribute("data-ready", "")));
