//! Tiny healthcheck binary for the distroless runtime image.
//!
//! GETs ``http://localhost:${METRICS_PORT:-9101}/healthz`` and exits
//! 0 iff the response is HTTP 200 within 3 seconds. Anything else
//! (connection refused, timeout, non-200) → exit 1.
//!
//! Implemented with raw ``std::net::TcpStream`` + a hand-rolled HTTP
//! GET so we don't pull hyper / reqwest into a 200-line binary —
//! keeps the static-linked image layer small and the build fast.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::Duration;

fn main() {
    let port: u16 = std::env::var("METRICS_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(9101);

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
    // "HTTP/1.x 200" — we only care about the status code.
    let head = &buf[..n.min(12)];
    head.windows(4).any(|w| w == b" 200")
}
