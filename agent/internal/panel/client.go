// Package panel is the agent's conversation with WHPanel. The agent always connects OUT (ADR 16): it enrols once with a one-time token (sending only a certificate request, so its private key
// never leaves the node), then polls for signed commands, reports results, sends heartbeats and renews its certificate before it expires.
package panel

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Version is this agent's version; WHPanel refuses an agent older than its minimum.
const Version = "0.1.0"

// Config is what an enrolled agent keeps in /etc/whp/agent.json (mode 0600).
type Config struct {
	Panel        string   `json:"panel"`
	NodeID       int64    `json:"node_id"`
	Token        string   `json:"token"`
	PublicKey    string   `json:"public_key"`
	CertFile     string   `json:"cert_file"`
	KeyFile      string   `json:"key_file"`
	AcmeEmail    string   `json:"acme_email"`
	Capabilities []string `json:"capabilities"`
}

// Inventory is what a node tells WHPanel about itself when it enrols.
type Inventory struct {
	Hostname     string   `json:"hostname"`
	IPAddresses  []string `json:"ip_addresses"`
	OSName       string   `json:"os_name"`
	CPUCores     int      `json:"cpu_cores"`
	RAMMB        int64    `json:"ram_mb"`
	DiskGB       int64    `json:"disk_gb"`
	Capabilities []string `json:"capabilities"`
}

// LoadConfig reads the saved configuration.
func LoadConfig(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var c Config
	if err := json.Unmarshal(raw, &c); err != nil {
		return nil, err
	}
	return &c, nil
}

// Save writes the configuration so only root can read it.
func (c *Config) Save(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	raw, _ := json.MarshalIndent(c, "", " ")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// Client talks to WHPanel.
type Client struct {
	Base   string
	Token  string
	HTTP   *http.Client
	Config *Config
}

// New builds a client for an enrolled agent, presenting its client certificate when it has one.
func New(c *Config) (*Client, error) {
	transport := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12}}
	if c.CertFile != "" && c.KeyFile != "" {
		if cert, err := tls.LoadX509KeyPair(c.CertFile, c.KeyFile); err == nil {
			transport.TLSClientConfig.Certificates = []tls.Certificate{cert}
		}
	}
	return &Client{Base: strings.TrimRight(c.Panel, "/"), Token: c.Token, Config: c, HTTP: &http.Client{Transport: transport, Timeout: 60 * time.Second}}, nil
}

// StatusError is a refusal from WHPanel.
type StatusError struct {
	Status  int
	Message string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("WHPanel answered %d: %s", e.Status, e.Message)
}

func (c *Client) post(path string, body any, wait time.Duration, out any) error {
	raw, _ := json.Marshal(body)
	req, err := http.NewRequest("POST", c.Base+path, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Version", Version)
	if c.Token != "" {
		req.Header.Set("Authorization", "Agent "+c.Token)
	}
	client := c.HTTP
	if wait > 0 {
		copied := *client
		copied.Timeout = wait + 30*time.Second
		client = &copied
	}
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != 200 {
		var e struct {
			Error string `json:"error"`
		}
		_ = json.Unmarshal(data, &e)
		return &StatusError{resp.StatusCode, e.Error}
	}
	if out != nil {
		return json.Unmarshal(data, out)
	}
	return nil
}

// Heartbeat reports the node's figures.
func (c *Client) Heartbeat(figures map[string]any) error {
	return c.post("/api/agent/v1/heartbeat/", figures, 0, nil)
}

// Poll asks for work, letting WHPanel hold the request open for up to wait seconds. It returns the raw envelope, or nil when there is nothing to do.
func (c *Client) Poll(wait int) (*Envelope, error) {
	var reply struct {
		Job *Envelope `json:"job"`
	}
	if err := c.post(fmt.Sprintf("/api/agent/v1/poll/?wait=%d", wait), map[string]any{}, time.Duration(wait)*time.Second, &reply); err != nil {
		return nil, err
	}
	return reply.Job, nil
}

// Envelope is a signed command as WHPanel sends it.
type Envelope struct {
	Payload   string `json:"payload"`
	Signature string `json:"signature"`
}

// Report is the structured answer for one job.
type Report struct {
	OK      bool           `json:"ok"`
	Code    string         `json:"code"`
	Message string         `json:"message"`
	Data    map[string]any `json:"data"`
}

// Result tells WHPanel how a job ended.
func (c *Client) Result(job int64, r Report) error {
	if r.Data == nil {
		r.Data = map[string]any{}
	}
	return c.post(fmt.Sprintf("/api/agent/v1/jobs/%d/result/", job), r, 0, nil)
}

// ---- enrolment and renewal -------------------------------------------------------------------------------------------------------------------------

func newKeyAndCSR() (*ecdsa.PrivateKey, string, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, "", err
	}
	der, err := x509.CreateCertificateRequest(rand.Reader, &x509.CertificateRequest{Subject: pkix.Name{CommonName: "wh-agent"}}, key) // WHPanel names the certificate itself
	if err != nil {
		return nil, "", err
	}
	return key, string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE REQUEST", Bytes: der})), nil
}

