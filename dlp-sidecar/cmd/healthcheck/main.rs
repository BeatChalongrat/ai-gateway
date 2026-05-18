//! Tiny healthcheck binary — same pattern as rate-limiter's.
//!
//! HTTP GET ``http://localhost:${METRICS_PORT:-9102}/healthz`` and
//! exit 0 iff the response is HTTP 200 within 3 seconds.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::Duration;

fn main() {
    let port: u16 = std::env::var("METRICS_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(9102);

    if probe(port) {
        std::process::exit(0);
    }
    std::process::exit(1);
}

fn probe(port: u16) -> bool {
    let target = format!("127.0.0.1:{}", port);
    let addrs: Vec<SocketAddr> = match target.to_socket_addrs() {
        Ok(it) => it.collect(),
        Err(_) => return false,
    };
    let Some(addr) = addrs.first() else { return false };

    let mut stream = match TcpStream::connect_timeout(addr, Duration::from_secs(3)) {
        Ok(s) => s,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));

    let req = format!(
        "GET /healthz HTTP/1.0\r\nHost: localhost:{}\r\nConnection: close\r\n\r\n",
        port
    );
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }

    let mut buf = [0u8; 64];
    let n = match stream.read(&mut buf) {
        Ok(n) => n,
        Err(_) => return false,
    };
    if n < 12 {
        return false;
    }
    let head = &buf[..n.min(12)];
    head.windows(4).any(|w| w == b" 200")
}
