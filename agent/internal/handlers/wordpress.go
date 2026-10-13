package handlers

import (
	"crypto/rand"
	"crypto/sha1"
	"crypto/sha512"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// Installing WordPress on a node (the "Create website" step). The Go twin of apps/agent/node/wordpress.py: it does what a person would do by hand so the customer only names the site and its
// administrator. The website folder must hold only the placeholder page; the database and its user are made through the same handlers the Databases screen uses; the downloads are checked
// against the checksums wordpress.org publishes before anything is unpacked; wp-config.php gets fresh keys; WP-CLI finishes the installation as the account's own user with the password on
// standard input; and a failure puts the account back as it was. Never run on a real node.

const (
	wpScheme    = "https"
	wpArchive   = wpScheme + "://wordpress.org/latest.tar.gz"
	wpChecksum  = wpArchive + ".sha1"
	wpCLIPhar   = wpScheme + "://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
	wpCLISum    = wpCLIPhar + ".sha512"
	wpMaxBytes  = 120 << 20
	wpHolder    = "index.html"
	wpRunLimit  = 900
	wpTokenText = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
)

var (
	wpSaltNames   = []string{"AUTH_KEY", "SECURE_AUTH_KEY", "LOGGED_IN_KEY", "NONCE_KEY", "AUTH_SALT", "SECURE_AUTH_SALT", "LOGGED_IN_SALT", "NONCE_SALT"}
	wpVersionRe   = regexp.MustCompile(`\$wp_version\s*=\s*'([^']+)'`)
	wpDBPrivilege = []string{"select", "insert", "update", "delete", "create", "alter", "drop", "index"}
)

const wpConfig = `<?php
// managed by WH Agent when WordPress was installed. Keep this file private.
define( 'DB_NAME', '%s' );
define( 'DB_USER', '%s' );
define( 'DB_PASSWORD', '%s' );
define( 'DB_HOST', 'localhost' );
define( 'DB_CHARSET', 'utf8mb4' );
define( 'DB_COLLATE', '' );

%s

$table_prefix = 'wp_';
define( 'WP_DEBUG', false );
define( 'FS_METHOD', 'direct' );

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}
require_once ABSPATH . 'wp-settings.php';
`

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){"INSTALL_WORDPRESS": a.installWordPress}
	})
}

// randomToken is a random string of letters and digits (database passwords, keys and salts).
func randomToken(n int) string {
	raw := make([]byte, n)
	_, _ = rand.Read(raw)
	out := make([]byte, n)
	for i, b := range raw {
		out[i] = wpTokenText[int(b)%len(wpTokenText)]
	}
	return string(out)
}

// httpFetch is one HTTPS download, size-limited. Only the fixed addresses above are ever asked for.
func httpFetch(url string) ([]byte, error) {
	client := &http.Client{Timeout: 120 * time.Second}
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "wh-agent")
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("status %d", resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, wpMaxBytes+1))
	if err != nil {
		return nil, err
	}
	if len(data) > wpMaxBytes {
		return nil, fmt.Errorf("download too large")
	}
	return data, nil
}

func (a *Agent) wpVerified(url, sumURL, algorithm string) ([]byte, error) {
	failed := herr("download_failed", "Could not download WordPress. Try again in a few minutes.")
	data, err := a.Fetch(url)
	if err != nil {
		return nil, failed
	}
	sum, err := a.Fetch(sumURL)
	if err != nil {
		return nil, failed
	}
	wanted := ""
	if f := strings.Fields(string(sum)); len(f) > 0 {
		wanted = strings.ToLower(f[0])
	}
	got := ""
	if algorithm == "sha1" {
		h := sha1.Sum(data)
		got = hex.EncodeToString(h[:])
	} else {
		h := sha512.Sum512(data)
		got = hex.EncodeToString(h[:])
	}
	if wanted == "" || got != wanted {
		return nil, herr("checksum_mismatch", "A download did not match its published checksum, so nothing was installed.")
	}
	return data, nil
}

func (a *Agent) wpCLI() (string, error) {
	path := filepath.Join(a.ToolsDir, "wp-cli.phar")
	if _, err := os.Stat(path); err != nil {
		data, err := a.wpVerified(wpCLIPhar, wpCLISum, "sha512")
		if err != nil {
			return "", err
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return "", err
		}
		if err := os.WriteFile(path, data, 0o755); err != nil {
			return "", err
		}
	}
	return path, nil
}

func wpSSLOn(st State) bool {
	switch v := st["ssl"].(type) {
	case nil:
		return false
	case map[string]any:
		return len(v) > 0
	case bool:
		return v
	case string:
		return v != ""
	}
	return true
}

func wpAdminURL(st State, domain string) string {
	scheme := "http"
	if wpSSLOn(st) {
		scheme = "https"
	}
	return scheme + "://" + domain + "/wp-admin/"
}

func wpVersion(docroot string) string {
	raw, err := os.ReadFile(filepath.Join(docroot, "wp-includes", "version.php"))
	if err != nil {
		return ""
	}
	if m := wpVersionRe.FindStringSubmatch(string(raw)); m != nil {
		return m[1]
	}
	return ""
}

