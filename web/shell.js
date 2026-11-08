// The preserved and MK-S2 pages have independent documents and UI state.
// Workspace selection and manual checks never call a model or write business data.
const choices = ['current', 'mks2', 'checks'];
const buttons = choices.map(name => document.getElementById(`workspace-${name}`));
const frames = {
  current: document.getElementById('frame-current'),
  mks2: document.getElementById('frame-mks2'),
};
function show(name) {
  const selected = choices.includes(name) ? name : 'current';
  for (const choice of choices) {
    const active = choice === selected;
    document.getElementById(`panel-${choice}`).hidden = !active;
    const button = document.getElementById(`workspace-${choice}`);
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
  }
  if (selected === 'mks2' && !frames.mks2.hasAttribute('src')) frames.mks2.src = '/mks2/#graph';
}
function route() { show(location.hash.slice(1)); }
for (const [index, button] of buttons.entries()) {
  button.addEventListener('click', () => { location.hash = choices[index]; });
  button.addEventListener('keydown', event => {
    let next;
    if (event.key === 'ArrowRight') next = (index + 1) % choices.length;
    if (event.key === 'ArrowLeft') next = (index + choices.length - 1) % choices.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = choices.length - 1;
    if (next === undefined) return;
    event.preventDefault(); buttons[next].focus(); location.hash = choices[next];
  });
}
window.addEventListener('hashchange', route);
const manual = [...document.querySelectorAll('[data-manual-check]')];
function updateCount() {
  document.getElementById('manual-count').textContent = `수동 확인 ${manual.filter(box => box.checked).length} / ${manual.length}`;
}
for (const box of manual) box.addEventListener('change', updateCount);
document.getElementById('clear-checks').addEventListener('click', () => {
  for (const box of manual) box.checked = false;
  updateCount();
});
for (const button of document.querySelectorAll('[data-open-screen]')) {
  button.addEventListener('click', () => {
    const screen = button.dataset.openScreen === 'dashboard' ? 'dashboard' : 'graph';
    if (!frames.mks2.hasAttribute('src')) frames.mks2.src = `/mks2/#${screen}`;
    else if (frames.mks2.contentDocument?.readyState === 'complete') frames.mks2.contentWindow.location.hash = screen;
    else frames.mks2.addEventListener('load', () => { frames.mks2.contentWindow.location.hash = screen; }, {once:true});
    location.hash = 'mks2';
  });
}
route();updateCount();
