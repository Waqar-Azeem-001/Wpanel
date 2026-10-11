package handlers

import (
	"bytes"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"mime"
	"mime/multipart"
	"mime/quotedprintable"
	"net/mail"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"time"

	"whagent/internal/render"
)

// A MAIL node (ADR 06): Postfix receives and sends, Dovecot stores and serves, Rspamd filters and signs (DKIM). This file keeps each mail domain's state in a small file, renders the
// whole configuration from those states (Postfix maps, the Dovecot password file, Rspamd spam settings, per-mailbox Sieve auto-replies), reloads the services only after the maps are
// rebuilt, and reads and writes the mailboxes themselves as Maildir folders. Only the Maildir part works on real folders. The generated Postfix, Dovecot and Rspamd settings have never been
// run against the real programs.

const (
	dkimSelector = "whp"
	vmailUID     = 5000
)

var (
	mailFolders = map[string]string{"inbox": "", "drafts": ".Drafts", "sent": ".Sent", "archive": ".Archive", "spam": ".Junk", "trash": ".Trash"}
	folderOrder = []string{"inbox", "drafts", "sent", "archive", "spam", "trash"}
	spamActions = map[string][2]int{"low": {10, 20}, "medium": {6, 15}, "high": {4, 10}}
	mailDefault = map[string]int64{"max_mailboxes": 10, "max_forwarders": 10, "max_quota_mb": 5120, "emails_per_hour": 200}
	emailRe     = regexp.MustCompile(`^[a-z0-9][a-z0-9._+-]{0,63}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$`)
	nonSlug     = regexp.MustCompile(`[^a-z0-9]`)
)

// flagSep puts the flags in a Maildir file name. Windows cannot have a colon in a file name, so the stand-in used there (only for tests) is a semicolon.
func flagSep() string {
	if runtime.GOOS == "windows" {
		return ";2,"
	}
	return ":2,"
}

type mailBox struct {
	QuotaMB   int64      `json:"quota_mb"`
	Hash      string     `json:"hash"`
	Blocked   bool       `json:"blocked"`
	Sent      []float64  `json:"sent"`
	Autoreply *autoReply `json:"autoreply"`
	Profile   *profile   `json:"profile,omitempty"`
}

type autoReply struct {
	Subject string `json:"subject"`
	Body    string `json:"body"`
	Enabled bool   `json:"enabled"`
}

type profile struct {
	DisplayName    string `json:"display_name"`
	ReplyTo        string `json:"reply_to"`
	SignatureHTML  string `json:"signature_html"`
	SignatureText  string `json:"signature_text"`
	SignatureNew   bool   `json:"signature_new"`
	SignatureReply bool   `json:"signature_reply"`
}

type forward struct {
	Source      string `json:"source"`
	Destination string `json:"destination"`
}

type spamSettings struct {
	Enabled    bool   `json:"enabled"`
	Level      string `json:"level"`
	DeleteSpam bool   `json:"delete_spam"`
}

type mailDomain struct {
	Account    string              `json:"account"`
	Domain     string              `json:"domain"`
	Limits     map[string]int64    `json:"limits"`
	Routing    string              `json:"routing"`
	Spam       spamSettings        `json:"spam"`
	Mailboxes  map[string]*mailBox `json:"mailboxes"`
	Forwarders []forward           `json:"forwarders"`
	DKIMTxt    string              `json:"dkim_txt"`
}

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){
			"MAIL_DOMAIN_ADD": a.mailDomainAdd, "MAIL_DOMAIN_REMOVE": a.mailDomainRemove, "MAIL_LIST": a.mailList, "CREATE_MAILBOX": a.mailBoxCreate, "MAIL_BOX_DELETE": a.mailBoxDelete,
			"MAIL_BOX_PASSWORD": a.mailBoxPassword, "MAIL_BOX_QUOTA": a.mailBoxQuota, "MAIL_FORWARD_ADD": a.mailForwardAdd, "MAIL_FORWARD_DELETE": a.mailForwardDelete,
			"MAIL_AUTOREPLY_SET": a.mailAutoreplySet, "MAIL_AUTOREPLY_DELETE": a.mailAutoreplyDelete, "MAIL_SPAM_SET": a.mailSpamSet, "MAIL_ROUTING_SET": a.mailRoutingSet,
			"MAIL_FOLDERS": a.mailFoldersCmd, "MAIL_MESSAGES": a.mailMessages, "MAIL_READ": a.mailRead, "MAIL_MOVE": a.mailMove, "MAIL_TRASH": a.mailTrash, "MAIL_STAR": a.mailStar,
			"MAIL_DRAFT": a.mailDraft, "MAIL_PROFILE": a.mailProfile, "MAIL_PROFILE_SET": a.mailProfileSet, "MAIL_SEND": a.mailSend}
	})
}

func mailStatePath(domain string) string { return "/var/lib/whp/mail/" + domain + ".json" }