func (a *Agent) wpUndo(account, name string, madeDB, madeUser bool, docroot string) {
	if madeUser {
		_, _ = a.dbDeleteUser(map[string]any{"account": account, "name": name})
	}
	if madeDB {
		_, _ = a.dbDelete(map[string]any{"account": account, "name": name})
	}
	if entries, err := os.ReadDir(docroot); err == nil {
		for _, e := range entries {
			_ = os.RemoveAll(filepath.Join(docroot, e.Name()))
		}
	}
	_ = os.WriteFile(filepath.Join(docroot, wpHolder), []byte("<!doctype html><title>Welcome</title>"), 0o644)
}

func (a *Agent) installWordPress(p map[string]any) (Result, error) {
	account, domain := str(p, "account"), str(p, "domain")
	st := a.stateRead(pathsOfState(account))
	if st == nil || st.text("domain") != domain {
		return Result{}, herr("not_found", "No such website on this node.")
	}
	if st.flag("suspended") {
		return Result{}, herr("suspended", "This website is suspended.")
	}
	root := ""
	if a.RootFor != nil {
		root = a.RootFor(account)
	}
	if root == "" {
		return Result{}, herr("not_found", "No such website on this node.")
	}
	docroot := filepath.Join(root, domain, "public_html")
	if _, err := os.Stat(filepath.Join(docroot, "wp-config.php")); err == nil {
		return Result{"exists", map[string]any{"admin_url": wpAdminURL(st, domain), "version": wpVersion(docroot)}}, nil
	}
	if entries, err := os.ReadDir(docroot); err == nil {
		for _, e := range entries {
			if e.Name() != wpHolder {
				return Result{}, herr("not_empty", "The website folder already holds files, so WordPress was not installed over them.")
			}
		}
	}

	// download and check first: nothing is changed on the machine until both downloads are known to be good
	archiveBytes, err := a.wpVerified(wpArchive, wpChecksum, "sha1")
	if err != nil {
		return Result{}, err
	}
	cli, err := a.wpCLI()
	if err != nil {
		return Result{}, err
	}

	existing, err := a.Engine.Databases(account + "_")
	if err != nil {
		return Result{}, err
	}
	name := ""
	for _, suffix := range []string{"wp", "wp2", "wp3", "wp4", "wp5"} {
		if !contains(existing, account+"_"+suffix) {
			name = account + "_" + suffix
			break
		}
	}
	if name == "" {
		return Result{}, herr("limit_reached", "There are already several WordPress databases on this account.")
	}
	password := a.Token(24)
	stage := filepath.Join(a.StageRoot, "wp-"+a.Token(8))
	madeDB, madeUser := false, false
	fail := func(e error) (Result, error) {
		a.wpUndo(account, name, madeDB, madeUser, docroot)
		_ = os.RemoveAll(stage)
		return Result{}, e
	}
	if err := os.MkdirAll(stage, 0o755); err != nil {
		return fail(err)
	}
	if _, err := a.dbCreate(map[string]any{"account": account, "name": name}); err != nil {
		return fail(err)
	}
	madeDB = true
	if _, err := a.dbCreateUser(map[string]any{"account": account, "name": name, "password": password}); err != nil {
		return fail(err)
	}
	madeUser = true
	if _, err := a.dbGrant(map[string]any{"account": account, "database": name, "user": name, "privileges": wpDBPrivilege}); err != nil {
		return fail(err)
	}

	tarPath := filepath.Join(stage, "wordpress.tar.gz")
	if err := os.WriteFile(tarPath, archiveBytes, 0o600); err != nil {
		return fail(err)
	}
	ar, err := openArchive(tarPath)
	if err != nil {
		return fail(err)
	}
	_ = os.Remove(filepath.Join(docroot, wpHolder))
	if _, _, err := ar.extract("", docroot, false, nil); err != nil {
		return fail(err)
	}

	var salts []string
	for _, key := range wpSaltNames {
		salts = append(salts, fmt.Sprintf("define( '%s', '%s' );", key, a.Token(64)))
	}
	config := filepath.Join(docroot, "wp-config.php")
	if err := os.WriteFile(config, []byte(fmt.Sprintf(wpConfig, name, name, password, strings.Join(salts, "\n"))), 0o640); err != nil {
		return fail(err)
	}
	site := filepath.Join(root, domain)
	for _, argv := range [][]string{{"chown", "-R", account + ":apache", site}, {"chmod", "-R", "u+rwX,g+rX,o-rwx", site}, {"chmod", "0640", config}} {
		if _, err := a.Sys.Run(argv, ""); err != nil {
			return fail(err)
		}
	}
	scheme := "http"
	if wpSSLOn(st) {
		scheme = "https"
	}
	argv := []string{"systemd-run", "--quiet", "--collect", "--wait", "--pipe", "--uid", account, "--gid", account, "--slice", "whp-" + account + ".slice",
		"--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes", "--property=BindPaths=/home/" + account, fmt.Sprintf("--property=RuntimeMaxSec=%d", wpRunLimit),
		fmt.Sprintf("/opt/remi/php%s/root/usr/bin/php", strings.ReplaceAll(st.text("php_version"), ".", "")), cli, "core", "install", "--path=" + docroot,
		"--url=" + scheme + "://" + domain, "--title=" + str(p, "site_title"), "--admin_user=" + str(p, "admin_user"), "--admin_email=" + str(p, "admin_email"),
		"--prompt=admin_password", "--skip-email"}
	if _, err := a.Sys.Run(argv, str(p, "admin_password")+"\n"); err != nil {
		return fail(err)
	}
	_ = os.RemoveAll(stage)
	return Result{"installed", map[string]any{"admin_url": wpAdminURL(st, domain), "version": wpVersion(docroot), "database": name}}, nil
}
