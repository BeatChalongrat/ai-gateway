'use strict';
// The one door a fetch's browser has to the network.
//
// Each fetch gets a proxy of its own on the loopback interface. The browser is
// started with it as its only route, so every connection the page causes —
// the document, a script, an image, a frame, a redirect, a socket — arrives
// here as a request for a host and a port. The proxy opens exactly one
// destination: the host the fetch named, at the public address that name was
// resolved to before the browser started, on the web's own port. Everything
// else is answered 403 and written down. It also counts the bytes that come
// back and closes the door when the fetch's allowance is spent.

const http = require('node:http');
const net = require('node:net');

function realDial(ip, port) {
  return net.connect({ host: ip, port });
}

function startProxy({ host, ip, ports = [80, 443], maxBytes, dial = realDial }) {
  const stats = { bytes: 0, overflow: false, reached: new Set(), refused: new Map() };
  const sockets = new Set();
  const spent = [];

  const refuse = (h) => stats.refused.set(h, (stats.refused.get(h) || 0) + 1);
  const allowed = (h, p) => h === host && ports.includes(p);
  const count = (n) => {
    stats.bytes += n;
    if (maxBytes && stats.bytes > maxBytes && !stats.overflow) {
      stats.overflow = true;
      for (const s of sockets) s.destroy();
      for (const fn of spent) fn();
    }
  };
  const track = (s) => {
    sockets.add(s);
    s.on('close', () => sockets.delete(s));
    s.on('error', () => {});
    return s;
  };

  const server = http.createServer((req, res) => {
    // a plain-HTTP request arrives with its whole URL
    let u;
    try {
      u = new URL(req.url);
    } catch {
      res.writeHead(400).end();
      return;
    }
    const h = u.hostname.replace(/^\[|\]$/g, '');
    const p = Number(u.port || 80);
    if (u.protocol !== 'http:' || !allowed(h, p) || stats.overflow) {
      refuse(h);
      res.writeHead(403, { 'content-type': 'text/plain' }).end('not the host this fetch named');
      return;
    }
    const headers = { ...req.headers, host: u.host };
    delete headers['proxy-connection'];
    delete headers['proxy-authorization'];
    const up = http.request({ method: req.method, path: u.pathname + u.search, headers, createConnection: () => track(dial(ip, p)) });
    up.on('response', (r) => {
      stats.reached.add(h);
      res.writeHead(r.statusCode, r.headers);
      r.on('data', (c) => {
        count(c.length);
        if (!stats.overflow) res.write(c);
      });
      r.on('end', () => res.end());
      r.on('error', () => res.destroy());
    });
    up.on('error', () => {
      if (!res.headersSent) res.writeHead(502);
      res.end();
    });
    req.pipe(up);
  });

  server.on('connect', (req, client, head) => {
    track(client);
    const at = req.url.lastIndexOf(':');
    const h = req.url.slice(0, at).replace(/^\[|\]$/g, '').toLowerCase();
    const p = Number(req.url.slice(at + 1));
    if (!allowed(h, p) || stats.overflow) {
      refuse(h);
      client.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');
      return;
    }
    const up = track(dial(ip, p));
    up.on('connect', () => {
      stats.reached.add(h);
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (head && head.length) up.write(head);
      up.on('data', (c) => {
        count(c.length);
        if (!stats.overflow) client.write(c);
      });
      client.on('data', (c) => up.write(c));
      up.on('end', () => client.end());
      client.on('end', () => up.end());
    });
    up.on('error', () => client.end('HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'));
  });
  server.on('connection', track);

  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      resolve({
        port: server.address().port,
        stats,
        onOverflow: (fn) => spent.push(fn),
        close: () =>
          new Promise((done) => {
            for (const s of sockets) s.destroy();
            server.close(() => done());
          }),
      });
    });
  });
}

module.exports = { startProxy, realDial };
