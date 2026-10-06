'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { decide } = require('../../src/robots');

const TOKEN = 'AgentMarketFetcher';

test('no file, an empty file and a file with no rule for the path all allow', () => {
  assert.equal(decide('', '/p/1', TOKEN).allowed, true);
  assert.equal(decide('User-agent: *\nDisallow:\n', '/p/1', TOKEN).allowed, true);
  assert.equal(decide('User-agent: *\nDisallow: /cart\n', '/p/1', TOKEN).allowed, true);
});

test('a rule for every crawler applies to this one, and the rule that decided is named', () => {
  const d = decide('User-agent: *\nDisallow: /product/\n', '/product/123', TOKEN);
  assert.deepEqual([d.allowed, d.rule, d.group], [false, 'Disallow: /product/', '*']);
  assert.equal(decide('User-agent: *\nDisallow: /\n', '/', TOKEN).allowed, false);
});

test('a group that names this fetcher is used instead of the group for everyone', () => {
  const text = 'User-agent: *\nDisallow: /\n\nUser-agent: agentmarketfetcher\nAllow: /\n';
  const d = decide(text, '/p/1', TOKEN);
  assert.deepEqual([d.allowed, d.group], [true, 'agentmarketfetcher']);
  const other = 'User-agent: *\nAllow: /\n\nUser-agent: AgentMarketFetcher\nDisallow: /\n';
  assert.equal(decide(other, '/p/1', TOKEN).allowed, false);
});

test('a group for another crawler says nothing about this one', () => {
  assert.equal(decide('User-agent: Googlebot\nDisallow: /\n', '/p/1', TOKEN).allowed, true);
});

test('the longest matching rule decides, and Allow wins a tie', () => {
  const text = 'User-agent: *\nDisallow: /shop\nAllow: /shop/public\n';
  assert.equal(decide(text, '/shop/public/1', TOKEN).allowed, true);
  assert.equal(decide(text, '/shop/private/1', TOKEN).allowed, false);
  assert.equal(decide('User-agent: *\nDisallow: /a\nAllow: /a\n', '/a', TOKEN).allowed, true);
});

test('a star stands for any run of characters and a final dollar for the end', () => {
  const text = 'User-agent: *\nDisallow: /*?sort=\nDisallow: /*.pdf$\n';
  assert.equal(decide(text, '/list?sort=price', TOKEN).allowed, false);
  assert.equal(decide(text, '/list', TOKEN).allowed, true);
  assert.equal(decide(text, '/doc/a.pdf', TOKEN).allowed, false);
  assert.equal(decide(text, '/doc/a.pdf?x=1', TOKEN).allowed, true);
});

test('several user-agent lines share the rules that follow them; comments and case are ignored', () => {
  const text = '# shop\nUSER-AGENT: one\nUser-Agent: * # everyone\nDISALLOW: /x # no\n';
  assert.equal(decide(text, '/x/1', TOKEN).allowed, false);
});

test('a characters that mean something in a pattern language mean nothing here', () => {
  assert.equal(decide('User-agent: *\nDisallow: /a.b(c)+\n', '/a.b(c)+/1', TOKEN).allowed, false);
  assert.equal(decide('User-agent: *\nDisallow: /a.b\n', '/aXb', TOKEN).allowed, true);
});

test('a crawl delay is carried with the decision', () => {
  assert.equal(decide('User-agent: *\nCrawl-delay: 5\nDisallow: /x\n', '/p', TOKEN).delay, 5);
});
