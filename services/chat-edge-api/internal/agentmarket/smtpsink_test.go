package agentmarket

import (
	"bufio"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"math/big"
	"net"
	"strings"
	"sync"
	"testing"
	"time"
)

// smtpSink is a small SMTP server for tests (AM7): it speaks EHLO, STARTTLS,
// AUTH PLAIN, MAIL, RCPT, DATA and QUIT, and keeps what it received. Only
// the relay is simulated; the connector talks real SMTP to it.
type smtpSink struct {
	t        *testing.T
	ln       net.Listener
	tlsCfg   *tls.Config
	roots    *x509.CertPool
	startTLS bool   // advertise STARTTLS
	wantUser string // AUTH PLAIN required with this user / password when set
	wantPass string
	reject   string // a RCPT address answered 550

	mu   sync.Mutex
	got  []sinkMessage
	auth []string // "user:pass" as received
}

type sinkMessage struct {
	From  string
	Rcpt  []string
	Data  string
	TLS   bool
	Authd bool
}

func newSMTPSink(t *testing.T) *smtpSink {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	s := &smtpSink{t: t, ln: ln}
	s.tlsCfg, s.roots = testCert(t)
	go s.serve()
	t.Cleanup(func() { _ = ln.Close() })
	return s
}

func (s *smtpSink) port() int { return s.ln.Addr().(*net.TCPAddr).Port }

func (s *smtpSink) messages() []sinkMessage {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]sinkMessage(nil), s.got...)
}

func (s *smtpSink) serve() {
	for {
		c, err := s.ln.Accept()
		if err != nil {
			return
		}
		go s.session(c)
	}
}

func (s *smtpSink) session(conn net.Conn) {
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(20 * time.Second))
	r := bufio.NewReader(conn)
	w := bufio.NewWriter(conn)
	say := func(line string) { _, _ = w.WriteString(line + "\r\n"); _ = w.Flush() }
	var msg sinkMessage
	secure, authd := false, false
	say("220 sink.test ESMTP")
	for {
		line, err := r.ReadString('\n')
		if err != nil {
			return
		}
		line = strings.TrimRight(line, "\r\n")
		up := strings.ToUpper(line)
		switch {
		case strings.HasPrefix(up, "EHLO"), strings.HasPrefix(up, "HELO"):
			ext := []string{"250-sink.test", "250-8BITMIME"}
			if s.startTLS && !secure {
				ext = append(ext, "250-STARTTLS")
			}
			if s.wantUser != "" {
				ext = append(ext, "250-AUTH PLAIN")
			}
			ext = append(ext, "250 SIZE 10240000")
			for _, x := range ext {
				_, _ = w.WriteString(x + "\r\n")
			}
			_ = w.Flush()
		case up == "STARTTLS":
			say("220 go ahead")
			tc := tls.Server(conn, s.tlsCfg)
			if err := tc.Handshake(); err != nil {
				return
			}
			conn, secure = tc, true
			r, w = bufio.NewReader(tc), bufio.NewWriter(tc)
		case strings.HasPrefix(up, "AUTH PLAIN"):
			raw := strings.TrimSpace(line[len("AUTH PLAIN"):])
			if raw == "" {
				say("334 ")
				raw, _ = r.ReadString('\n')
				raw = strings.TrimSpace(raw)
			}
			b, _ := base64.StdEncoding.DecodeString(raw)
			parts := strings.Split(string(b), "\x00")
			if len(parts) == 3 {
				s.mu.Lock()
				s.auth = append(s.auth, parts[1]+":"+parts[2])
				s.mu.Unlock()
			}
			if len(parts) == 3 && parts[1] == s.wantUser && parts[2] == s.wantPass {
				authd = true
				say("235 ok")
			} else {
				say("535 5.7.8 authentication failed")
			}
		case strings.HasPrefix(up, "MAIL FROM:"):
			if s.wantUser != "" && !authd {
				say("530 5.7.0 authentication required")
				continue
			}
			msg = sinkMessage{From: angle(line[len("MAIL FROM:"):]), TLS: secure, Authd: authd}
			say("250 ok")
		case strings.HasPrefix(up, "RCPT TO:"):
			to := angle(line[len("RCPT TO:"):])
			if s.reject != "" && strings.EqualFold(to, s.reject) {
				say("550 5.1.1 no such user")
				continue
			}
			msg.Rcpt = append(msg.Rcpt, to)
			say("250 ok")
		case up == "DATA":
			say("354 go ahead")
			var b strings.Builder
			for {
				l, err := r.ReadString('\n')
				if err != nil {
					return
				}
				if l == ".\r\n" {
					break
				}
				b.WriteString(strings.TrimPrefix(l, "."))
			}
			msg.Data = b.String()
			s.mu.Lock()
			s.got = append(s.got, msg)
			s.mu.Unlock()
			say("250 queued")
		case up == "RSET", up == "NOOP":
			say("250 ok")
		case up == "QUIT":
			say("221 bye")
			return
		default:
			say("502 not here")
		}
	}
}

// testCert is a throwaway certificate for 127.0.0.1 and the pool that trusts it.
func testCert(t *testing.T) (*tls.Config, *x509.CertPool) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tpl := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "sink.test"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, DNSNames: []string{"localhost"},
		KeyUsage: x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		IsCA: true, BasicConstraintsValid: true}
	der, err := x509.CreateCertificate(rand.Reader, tpl, tpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, _ := x509.ParseCertificate(der)
	pool := x509.NewCertPool()
	pool.AddCert(cert)
	return &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}}}, pool
}

// angle is the address between < and > (what follows, like BODY=8BITMIME, is not).
func angle(s string) string {
	if i := strings.IndexByte(s, '<'); i >= 0 {
		if j := strings.IndexByte(s[i:], '>'); j > 0 {
			return s[i+1 : i+j]
		}
	}
	return strings.TrimSpace(s)
}
