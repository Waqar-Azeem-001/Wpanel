package handlers

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"math/big"
	"os/exec"
	"strings"
	"time"

	"whagent/internal/render"
)

// Certificates (ADR 08): get one over HTTP-01, install it into the account's web server configuration, read its status, switch renewal and the HTTPS redirect, remove it, and
// renew those close to expiry (RenewDue, run daily by the agent's scheduler). Certificate and key files belong to root and the key is readable by root only: the account's own user
// can never read it. Never run against the real Let's Encrypt.

const (
	renewWithinDays = 30
	expiringDays    = 14
)

// AcmeError is a refusal from the certificate authority, with a short code for WHPanel.
type AcmeError struct{ Code, Message string }

func (e *AcmeError) Error() string { return e.Code + ": " + e.Message }

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"ISSUE_SSL": a.sslIssue, "REMOVE_SSL": a.sslRemove, "SSL_STATUS": a.sslStatus, "SET_SSL_OPTIONS": a.sslOptions}
	})
}

type tlsPaths struct{ Dir, Chain, Key string }

func tlsFor(account, domain string) tlsPaths {
	base := "/etc/whp/tls/" + account + "/" + domain
	return tlsPaths{base, base + "/fullchain.pem", base + "/privkey.pem"}
}

func (a *Agent) now() time.Time {
	if a.Now != nil {
		return a.Now()
	}
	return time.Now()
}

func (a *Agent) readCert(account, domain string) *x509.Certificate {
	text, ok := a.Sys.Read(tlsFor(account, domain).Chain)
	if !ok {
		return nil
	}
	block, _ := pem.Decode([]byte(text))
	if block == nil {
		return nil
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return nil
	}
	return cert
}

func isoUTC(t time.Time) string { return t.UTC().Format("2006-01-02T15:04:05") + "+00:00" }

func floorDays(d time.Duration) int64 {
	days := int64(d / (24 * time.Hour))
	if d < 0 && d%(24*time.Hour) != 0 {
		days--
	}
	return days
}

func (a *Agent) sslRow(account string, st State) map[string]any {
	domain := st.text("domain")
	cert := a.readCert(account, domain)
	if cert == nil {
		return map[string]any{"domain": domain, "status": "none", "issuer": "", "issued_at": nil, "expires_at": nil, "days_left": nil, "auto_renew": false, "https_redirect": false}
	}
	left := floorDays(cert.NotAfter.Sub(a.now()))
	status := "active"
	if left < 0 {
		status = "expired"
	} else if left <= expiringDays {
		status = "expiring"
	}
	m, _ := st["ssl"].(map[string]any)
	autoRenew, redirect := true, false
	if v, ok := m["auto_renew"].(bool); ok {
		autoRenew = v
	}
	if v, ok := m["https_redirect"].(bool); ok {
		redirect = v
	}
	return map[string]any{"domain": domain, "status": status, "issuer": cert.Issuer.CommonName, "issued_at": isoUTC(cert.NotBefore), "expires_at": isoUTC(cert.NotAfter), "days_left": left,
		"auto_renew": autoRenew, "https_redirect": redirect}
}

func (a *Agent) writeVhost(account string, st State) error {
	text, err := render.Vhost(account, st.text("domain"), st.flag("suspended"), sslOf(st))
	if err != nil {
		return err
	}
	if err := a.Sys.Write(render.PathsOf(account, st.text("domain")).Vhost, text, 0o644, "root", ""); err != nil {
		return err
	}
	return a.reloadWeb()
}

func acmeErr(err error) error {
	var ae *AcmeError
	if errors.As(err, &ae) {
		return herr(ae.Code, "%s", ae.Message)
	}
	return err
}