func writeKey(path string, key *ecdsa.PrivateKey) error {
	der, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: der}), 0o600)
}

// Enroll trades the one-time token for an identity. The private key is made here and never sent; only the certificate request is. It returns the configuration to save.
func Enroll(panelURL, token string, inv Inventory, dir string, acmeEmail string, httpClient *http.Client) (*Config, error) {
	key, csr, err := newKeyAndCSR()
	if err != nil {
		return nil, err
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 60 * time.Second}
	}
	c := &Client{Base: strings.TrimRight(panelURL, "/"), HTTP: httpClient}
	var reply struct {
		NodeID           int64  `json:"node_id"`
		Token            string `json:"token"`
		Certificate      string `json:"certificate"`
		CACertificate    string `json:"ca_certificate"`
		SigningPublicKey string `json:"signing_public_key"`
	}
	if err := c.post("/api/agent/v1/enroll/", map[string]any{"token": token, "csr": csr, "node": inv, "agent_version": Version}, 0, &reply); err != nil {
		return nil, err
	}
	cfg := &Config{Panel: strings.TrimRight(panelURL, "/"), NodeID: reply.NodeID, Token: reply.Token, PublicKey: reply.SigningPublicKey, AcmeEmail: acmeEmail,
		CertFile: filepath.Join(dir, "client.pem"), KeyFile: filepath.Join(dir, "client.key"), Capabilities: inv.Capabilities}
	if err := writeKey(cfg.KeyFile, key); err != nil {
		return nil, err
	}
	if err := os.WriteFile(cfg.CertFile, []byte(reply.Certificate), 0o644); err != nil {
		return nil, err
	}
	_ = os.WriteFile(filepath.Join(dir, "ca.pem"), []byte(reply.CACertificate), 0o644)
	return cfg, nil
}

// CertExpires is when the client certificate stops being valid.
func CertExpires(cfg *Config) (time.Time, error) {
	raw, err := os.ReadFile(cfg.CertFile)
	if err != nil {
		return time.Time{}, err
	}
	block, _ := pem.Decode(raw)
	if block == nil {
		return time.Time{}, errors.New("the certificate file is not valid")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		return time.Time{}, err
	}
	return cert.NotAfter, nil
}

// Renew swaps the certificate for a new one; the old one stops working at once. The new key is only put in place after WHPanel has answered.
func (c *Client) Renew() error {
	key, csr, err := newKeyAndCSR()
	if err != nil {
		return err
	}
	var reply struct {
		Certificate string `json:"certificate"`
	}
	if err := c.post("/api/agent/v1/renew/", map[string]any{"csr": csr}, 0, &reply); err != nil {
		return err
	}
	if err := writeKey(c.Config.KeyFile+".new", key); err != nil {
		return err
	}
	if err := os.WriteFile(c.Config.CertFile+".new", []byte(reply.Certificate), 0o644); err != nil {
		return err
	}
	if err := os.Rename(c.Config.KeyFile+".new", c.Config.KeyFile); err != nil {
		return err
	}
	if err := os.Rename(c.Config.CertFile+".new", c.Config.CertFile); err != nil {
		return err
	}
	fresh, err := New(c.Config)
	if err == nil {
		c.HTTP = fresh.HTTP
	}
	return err
}

// ---- what this machine is ----------------------------------------------------------------------------------------------------------------------------

func osName() string {
	raw, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return runtime.GOOS
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if v, ok := strings.CutPrefix(line, "PRETTY_NAME="); ok {
			return strings.Trim(v, `"`)
		}
	}
	return runtime.GOOS
}

func ramMB() int64 {
	raw, _ := os.ReadFile("/proc/meminfo")
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.HasPrefix(line, "MemTotal:") {
			var kb int64
			fmt.Sscan(strings.TrimSpace(strings.TrimSuffix(strings.TrimPrefix(line, "MemTotal:"), "kB")), &kb)
			return kb / 1024
		}
	}
	return 1
}

// Gather describes this machine. diskGB is the size of the disk that holds the hosting accounts (measured by the caller).
func Gather(capabilities []string, diskGB int64) Inventory {
	host, _ := os.Hostname()
	var ips []string
	addrs, _ := net.InterfaceAddrs()
	for _, a := range addrs {
		if ipnet, ok := a.(*net.IPNet); ok && ipnet.IP.IsGlobalUnicast() && !ipnet.IP.IsPrivate() && len(ips) < 16 {
			ips = append(ips, ipnet.IP.String())
		}
	}
	if len(ips) == 0 { // a node behind NAT still has to tell WHPanel something: its private addresses
		for _, a := range addrs {
			if ipnet, ok := a.(*net.IPNet); ok && ipnet.IP.IsGlobalUnicast() && len(ips) < 16 {
				ips = append(ips, ipnet.IP.String())
			}
		}
	}
	if diskGB < 1 {
		diskGB = 1
	}
	return Inventory{Hostname: host, IPAddresses: ips, OSName: osName(), CPUCores: runtime.NumCPU(), RAMMB: ramMB(), DiskGB: diskGB, Capabilities: capabilities}
}
