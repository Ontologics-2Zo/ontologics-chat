import test from 'node:test';
import assert from 'node:assert/strict';
import { isBomPosted } from '../web/mks2/dashboard.js';

test('BOM entry stays registration until the server reports a posted BOM', () => {
  assert.equal(isBomPosted({ summary: { bom_status: 'unregistered' }, bom: null }), false);
  assert.equal(isBomPosted({ summary: { bom_status: 'POSTED_SOURCE' }, bom: null }), false);
  assert.equal(isBomPosted({ summary: { bom_status: 'POSTED_SOURCE' }, bom: { bom_id: 'b-1' } }), true);
  assert.equal(isBomPosted(undefined), false);
});