func (a *Agent) sslIssue(p map[string]any) (Result, error) {
	account, domain := str(p, "account"), str(p, "domain")
	st, err := a.need(account, domain)
	if err != nil {
		return Result{}, err
	}
	if st.flag("suspended") {
		return Result{}, herr("suspended", "This website is suspended.")
	}
	if a.Acme == nil {
		return Result{}, herr("not_available", "Certificates are not set up on this node.")
	}
	paths := tlsFor(account, domain)
	old := st["ssl"]
	chain, key, err := a.Acme.Obtain([]string{domain, "www." + domain}, render.PathsOf(account, domain).Docroot, str(p, "method"))
	if err != nil {
		return Result{}, acmeErr(err)
	}
	if err := a.Sys.Mkdir(paths.Dir, 0o700, "root", ""); err != nil {
		return Result{}, err
	}
	if err := a.Sys.Write(paths.Key, key, 0o600, "root", ""); err != nil { // root only
		return Result{}, err
	}
	if err := a.Sys.Write(paths.Chain, chain, 0o644, "root", ""); err != nil {
		return Result{}, err
	}
	next := st.copy()
	if old != nil {
		next["ssl"] = old
	} else {
		next["ssl"] = map[string]any{"auto_renew": true, "https_redirect": false}
	}
	if err := a.writeVhost(account, next); err != nil {
		restore := st.copy() // the web server refused the new configuration: put back what worked
		restore["ssl"] = old
		if old == nil {
			_ = a.Sys.Remove(paths.Dir)
		}
		text, _ := render.Vhost(account, domain, false, sslOf(restore))
		_ = a.Sys.Write(render.PathsOf(account, domain).Vhost, text, 0o644, "root", "")
		return Result{}, err
	}
	if err := a.stateWrite(render.PathsOf(account, "").State, next); err != nil {
		return Result{}, err
	}
	return Result{"issued", a.sslRow(account, next)}, nil
}

func (a *Agent) sslRemove(p map[string]any) (Result, error) {
	account, domain := str(p, "account"), str(p, "domain")
	st, err := a.need(account, domain)
	if err != nil {
		return Result{}, err
	}
	if st["ssl"] == nil && !a.Sys.Exists(tlsFor(account, domain).Chain) {
		return Result{"absent", nil}, nil
	}
	next := st.copy()
	next["ssl"] = nil
	if err := a.writeVhost(account, next); err != nil {
		return Result{}, err
	}
	if err := a.Sys.Remove(tlsFor(account, domain).Dir); err != nil {
		return Result{}, err
	}
	return Result{"removed", nil}, a.stateWrite(render.PathsOf(account, "").State, next)
}

func (a *Agent) sslStatus(p map[string]any) (Result, error) {
	account := str(p, "account")
	st := a.stateRead(render.PathsOf(account, "").State)
	rows := []any{}
	if st != nil {
		rows = append(rows, a.sslRow(account, st))
	}
	return Result{"listed", map[string]any{"certificates": rows}}, nil
}

func (a *Agent) sslOptions(p map[string]any) (Result, error) {
	account, domain := str(p, "account"), str(p, "domain")
	st, err := a.need(account, domain)
	if err != nil {
		return Result{}, err
	}
	if st["ssl"] == nil {
		return Result{}, herr("no_certificate", "There is no certificate for this domain yet.")
	}
	next := st.copy()
	next["ssl"] = map[string]any{"auto_renew": str(p, "auto_renew") == "yes", "https_redirect": str(p, "https_redirect") == "yes"}
	if err := a.writeVhost(account, next); err != nil {
		return Result{}, err
	}
	if err := a.stateWrite(render.PathsOf(account, "").State, next); err != nil {
		return Result{}, err
	}
	return Result{"changed", a.sslRow(account, next)}, nil
}

// RenewResult is one line of the daily renewal report.
type RenewResult struct{ Account, Domain, Outcome string }