func (a *Agent) mailState(domain string) *mailDomain {
	raw, ok := a.Sys.Read(mailStatePath(domain))
	if !ok {
		return nil
	}
	var st mailDomain
	if json.Unmarshal([]byte(raw), &st) != nil {
		return nil
	}
	if st.Mailboxes == nil {
		st.Mailboxes = map[string]*mailBox{}
	}
	return &st
}

func (a *Agent) mailNeed(account, domain string) (*mailDomain, error) {
	st := a.mailState(domain)
	if st == nil || st.Account != account {
		return nil, herr("not_found", "Mail is not set up for that domain on this account.")
	}
	return st, nil
}

func (a *Agent) mailAll() []*mailDomain {
	var out []*mailDomain
	for _, path := range a.Sys.List("/var/lib/whp/mail/") {
		if strings.HasSuffix(path, ".json") {
			if st := a.mailState(strings.TrimSuffix(filepath.Base(path), ".json")); st != nil {
				out = append(out, st)
			}
		}
	}
	return out
}

func (a *Agent) maildir(domain, local string) string { return filepath.Join(a.MailRoot, domain, local) }

func sieve(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, `\`, `\\`), `"`, `\"`)
}

// ---- configuration -----------------------------------------------------------------------------------------------------------------------------

func (a *Agent) mailRender(states []*mailDomain) [][2]any {
	var domains, boxes, aliases, blocked, passwd, spam []string
	for _, st := range states {
		d := st.Domain
		if st.Routing == "local" {
			domains = append(domains, d+" OK")
		}
		locals := make([]string, 0, len(st.Mailboxes))
		for l := range st.Mailboxes {
			locals = append(locals, l)
		}
		sort.Strings(locals)
		for _, local := range locals {
			box := st.Mailboxes[local]
			if st.Routing == "local" {
				boxes = append(boxes, fmt.Sprintf("%s@%s %s/%s/", local, d, d, local))
			}
			quota := box.QuotaMB
			if quota == 0 {
				quota = st.Limits["max_quota_mb"]
			}
			passwd = append(passwd, fmt.Sprintf("%s@%s:%s:%d:%d::/var/mail/vhosts/%s/%s::userdb_quota_rule=*:storage=%dM", local, d, box.Hash, vmailUID, vmailUID, d, local, quota))
			if box.Blocked {
				blocked = append(blocked, fmt.Sprintf("%s@%s REJECT Sending from this address is blocked", local, d))
			}
		}
		var order []string
		targets := map[string][]string{}
		for _, fw := range st.Forwarders {
			if _, seen := targets[fw.Source]; !seen {
				order = append(order, fw.Source)
			}
			targets[fw.Source] = append(targets[fw.Source], fw.Destination)
		}
		sort.Strings(order)
		for _, source := range order {
			var keep []string
			if _, own := st.Mailboxes[strings.SplitN(source, "@", 2)[0]]; own {
				keep = []string{source} // a mailbox that is also forwarded keeps its own copy
			}
			aliases = append(aliases, source+" "+strings.Join(append(keep, targets[source]...), ","))
		}
		mark, reject := spamActions[st.Spam.Level][0], spamActions[st.Spam.Level][1]
		if st.Spam.DeleteSpam {
			reject = mark // no Spam folder: what would be marked is rejected
		}
		body := "want_spam = yes;"
		if st.Spam.Enabled {
			body = fmt.Sprintf(`apply { actions { "add header" = %d; "reject" = %d; } }`, mark, reject)
		}
		spam = append(spam, fmt.Sprintf(`whp_%s { priority = 10; rcpt = "@%s"; %s }`, nonSlug.ReplaceAllString(d, "_"), d, body))
	}
	return [][2]any{{"/etc/postfix/whp/virtual_domains", domains}, {"/etc/postfix/whp/virtual_mailboxes", boxes}, {"/etc/postfix/whp/virtual_aliases", aliases},
		{"/etc/postfix/whp/blocked_senders", blocked}, {"/etc/dovecot/whp/passwd", passwd}, {"/etc/rspamd/local.d/whp_spam.conf", spam}}
}

func (a *Agent) mailApply(states []*mailDomain) error {
	var changed []string
	for _, item := range a.mailRender(states) {
		path, lines := item[0].(string), item[1].([]string)
		text := "# managed by WH Agent - do not edit by hand\n" + strings.Join(lines, "\n")
		if len(lines) > 0 {
			text += "\n"
		}
		if current, _ := a.Sys.Read(path); current != text {
			if err := a.Sys.Write(path, text, 0o640, "root", ""); err != nil {
				return err
			}
			changed = append(changed, path)
		}
	}
	maps := 0
	for _, path := range changed {
		if strings.HasPrefix(path, "/etc/postfix/whp/") {
			if _, err := a.Sys.Run([]string{"postmap", path}, ""); err != nil {
				return err
			}
			maps++
		}
	}
	if maps > 0 {
		if _, err := a.Sys.Run([]string{"postfix", "reload"}, ""); err != nil {
			return err
		}
	}
	if contains(changed, "/etc/rspamd/local.d/whp_spam.conf") {
		if _, err := a.Sys.Run([]string{"systemctl", "reload", "rspamd"}, ""); err != nil {
			return err
		}
	}
	return nil
}

