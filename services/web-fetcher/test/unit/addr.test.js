'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { notPublic } = require('../../src/addr');

test('ordinary public addresses may be dialled', () => {
  for (const ip of ['1.1.1.1', '8.8.8.8', '93.184.215.14', '203.0.114.1', '2606:4700:4700::1111', '2a00:1450:4001:81b::200e']) {
    assert.equal(notPublic(ip), null, ip);
  }
});

test('this host, private networks, link-local and metadata addresses are refused by kind', () => {
  const cases = {
    '127.0.0.1': 'loopback', '127.8.8.8': 'loopback', '0.0.0.0': 'this_network', '0.1.2.3': 'this_network',
    '10.0.0.5': 'private', '172.16.0.1': 'private', '172.31.255.254': 'private', '192.168.65.254': 'private',
    '169.254.169.254': 'link_local', '169.254.0.1': 'link_local', '100.64.0.1': 'carrier_nat', '100.100.100.200': 'carrier_nat',
    '192.0.2.1': 'documentation', '198.51.100.7': 'documentation', '203.0.113.9': 'documentation', '198.18.0.1': 'benchmark',
    '192.0.0.8': 'reserved', '192.88.99.1': 'reserved', '224.0.0.1': 'multicast', '239.255.255.250': 'multicast',
    '240.0.0.1': 'reserved', '255.255.255.255': 'reserved',
    '::1': 'loopback', '::': 'unspecified', 'fe80::1': 'link_local', 'fc00::1': 'private', 'fd00:ec2::254': 'private',
    'fec0::1': 'private', 'ff02::1': 'multicast', '2001:db8::1': 'documentation', '2001:0::1': 'teredo', '100::1': 'discard',
  };
  for (const [ip, kind] of Object.entries(cases)) assert.equal(notPublic(ip), kind, ip);
});

test('the neighbours of a private range are not swept up with it', () => {
  for (const ip of ['172.15.255.255', '172.32.0.1', '11.0.0.1', '9.255.255.255', '192.167.1.1', '192.169.1.1', '100.63.255.255', '100.128.0.1', '169.253.1.1', '198.17.0.1', '198.20.0.1']) {
    assert.equal(notPublic(ip), null, ip);
  }
});

test('an IPv4 address carried inside an IPv6 one is judged as that IPv4 address', () => {
  assert.equal(notPublic('::ffff:127.0.0.1'), 'loopback');
  assert.equal(notPublic('::ffff:169.254.169.254'), 'link_local');
  assert.equal(notPublic('::ffff:a9fe:a9fe'), 'link_local');
  assert.equal(notPublic('::ffff:8.8.8.8'), null);
  assert.equal(notPublic('64:ff9b::a00:1'), 'private'); // NAT64 of 10.0.0.1
  assert.equal(notPublic('64:ff9b::808:808'), 'nat64'); // NAT64 is not dialled even to a public address
  assert.equal(notPublic('2002:c0a8:101::1'), 'private'); // 6to4 of 192.168.1.1
  assert.equal(notPublic('2002:808:808::1'), 'six_to_four');
  assert.equal(notPublic('::10.0.0.1'), 'private');
});

test('what is not an address is refused', () => {
  for (const s of ['', 'localhost', '1.2.3', '999.1.1.1', 'gggg::1', '1.1.1.1.1']) assert.equal(notPublic(s), 'unreadable', s);
});