// RenewDue renews every certificate with 30 days or less left whose owner has not switched renewal off. One failure never stops the others, and the web server is reloaded once.
func (a *Agent) RenewDue(accounts []string) []RenewResult {
	var done []RenewResult
	reload := false
	for _, account := range accounts {
		st := a.stateRead(render.PathsOf(account, "").State)
		if st == nil || st["ssl"] == nil || st.flag("suspended") {
			continue
		}
		if m, _ := st["ssl"].(map[string]any); m != nil {
			if v, ok := m["auto_renew"].(bool); ok && !v {
				continue
			}
		}
		row := a.sslRow(account, st)
		days, ok := row["days_left"].(int64)
		if !ok || days > renewWithinDays {
			continue
		}
		domain := st.text("domain")
		chain, key, err := a.Acme.Obtain([]string{domain, "www." + domain}, render.PathsOf(account, domain).Docroot, "http-01")
		if err != nil {
			code := err.Error()
			var ae *AcmeError
			if errors.As(err, &ae) {
				code = ae.Code
			}
			done = append(done, RenewResult{account, domain, code})
			continue
		}
		paths := tlsFor(account, domain)
		if a.Sys.Write(paths.Key, key, 0o600, "root", "") != nil || a.Sys.Write(paths.Chain, chain, 0o644, "root", "") != nil {
			done = append(done, RenewResult{account, domain, "write_failed"})
			continue
		}
		reload = true
		done = append(done, RenewResult{account, domain, "renewed"})
	}
	if reload {
		_ = a.reloadWeb() // one reload for all
	}
	return done
}

// ---- getting certificates --------------------------------------------------------------------------------------------------------------------------

// FakeAcme makes real self-signed certificates with the dates and names asked for, so everything that reads and installs them is tested for real.
type FakeAcme struct {
	Days     int
	IssuedAt time.Time // zero means now
	Fail     *AcmeError
	Calls    [][]string
}

// Obtain makes a certificate.
func (f *FakeAcme) Obtain(names []string, webroot, method string) (string, string, error) {
	f.Calls = append(f.Calls, append([]string{webroot, method}, names...))
	if f.Fail != nil {
		return "", "", f.Fail
	}
	days := f.Days
	if days == 0 {
		days = 90
	}
	start := f.IssuedAt
	if start.IsZero() {
		start = time.Now()
	}
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return "", "", err
	}
	serial, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 100))
	issuer := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Fake ACME CA"}} // the issuer's name comes from the signing certificate
	tmpl := &x509.Certificate{SerialNumber: serial, Subject: pkix.Name{CommonName: names[0]}, NotBefore: start, NotAfter: start.Add(time.Duration(days) * 24 * time.Hour), DNSNames: names}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, issuer, &key.PublicKey, key)
	if err != nil {
		return "", "", err
	}
	keyDER, _ := x509.MarshalPKCS8PrivateKey(key)
	return string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})), string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})), nil
}

// Certbot gets certificates from Let's Encrypt over HTTP-01 with the certbot program. It has never been run against the real service. DNS-01 (wildcards) is not available.
type Certbot struct {
	Email string
	Read  func(path string) ([]byte, error)
}

// Obtain asks certbot, then reads the files it wrote.
func (c *Certbot) Obtain(names []string, webroot, method string) (string, string, error) {
	if method != "http-01" {
		return "", "", &AcmeError{"not_implemented", "Only HTTP-01 validation is available on this node."}
	}
	argv := []string{"certonly", "--webroot", "-w", webroot, "--non-interactive", "--agree-tos", "--email", c.Email, "--cert-name", names[0], "--keep-until-expiring"}
	for _, n := range names {
		argv = append(argv, "-d", n)
	}
	out, err := exec.Command("certbot", argv...).CombinedOutput()
	if err != nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		last := lines[len(lines)-1]
		if len(last) > 300 {
			last = last[:300]
		}
		return "", "", &AcmeError{"acme_failed", last}
	}
	live := "/etc/letsencrypt/live/" + names[0] + "/"
	chain, err1 := c.Read(live + "fullchain.pem")
	key, err2 := c.Read(live + "privkey.pem")
	if err1 != nil || err2 != nil {
		return "", "", herr("system_error", "certbot finished but its files could not be read")
	}
	return string(chain), string(key), nil
}