// mailCommit writes the new state of one domain and the configuration built from it; if the services refuse, the old configuration is put back.
func (a *Agent) mailCommit(st *mailDomain, old []*mailDomain) error {
	var others []*mailDomain
	for _, o := range old {
		if o.Domain != st.Domain {
			others = append(others, o)
		}
	}
	if err := a.mailApply(append(others, st)); err != nil {
		_ = a.mailApply(old)
		return err
	}
	raw, _ := json.Marshal(st)
	return a.Sys.Write(mailStatePath(st.Domain), string(raw), 0o600, "root", "")
}

func intLimits(p map[string]any) map[string]int64 {
	out := map[string]int64{}
	for k := range mailDefault {
		if v, ok := p[k].(int64); ok {
			out[k] = v
		}
	}
	return out
}

// ---- domains ---------------------------------------------------------------------------------------------------------------------------------------

func (a *Agent) mailDomainAdd(p map[string]any) (Result, error) {
	account, domain := str(p, "account"), str(p, "domain")
	all := a.mailAll()
	limits := intLimits(p)
	if st := a.mailState(domain); st != nil {
		if st.Account != account {
			return Result{}, herr("conflict", "That domain already has mail on this node for another account.")
		}
		for k, v := range limits {
			st.Limits[k] = v
		}
		if err := a.mailCommit(st, all); err != nil {
			return Result{}, err
		}
		return Result{"updated", map[string]any{"dkim_txt": st.DKIMTxt, "selector": dkimSelector}}, nil
	}
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return Result{}, err
	}
	der, _ := x509.MarshalPKIXPublicKey(&key.PublicKey)
	keyDER, _ := x509.MarshalPKCS8PrivateKey(key)
	st := &mailDomain{Account: account, Domain: domain, Limits: map[string]int64{}, Routing: "local", Spam: spamSettings{true, "medium", false}, Mailboxes: map[string]*mailBox{},
		DKIMTxt: "v=DKIM1; k=rsa; p=" + base64.StdEncoding.EncodeToString(der)}
	for k, v := range mailDefault {
		st.Limits[k] = v
	}
	for k, v := range limits {
		st.Limits[k] = v
	}
	keyPath := "/etc/rspamd/dkim/" + domain + "." + dkimSelector + ".key"
	if err := a.Sys.Write(keyPath, string(pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: keyDER})), 0o600, "_rspamd", ""); err != nil {
		return Result{}, err
	}
	if _, found := a.Sys.Read("/etc/rspamd/local.d/dkim_signing.conf"); !found {
		conf := "# managed by WH Agent\nselector = \"" + dkimSelector + "\";\npath = \"/etc/rspamd/dkim/$domain.$selector.key\";\nallow_username_mismatch = false;\nsign_authenticated = true;\n"
		if err := a.Sys.Write("/etc/rspamd/local.d/dkim_signing.conf", conf, 0o644, "root", ""); err != nil {
			return Result{}, err
		}
	}
	if err := a.mailCommit(st, all); err != nil {
		_ = a.Sys.Remove(keyPath)
		return Result{}, err
	}
	return Result{"created", map[string]any{"dkim_txt": st.DKIMTxt, "selector": dkimSelector}}, nil
}

func (a *Agent) mailDomainRemove(p map[string]any) (Result, error) {
	domain := str(p, "domain")
	st := a.mailState(domain)
	if st == nil {
		return Result{"absent", nil}, nil
	}
	if st.Account != str(p, "account") {
		return Result{}, herr("not_found", "Mail is not set up for that domain on this account.")
	}
	var rest []*mailDomain
	for _, o := range a.mailAll() {
		if o.Domain != domain {
			rest = append(rest, o)
		}
	}
	if err := a.mailApply(rest); err != nil {
		return Result{}, err
	}
	for _, path := range []string{mailStatePath(domain), "/etc/rspamd/dkim/" + domain + "." + dkimSelector + ".key", "/var/mail/vhosts/" + domain} {
		if err := a.Sys.Remove(path); err != nil {
			return Result{}, err
		}
	}
	return Result{"removed", nil}, nil
}

func (a *Agent) usedMB(domain, local string) float64 {
	var total int64
	_ = filepath.WalkDir(a.maildir(domain, local), func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if info, e := d.Info(); e == nil {
				total += info.Size()
			}
		}
		return nil
	})
	return float64(int64(float64(total)/1048576*100+0.5)) / 100
}

