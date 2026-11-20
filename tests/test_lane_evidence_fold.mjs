import test from 'node:test';
import assert from 'node:assert/strict';
import { foldEvidence, EVIDENCE_UNFOLD_LIMIT } from '../web/mks2/lanegraph.js';

// Fold keys as the lane view uses them: BOM rows and source rows per BOM version.
const groupOf = id => (id.startsWith('line-') ? 'gL:0' : id.startsWith('row-') ? 'gR:0' : null);
const lines = n => Array.from({ length: n }, (_, i) => `line-${i}`);
const rows = n => Array.from({ length: n }, (_, i) => `row-${i}`);

test('a long evidence list keeps BOM rows and source rows folded and counts them', () => {
  const { pin, folded } = foldEvidence(['ord', 'cfg', ...lines(73), ...rows(73)], groupOf);
  assert.deepEqual(pin, []);
  assert.deepEqual([...folded], [['gL:0', 73], ['gR:0', 73]]);
});

test('a few evidence rows are still opened one by one', () => {
  const { pin, folded } = foldEvidence([...lines(EVIDENCE_UNFOLD_LIMIT), 'row-1'], groupOf);
  assert.deepEqual(pin, [...lines(EVIDENCE_UNFOLD_LIMIT), 'row-1']);
  assert.equal(folded.size, 0);
});

test('nothing is dropped: every foldable evidence node is either pinned or counted', () => {
  const ids = [...lines(9), ...rows(2), 'ord', 'line-0'];
  const { pin, folded } = foldEvidence(ids, groupOf);
  const counted = [...folded.values()].reduce((sum, n) => sum + n, 0);
  assert.equal(pin.length + counted, new Set(ids.filter(groupOf)).size);
});

test('nodes that never fold are left to the caller', () => {
  const { pin, folded } = foldEvidence(['ord', 'cfg'], groupOf);
  assert.deepEqual(pin, []);
  assert.equal(folded.size, 0);
});
