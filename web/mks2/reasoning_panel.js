// 논리 검사 패널 (프로젝트 그래프): SHACL 제약 결과와 OWL RL 추론 검사 결과를 같은 원장 버전으로 보여 준다.
// 값은 /api/mks2/reasoning 응답만 쓴다. 추론 결과는 원장 사실이 아니며 수량·답변에 쓰지 않는다.

const el = (tag, className = '', text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
};
const count = value => Number.isFinite(value) ? value.toLocaleString('ko-KR') : '미확인';

/** Short status for the collapsed line: tone and text. */
export function reasoningSummary(report) {
  if (!report || typeof report !== 'object') return { tone: 'mute', text: '추론 검사 미확인' };
  if (report.state === 'INCONSISTENT') return { tone: 'bad', text: `모순 ${count(report.inconsistency_count)}건` };
  if (report.state === 'TYPE_MISMATCH') return { tone: 'warn', text: `유형 불일치 ${count(report.type_mismatch_count)}건` };
  if (report.state === 'CONSISTENT') return { tone: 'ok', text: '모순·불일치 없음' };
  if (report.state === 'NOT_RUN') return { tone: 'mute', text: '추론 검사 실행 안 됨' };
  return { tone: 'mute', text: '추론 검사 실패' };
}

export function mountReasoningPanel(container, report, { shaclState = null, labelFor = id => id, onFocus = () => {} } = {}) {
  const box = el('details', 'mks-reasoning');
  const head = el('summary');
  const status = reasoningSummary(report);
  head.append(el('b', '', '논리 검사'), el('span', `mks-reasoning-pill ${status.tone}`, status.text));
  box.append(head);
  const body = el('div', 'mks-reasoning-body');
  const row = (label, value, tone = '') => { const p = el('p', 'row'); p.append(el('span', '', label), el('b', tone, value)); return p; };
  body.append(row('SHACL 제약', shaclState === 'READY' ? '적합' : shaclState ? `그래프 ${shaclState}` : '미확인', shaclState === 'READY' ? 'ok' : ''));
  if (report && report.engine) {
    body.append(row('OWL RL 추론', `${report.profile || 'OWL 2 RL'} · v${report.ledger_version ?? '?'}`),
      row('모순 (disjoint)', `${count(report.inconsistency_count)}건`, report.inconsistency_count ? 'bad' : 'ok'),
      row('유형 불일치', `${count(report.type_mismatch_count)}건`, report.type_mismatch_count ? 'warn' : 'ok'),
      row('추론으로 더한 관계', `${count(report.inferred_relationship_count)}건`),
      row('검사 시간', Number.isFinite(report.elapsed_ms) ? `${count(report.elapsed_ms)}ms · 객체 ${count(report.entity_count)} · 관계 ${count(report.relationship_count)}` : '미확인'));
    const items = [
      ...(report.inconsistencies || []).map(item => ({ id: item.entity_id, tone: 'bad', text: `서로 배타적인 ${item.classes.join(' · ')}에 함께 속함` })),
      ...(report.type_mismatches || []).map(item => ({ id: item.entity_id, tone: 'warn',
        text: `선언 ${item.declared} → 추론 ${item.entailed.join(', ')}${item.via.length ? ` · ${item.via.join(', ')}` : ''}` }))];
    if (items.length) {
      const list = el('ul');
      for (const item of items) {
        const li = el('li'), button = el('button', item.tone, labelFor(item.id) || item.id);
        button.type = 'button'; button.title = '그래프에서 이 객체 보기';
        button.addEventListener('click', () => onFocus(item.id));
        li.append(button, el('span', '', item.text)); list.append(li);
      }
      body.append(list);
    }
  }
  body.append(el('small', '', '추론 결과는 원장 사실이 아니며 수량·답변에 쓰지 않습니다. 이 검사는 그래프 준비 상태를 바꾸지 않습니다.'));
  box.append(body);
  container.append(box);
  return box;
}