func (a *Agent) mailList(p map[string]any) (Result, error) {
	st, err := a.mailNeed(str(p, "account"), str(p, "domain"))
	if err != nil {
		return Result{}, err
	}
	locals := make([]string, 0, len(st.Mailboxes))
	for l := range st.Mailboxes {
		locals = append(locals, l)
	}
	sort.Strings(locals)
	boxes := []any{}
	for _, l := range locals {
		box := st.Mailboxes[l]
		var quota any
		if box.QuotaMB != 0 {
			quota = box.QuotaMB
		}
		var reply any
		if box.Autoreply != nil {
			reply = map[string]any{"subject": box.Autoreply.Subject, "body": box.Autoreply.Body, "enabled": box.Autoreply.Enabled}
		}
		boxes = append(boxes, map[string]any{"address": l + "@" + st.Domain, "quota_mb": quota, "used_mb": a.usedMB(st.Domain, l), "blocked": box.Blocked, "autoreply": reply})
	}
	fws := []any{}
	for _, f := range st.Forwarders {
		fws = append(fws, map[string]any{"source": f.Source, "destination": f.Destination})
	}
	return Result{"listed", map[string]any{"domain": st.Domain, "limits": limitsAsAny(st.Limits), "routing": st.Routing,
		"spam": map[string]any{"enabled": st.Spam.Enabled, "level": st.Spam.Level, "delete_spam": st.Spam.DeleteSpam}, "dkim_txt": st.DKIMTxt, "selector": dkimSelector, "mailboxes": boxes, "forwarders": fws}}, nil
}

// ---- mailboxes ------------------------------------------------------------------------------------------------------------------------------------

func (a *Agent) mailOwner(account, address string) (*mailDomain, string, error) {
	i := strings.LastIndex(address, "@")
	st, err := a.mailNeed(account, address[i+1:])
	return st, address[:i], err
}

func (a *Agent) mailBoxOf(p map[string]any) (*mailDomain, string, *mailBox, error) {
	st, local, err := a.mailOwner(str(p, "account"), str(p, "address"))
	if err != nil {
		return nil, "", nil, err
	}
	box, ok := st.Mailboxes[local]
	if !ok {
		return nil, "", nil, herr("not_found", "That mailbox does not exist.")
	}
	return st, local, box, nil
}

func (a *Agent) mailBoxCreate(p map[string]any) (Result, error) {
	st, err := a.mailNeed(str(p, "account"), str(p, "domain"))
	if err != nil {
		return Result{}, err
	}
	local := str(p, "local_part")
	if _, dup := st.Mailboxes[local]; dup {
		return Result{}, herr("exists", "That mailbox already exists.")
	}
	if int64(len(st.Mailboxes)) >= st.Limits["max_mailboxes"] {
		return Result{}, herr("limit_reached", "Your plan's mailbox limit has been reached.")
	}
	if num(p, "quota_mb") > st.Limits["max_quota_mb"] {
		return Result{}, herr("quota_too_big", "A mailbox can have at most %d MB.", st.Limits["max_quota_mb"])
	}
	all := a.mailAll()
	st.Mailboxes[local] = &mailBox{QuotaMB: num(p, "quota_mb"), Hash: HashPassword(str(p, "password")), Sent: []float64{}}
	if err := a.mailCommit(st, all); err != nil {
		return Result{}, err
	}
	for _, folder := range []string{"", ".Drafts", ".Sent", ".Archive", ".Junk", ".Trash"} {
		for _, sub := range []string{"cur", "new", "tmp"} {
			if err := os.MkdirAll(filepath.Join(a.maildir(st.Domain, local), folder, sub), 0o755); err != nil {
				return Result{}, err
			}
		}
	}
	if _, err := a.Sys.Run([]string{"chown", "-R", "5000:5000", a.maildir(st.Domain, local)}, ""); err != nil {
		return Result{}, err
	}
	return Result{"created", map[string]any{"address": local + "@" + st.Domain}}, nil
}

func (a *Agent) mailBoxDelete(p map[string]any) (Result, error) {
	st, local, err := a.mailOwner(str(p, "account"), str(p, "address"))
	if err != nil {
		return Result{}, err
	}
	if _, ok := st.Mailboxes[local]; !ok {
		return Result{"absent", nil}, nil
	}
	all := a.mailAll()
	delete(st.Mailboxes, local)
	var keep []forward
	for _, f := range st.Forwarders {
		if f.Source != str(p, "address") {
			keep = append(keep, f)
		}
	}
	st.Forwarders = keep
	if err := a.mailCommit(st, all); err != nil {
		return Result{}, err
	}
	_ = os.RemoveAll(a.maildir(st.Domain, local))
	return Result{"deleted", nil}, nil
}

func (a *Agent) mailBoxPassword(p map[string]any) (Result, error) {
	st, _, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	box.Hash = HashPassword(str(p, "password"))
	return Result{"changed", nil}, a.mailCommit(st, all)
}

func (a *Agent) mailBoxQuota(p map[string]any) (Result, error) {
	st, _, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	if num(p, "quota_mb") > st.Limits["max_quota_mb"] {
		return Result{}, herr("quota_too_big", "A mailbox can have at most %d MB.", st.Limits["max_quota_mb"])
	}
	all := a.mailAll()
	box.QuotaMB = num(p, "quota_mb")
	return Result{"changed", nil}, a.mailCommit(st, all)
}

