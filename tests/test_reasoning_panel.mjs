import test from 'node:test';
import assert from 'node:assert/strict';
import { reasoningSummary } from '../web/mks2/reasoning_panel.js';

test('the collapsed reasoning line names the worst finding first', () => {
  assert.deepEqual(reasoningSummary({ state: 'INCONSISTENT', inconsistency_count: 1, type_mismatch_count: 4 }), { tone: 'bad', text: '모순 1건' });
  assert.deepEqual(reasoningSummary({ state: 'TYPE_MISMATCH', inconsistency_count: 0, type_mismatch_count: 6 }), { tone: 'warn', text: '유형 불일치 6건' });
  assert.deepEqual(reasoningSummary({ state: 'CONSISTENT' }), { tone: 'ok', text: '모순·불일치 없음' });
  assert.equal(reasoningSummary({ state: 'ERROR' }).text, '추론 검사 실패');
  assert.equal(reasoningSummary(null).text, '추론 검사 미확인');
});
