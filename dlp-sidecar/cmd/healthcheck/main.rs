//! Tiny healthcheck binary — same pattern as rate-limiter's.
//!
//! HTTP GET ``http://localhost:${METRICS_PORT:-9102}/healthz`` and
//! exit 0 iff the response is HTTP 200 within 3 seconds.
//!
//! FIX103 — it now SAYS what happened. It used to exit 1 with no output,
//! and `docker inspect` showed `ExitCode 1, Output ""` for a sidecar that
//! was really wedged at 100 % CPU; FIX101 read that empty output as "the
//! check cannot run in a distroless image". The check was right all along
//! (watched going red on a deliberately wedged instance in FIX103); it just
//! gave nobody a reason to believe it.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::Duration;

fn main() {
    let port: u16 = std::env::var("METRICS_PORT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(9102);

    match probe(port) {
        Ok(()) => {
            println!("healthz 200 on 127.0.0.1:{port}");
            std::process::exit(0);
        }
        Err(why) => {
            println!("healthz FAILED on 127.0.0.1:{port}: {why}");
            std::process::exit(1);
        }
    }
}

fn probe(port: u16) -> Result<(), String> {
    let target = format!("127.0.0.1:{}", port);
    let addrs: Vec<SocketAddr> = target
        .to_socket_addrs()
        .map_err(|e| format!("cannot resolve: {e}"))?
        .collect();
    let Some(addr) = addrs.first() else {
        return Err("no address".into());
    };

    let mut stream = TcpStream::connect_timeout(addr, Duration::from_secs(3))
        .map_err(|e| format!("connect: {e}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));

    let req = format!(
        "GET /healthz HTTP/1.0\r\nHost: localhost:{}\r\nConnection: close\r\n\r\n",
        port
    );
    stream
        .write_all(req.as_bytes())
        .map_err(|e| format!("write: {e}"))?;

    let mut buf = [0u8; 64];
    let n = stream.read(&mut buf).map_err(|e| {
        format!("no answer within 3 s ({e}) — the runtime serving /healthz is busy or stuck")
    })?;
    if n < 12 {
        return Err(format!("short response ({n} bytes)"));
    }
    let head = &buf[..n.min(12)];
    if head.windows(4).any(|w| w == b" 200") {
        Ok(())
    } else {
        Err(format!("unexpected status line {:?}", String::from_utf8_lossy(head)))
    }
}