func (a *Agent) mailForwardAdd(p map[string]any) (Result, error) {
	st, _, err := a.mailOwner(str(p, "account"), str(p, "source"))
	if err != nil {
		return Result{}, err
	}
	fw := forward{str(p, "source"), str(p, "destination")}
	for _, f := range st.Forwarders {
		if f == fw {
			return Result{"exists", nil}, nil
		}
	}
	if fw.Source == fw.Destination {
		return Result{}, herr("invalid", "An address cannot be forwarded to itself.")
	}
	if int64(len(st.Forwarders)) >= st.Limits["max_forwarders"] {
		return Result{}, herr("limit_reached", "Your plan's forwarder limit has been reached.")
	}
	all := a.mailAll()
	st.Forwarders = append(st.Forwarders, fw)
	return Result{"created", nil}, a.mailCommit(st, all)
}

func (a *Agent) mailForwardDelete(p map[string]any) (Result, error) {
	st, _, err := a.mailOwner(str(p, "account"), str(p, "source"))
	if err != nil {
		return Result{}, err
	}
	fw := forward{str(p, "source"), str(p, "destination")}
	idx := -1
	for i, f := range st.Forwarders {
		if f == fw {
			idx = i
		}
	}
	if idx < 0 {
		return Result{"absent", nil}, nil
	}
	all := a.mailAll()
	st.Forwarders = append(st.Forwarders[:idx], st.Forwarders[idx+1:]...)
	return Result{"deleted", nil}, a.mailCommit(st, all)
}

func (a *Agent) writeSieve(domain, local string, reply *autoReply) error {
	path := "/var/mail/vhosts/" + domain + "/" + local + "/.dovecot.sieve"
	if reply == nil || !reply.Enabled {
		return a.Sys.Remove(path)
	}
	return a.Sys.Write(path, fmt.Sprintf("require [\"vacation\"];\nvacation :days 1 :subject \"%s\" \"%s\";\n", sieve(reply.Subject), sieve(reply.Body)), 0o640, "root", "")
}

func (a *Agent) mailAutoreplySet(p map[string]any) (Result, error) {
	st, local, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	box.Autoreply = &autoReply{str(p, "subject"), str(p, "body"), str(p, "enabled") == "yes"}
	if err := a.mailCommit(st, all); err != nil {
		return Result{}, err
	}
	return Result{"set", nil}, a.writeSieve(st.Domain, local, box.Autoreply)
}

func (a *Agent) mailAutoreplyDelete(p map[string]any) (Result, error) {
	st, local, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	if box.Autoreply == nil {
		return Result{"absent", nil}, nil
	}
	all := a.mailAll()
	box.Autoreply = nil
	if err := a.mailCommit(st, all); err != nil {
		return Result{}, err
	}
	return Result{"deleted", nil}, a.writeSieve(st.Domain, local, nil)
}

func (a *Agent) mailSpamSet(p map[string]any) (Result, error) {
	st, err := a.mailNeed(str(p, "account"), str(p, "domain"))
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	st.Spam = spamSettings{str(p, "enabled") == "yes", str(p, "level"), str(p, "delete_spam") == "yes"}
	return Result{"set", nil}, a.mailCommit(st, all)
}

func (a *Agent) mailRoutingSet(p map[string]any) (Result, error) {
	st, err := a.mailNeed(str(p, "account"), str(p, "domain"))
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	st.Routing = str(p, "mode")
	return Result{"set", nil}, a.mailCommit(st, all)
}

// ScanLogs is the hourly abuse check, run by the agent (not a command): it counts recipients per sender in Postfix's queue-manager log lines and blocks any mailbox over its domain's
// hourly limit. It returns the addresses newly blocked.
func (a *Agent) ScanLogs(lines []string) []string {
	re := regexp.MustCompile(`postfix/qmgr\[\d+\]: [0-9A-F]+: from=<([^>]+)>, size=\d+, nrcpt=(\d+)`)
	counts := map[string]int64{}
	for _, line := range lines {
		if m := re.FindStringSubmatch(line); m != nil {
			var n int64
			fmt.Sscan(m[2], &n)
			counts[m[1]] += n
		}
	}
	blocked := []string{}
	all := a.mailAll()
	for _, st := range all {
		changed := false
		locals := make([]string, 0, len(st.Mailboxes))
		for l := range st.Mailboxes {
			locals = append(locals, l)
		}
		sort.Strings(locals)
		for _, local := range locals {
			box, address := st.Mailboxes[local], local+"@"+st.Domain
			if counts[address] > st.Limits["emails_per_hour"] && !box.Blocked {
				box.Blocked, changed = true, true
				blocked = append(blocked, address)
			}
		}
		if changed {
			_ = a.mailCommit(st, all)
		}
	}
	return blocked
}

func (a *Agent) mayMailSend(st *mailDomain, box *mailBox) error {
	horizon := float64(a.now().Unix()) - 3600
	kept := []float64{}
	for _, t := range box.Sent {
		if t > horizon {
			kept = append(kept, t)
		}
	}
	box.Sent = kept
	if box.Blocked {
		return herr("blocked", "Sending from this address has been blocked because of unusual activity. Contact support.")
	}
	if int64(len(box.Sent)) >= st.Limits["emails_per_hour"] {
		return herr("rate_limited", "You can send %d messages an hour. Try again later.", st.Limits["emails_per_hour"])
	}
	return nil
}

// ---- Maildir ---------------------------------------------------------------------------------------------------------------------------------------

// Deliver puts a message into a mailbox (what Dovecot's delivery does) and returns its id.
func (a *Agent) Deliver(domain, local string, raw []byte, folder string, seen bool) (string, error) {
	base := filepath.Join(a.maildir(domain, local), mailFolders[folder])
	for _, sub := range []string{"cur", "new", "tmp"} {
		if err := os.MkdirAll(filepath.Join(base, sub), 0o755); err != nil {
			return "", err
		}
	}
	id := fmt.Sprintf("%d.%s.mail", a.now().Unix(), randomHex(6))
	dir, name := "new", id
	if seen {
		dir, name = "cur", id+flagSep()+"S"
	}
	if err := os.WriteFile(filepath.Join(base, dir, name), raw, 0o644); err != nil {
		return "", err
	}
	// the agent runs as root; Dovecot runs as the mail user and must be able to write here
	if _, err := a.Sys.Run([]string{"chown", "-R", "5000:5000", a.maildir(domain, local)}, ""); err != nil {
		return "", err
	}
	return id, nil
}

type mailFile struct{ folder, path string }

func (a *Agent) findMail(domain, local, id string) (mailFile, error) {
	root := a.maildir(domain, local)
	for _, folder := range folderOrder {
		for _, sub := range []string{"new", "cur"} {
			dir := filepath.Join(root, mailFolders[folder], sub)
			items, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, it := range items {
				if strings.SplitN(it.Name(), flagSep(), 2)[0] == id {
					return mailFile{folder, filepath.Join(dir, it.Name())}, nil
				}
			}
		}
	}
	return mailFile{}, herr("not_found", "That message no longer exists.")
}

func flagsOf(path string) string {
	parts := strings.SplitN(filepath.Base(path), flagSep(), 2)
	if len(parts) == 2 {
		return parts[1]
	}
	return ""
}

func sortedFlags(flags string) string {
	seen := map[rune]bool{}
	var out []string
	for _, r := range flags {
		if !seen[r] {
			seen[r] = true
			out = append(out, string(r))
		}
	}
	sort.Strings(out)
	return strings.Join(out, "")
}

func setFlags(path, flags string) (string, error) {
	base := strings.SplitN(filepath.Base(path), flagSep(), 2)[0]
	folder := filepath.Dir(filepath.Dir(path))
	target := filepath.Join(folder, "cur", base+flagSep()+sortedFlags(flags))
	return target, os.Rename(path, target)
}

type parsed struct {
	From, Subject, To, Plain, HTML string
}

func decodeHeader(v string) string {
	out, err := (&mime.WordDecoder{}).DecodeHeader(v)
	if err != nil {
		return v
	}
	return out
}

func decodeBody(r io.Reader, encoding string) string {
	switch strings.ToLower(strings.TrimSpace(encoding)) {
	case "quoted-printable":
		r = quotedprintable.NewReader(r)
	case "base64":
		r = base64.NewDecoder(base64.StdEncoding, &stripSpace{r})
	}
	raw, _ := io.ReadAll(r)
	return string(raw)
}

type stripSpace struct{ r io.Reader }

func (s *stripSpace) Read(p []byte) (int, error) {
	n, err := s.r.Read(p)
	out := p[:0]
	for _, b := range p[:n] {
		if b != '\n' && b != '\r' && b != ' ' {
			out = append(out, b)
		}
	}
	return len(out), err
}

func parseMessage(raw []byte) parsed {
	msg, err := mail.ReadMessage(bytes.NewReader(raw))
	if err != nil {
		return parsed{}
	}
	out := parsed{From: decodeHeader(msg.Header.Get("From")), Subject: decodeHeader(msg.Header.Get("Subject")), To: decodeHeader(msg.Header.Get("To"))}
	var walk func(header map[string][]string, body io.Reader)
	walk = func(header map[string][]string, body io.Reader) {
		ct, params, _ := mime.ParseMediaType(firstOf(header, "Content-Type"))
		encoding := firstOf(header, "Content-Transfer-Encoding")
		switch {
		case strings.HasPrefix(ct, "multipart/"):
			mr := multipart.NewReader(body, params["boundary"])
			for {
				part, err := mr.NextPart()
				if err != nil {
					return
				}
				if strings.Contains(strings.ToLower(part.Header.Get("Content-Disposition")), "attachment") {
					continue
				}
				walk(part.Header, part)
			}
		case ct == "text/html":
			if out.HTML == "" {
				out.HTML = decodeBody(body, encoding)
			}
		case ct == "text/plain" || ct == "":
			if out.Plain == "" {
				out.Plain = decodeBody(body, encoding)
			}
		}
	}
	walk(msg.Header, msg.Body)
	return out
}

func firstOf(h map[string][]string, key string) string {
	for k, v := range h {
		if strings.EqualFold(k, key) && len(v) > 0 {
			return v[0]
		}
	}
	return ""
}

func (a *Agent) mailInfo(f mailFile, withBody bool) (map[string]any, error) {
	raw, err := os.ReadFile(f.path)
	if err != nil {
		return nil, err
	}
	info, _ := os.Stat(f.path)
	pm := parseMessage(raw)
	flags := flagsOf(f.path)
	snippet := strings.Join(strings.Fields(pm.Plain), " ")
	if r := []rune(snippet); len(r) > 140 {
		snippet = string(r[:140])
	}
	out := map[string]any{"message_id": strings.SplitN(filepath.Base(f.path), flagSep(), 2)[0], "sender": pm.From, "subject": pm.Subject, "received_at": info.ModTime().UTC().Format("2006-01-02T15:04:05") + "+00:00",
		"unread": !strings.Contains(flags, "S"), "to": pm.To, "folder": f.folder, "snippet": snippet, "starred": strings.Contains(flags, "F")}
	if withBody {
		out["body"], out["html"] = pm.Plain, pm.HTML
	}
	return out, nil
}

func (a *Agent) walkMail(domain, local, folder string) []mailFile {
	root := a.maildir(domain, local)
	var out []mailFile
	names := []string{folder}
	if folder == "starred" {
		names = folderOrder
	}
	for _, name := range names {
		for _, sub := range []string{"new", "cur"} {
			dir := filepath.Join(root, mailFolders[name], sub)
			items, err := os.ReadDir(dir)
			if err != nil {
				continue
			}
			for _, it := range items {
				path := filepath.Join(dir, it.Name())
				if folder != "starred" || strings.Contains(flagsOf(path), "F") {
					out = append(out, mailFile{name, path})
				}
			}
		}
	}
	return out
}

func (a *Agent) mailFoldersCmd(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	out := map[string]any{}
	for _, name := range append(append([]string{}, folderOrder...), "starred") {
		files := a.walkMail(st.Domain, local, name)
		unread := 0
		for _, f := range files {
			if !strings.Contains(flagsOf(f.path), "S") {
				unread++
			}
		}
		out[name] = []any{len(files), unread}
	}
	return Result{"listed", map[string]any{"folders": out}}, nil
}

func (a *Agent) mailMessages(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	rows := []map[string]any{}
	for _, f := range a.walkMail(st.Domain, local, str(p, "folder")) {
		if info, e := a.mailInfo(f, false); e == nil {
			rows = append(rows, info)
		}
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i]["received_at"].(string) > rows[j]["received_at"].(string) })
	if len(rows) > 200 {
		rows = rows[:200]
	}
	out := make([]any, len(rows))
	for i, r := range rows {
		out[i] = r
	}
	return Result{"listed", map[string]any{"messages": out}}, nil
}

func (a *Agent) mailRead(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	f, err := a.findMail(st.Domain, local, str(p, "message_id"))
	if err != nil {
		return Result{}, err
	}
	info, err := a.mailInfo(f, true)
	if err != nil {
		return Result{}, err
	}
	if info["unread"].(bool) {
		if _, err := setFlags(f.path, flagsOf(f.path)+"S"); err != nil {
			return Result{}, err
		}
		info["unread"] = false
	}
	return Result{"read", info}, nil
}

func (a *Agent) moveMail(st *mailDomain, local string, f mailFile, id, folder string) error {
	target := filepath.Join(a.maildir(st.Domain, local), mailFolders[folder])
	for _, sub := range []string{"cur", "new", "tmp"} {
		if err := os.MkdirAll(filepath.Join(target, sub), 0o755); err != nil {
			return err
		}
	}
	return os.Rename(f.path, filepath.Join(target, "cur", id+flagSep()+sortedFlags(flagsOf(f.path))))
}

func (a *Agent) mailMove(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	f, err := a.findMail(st.Domain, local, str(p, "message_id"))
	if err != nil {
		return Result{}, err
	}
	return Result{"moved", nil}, a.moveMail(st, local, f, str(p, "message_id"), str(p, "folder"))
}

func (a *Agent) mailTrash(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	f, err := a.findMail(st.Domain, local, str(p, "message_id"))
	if err != nil {
		return Result{}, err
	}
	if f.folder == "trash" || f.folder == "drafts" {
		return Result{"deleted", nil}, os.Remove(f.path)
	}
	return Result{"moved", nil}, a.moveMail(st, local, f, str(p, "message_id"), "trash")
}

func (a *Agent) mailStar(p map[string]any) (Result, error) {
	st, local, _, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	f, err := a.findMail(st.Domain, local, str(p, "message_id"))
	if err != nil {
		return Result{}, err
	}
	flags := flagsOf(f.path)
	if str(p, "starred") == "yes" {
		flags += "F"
	} else {
		flags = strings.ReplaceAll(flags, "F", "")
	}
	_, err = setFlags(f.path, flags)
	return Result{"set", nil}, err
}

func (a *Agent) mailProfile(p map[string]any) (Result, error) {
	_, _, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	pr := profile{SignatureNew: true, SignatureReply: true}
	if box.Profile != nil {
		pr = *box.Profile
	}
	return Result{"read", map[string]any{"profile": map[string]any{"display_name": pr.DisplayName, "reply_to": pr.ReplyTo, "signature_html": pr.SignatureHTML, "signature_text": pr.SignatureText,
		"signature_new": pr.SignatureNew, "signature_reply": pr.SignatureReply}}}, nil
}

func (a *Agent) mailProfileSet(p map[string]any) (Result, error) {
	st, _, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	box.Profile = &profile{str(p, "display_name"), str(p, "reply_to"), str(p, "signature_html"), str(p, "signature_text"), str(p, "signature_new") == "yes", str(p, "signature_reply") == "yes"}
	return Result{"set", nil}, a.mailCommit(st, all)
}

func (a *Agent) compose(st *mailDomain, box *mailBox, p map[string]any, strict bool) (string, []string, error) {
	var recipients []string
	for _, r := range strings.Split(str(p, "to"), ",") {
		if r = strings.TrimSpace(r); r != "" {
			recipients = append(recipients, r)
		}
	}
	if strict && len(recipients) == 0 {
		return "", nil, herr("invalid", "Add at least one recipient.")
	}
	for _, r := range recipients {
		if !emailRe.MatchString(strings.ToLower(r)) {
			return "", nil, herr("invalid", "%s is not a valid email address.", clip(r, 60))
		}
	}
	pr := profile{}
	if box.Profile != nil {
		pr = *box.Profile
	}
	one := func(s string) string { return strings.NewReplacer("\r", " ", "\n", " ").Replace(s) }
	from := str(p, "address")
	if pr.DisplayName != "" {
		from = fmt.Sprintf("%s <%s>", mime.QEncoding.Encode("utf-8", strings.ReplaceAll(pr.DisplayName, `"`, "")), str(p, "address"))
	}
	var h strings.Builder
	h.WriteString("From: " + from + "\r\n")
	if len(recipients) > 0 {
		h.WriteString("To: " + strings.Join(recipients, ", ") + "\r\n")
	}
	h.WriteString("Subject: " + mime.QEncoding.Encode("utf-8", one(str(p, "subject"))) + "\r\n")
	h.WriteString("Date: " + a.now().UTC().Format(time.RFC1123Z) + "\r\n")
	h.WriteString("Message-ID: <" + randomHex(12) + "@" + st.Domain + ">\r\n")
	if pr.ReplyTo != "" {
		h.WriteString("Reply-To: " + one(pr.ReplyTo) + "\r\n")
	}
	h.WriteString("MIME-Version: 1.0\r\n")
	body := str(p, "body")
	if body == "" {
		body = " "
	}
	if html := str(p, "html"); html != "" {
		boundary := "whp" + randomHex(12)
		h.WriteString("Content-Type: multipart/alternative; boundary=\"" + boundary + "\"\r\n\r\n")
		h.WriteString("--" + boundary + "\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n" + wrap64(body) + "\r\n")
		h.WriteString("--" + boundary + "\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n" + wrap64(html) + "\r\n--" + boundary + "--\r\n")
	} else {
		h.WriteString("Content-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: base64\r\n\r\n" + wrap64(body) + "\r\n")
	}
	return h.String(), recipients, nil
}

func wrap64(s string) string {
	enc := base64.StdEncoding.EncodeToString([]byte(s))
	var out []string
	for len(enc) > 76 {
		out = append(out, enc[:76])
		enc = enc[76:]
	}
	return strings.Join(append(out, enc), "\r\n")
}

func (a *Agent) dropDraft(st *mailDomain, local, id string) {
	if id == "" {
		return
	}
	if f, err := a.findMail(st.Domain, local, id); err == nil {
		_ = os.Remove(f.path)
	}
}

func (a *Agent) mailDraft(p map[string]any) (Result, error) {
	st, local, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	text, _, err := a.compose(st, box, p, false)
	if err != nil {
		return Result{}, err
	}
	a.dropDraft(st, local, str(p, "draft_id"))
	id, err := a.Deliver(st.Domain, local, []byte(text), "drafts", true)
	return Result{"saved", map[string]any{"draft_id": id}}, err
}

func (a *Agent) mailSend(p map[string]any) (Result, error) {
	st, local, box, err := a.mailBoxOf(p)
	if err != nil {
		return Result{}, err
	}
	if err := a.mayMailSend(st, box); err != nil {
		return Result{}, err
	}
	text, recipients, err := a.compose(st, box, p, true)
	if err != nil {
		return Result{}, err
	}
	all := a.mailAll()
	argv := append([]string{"sendmail", "-i", "-f", str(p, "address"), "--"}, recipients...) // an argument list; every recipient was validated above
	if _, err := a.Sys.Run(argv, text); err != nil {
		return Result{}, err
	}
	now := float64(a.now().Unix())
	for range recipients {
		box.Sent = append(box.Sent, now)
	}
	if err := a.mailCommit(st, all); err != nil {
		return Result{}, err
	}
	if _, err := a.Deliver(st.Domain, local, []byte(text), "sent", true); err != nil {
		return Result{}, err
	}
	a.dropDraft(st, local, str(p, "draft_id"))
	return Result{"sent", map[string]any{"recipients": len(recipients)}}, nil
}

var _ = render.Defaults
