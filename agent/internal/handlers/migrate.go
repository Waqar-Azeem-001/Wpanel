package handlers

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// Taking over a cPanel account (M2, ADR 19). The Go twin of apps/agent/node/cpanel_import.py: it reads the account's own pkgacct backup (cpmove-<user>.tar.gz), says what is in it and what
// cannot move by itself, and copies files, databases and mailboxes into the account WHPanel has already made on this node.
//
// The archive is never trusted: every member path is checked, links are skipped, nothing is executed, SQL only reaches this account's own databases, a database that exists is never
// replaced, and a file that exists is kept unless "replace" is asked for. It has run only against a pretend machine and a synthetic archive built from the documented layout; a real archive
// from a real cPanel server has NOT been tried.

const (
	migrationDir = "migrations"
	maxMembers   = 2_000_000
)

var (
	skipTop = map[string]bool{".cpanel": true, ".cagefs": true, ".trash": true, ".cphorde": true, ".softaculous": true, ".cpan": true, ".spamassassin": true, ".razor": true, ".contactemail": true,
		".lastlogin": true, "logs": true, "tmp": true, "access-logs": true, "mail": true, "etc": true, "ssl": true, "perl5": true, "lscache": true, ".bash_history": true, ".bash_logout": true,
		".bash_profile": true, ".bashrc": true, ".cl.selector": true, ".htpasswds": true}
	phpMap     = map[string]string{"ea-php74": "8.1", "ea-php80": "8.1", "ea-php81": "8.1", "ea-php82": "8.2", "ea-php83": "8.3", "ea-php84": "8.4"}
	grantRe    = regexp.MustCompile("(?i)GRANT\\s+(.+?)\\s+ON\\s+`?([^`.]+?)`?\\.\\*\\s+TO\\s+'([^']+)'@'([^']*)'")
	usageRe    = regexp.MustCompile(`(?i)GRANT\s+USAGE\s+ON\s+\*\.\*\s+TO\s+'([^']+)'@'[^']*'\s+IDENTIFIED BY PASSWORD '(\*[0-9A-Fa-f]{40})'`)
	mapPriv    = map[string]string{"SELECT": "select", "INSERT": "insert", "UPDATE": "update", "DELETE": "delete", "CREATE": "create", "ALTER": "alter", "DROP": "drop", "INDEX": "index"}
	archiveRe  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,100}\.tar\.gz$`)
	createRe   = regexp.MustCompile("(?s)CREATE TABLE `(\\w+)` \\((.*?)\\n\\)[^;]*;")
	columnRe   = regexp.MustCompile("^`(\\w+)` (\\S+)(.*)")
	insertRe   = regexp.MustCompile("(?s)INSERT INTO `(\\w+)` VALUES (.*?);\\n")
	intRe      = regexp.MustCompile(`^-?\d+$`)
	floatRe    = regexp.MustCompile(`^-?\d+\.\d+$`)
	allPrivs   = []string{"alter", "create", "delete", "drop", "index", "insert", "select", "update"}
	_          = archiveRe
	mailFolder = []string{"", ".Drafts", ".Sent", ".Archive", ".Junk", ".Trash"}
)

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){
			"MIGRATE_ANALYZE": a.migrateAnalyze, "MIGRATE_IMPORT": a.migrateImport, "MIGRATE_VALIDATE": a.migrateValidate, "MIGRATE_DISCARD": a.migrateDiscard}
	})
}

// --- reading the archive -------------------------------------------------------------------------------------------------------------------

func cleanMember(name string) (string, error) {
	unsafe := herr("unsafe_archive", "The archive holds a path that leads outside itself.")
	slashed := strings.ReplaceAll(name, "\\", "/")
	var parts []string
	for _, p := range strings.Split(slashed, "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." {
			return "", unsafe
		}
	}
	if strings.HasPrefix(name, "/") || (len(parts) > 0 && strings.Contains(parts[0], ":")) {
		return "", unsafe
	}
	if len(parts) > 1 {
		return strings.Join(parts[1:], "/"), nil
	}
	return "", nil
}

type archive struct {
	path         string
	files        map[string]int64
	order        []string
	skippedLinks int
}

func openTar(path string) (*tar.Reader, func(), error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	gz, err := gzip.NewReader(f)
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	return tar.NewReader(gz), func() { gz.Close(); f.Close() }, nil
}

func openArchive(path string) (*archive, error) {
	if info, err := os.Stat(path); err != nil || info.IsDir() {
		return nil, herr("not_found", "That archive has not arrived on this node.")
	}
	a := &archive{path: path, files: map[string]int64{}}
	tr, closeFn, err := openTar(path)
	if err != nil {
		return nil, herr("bad_archive", "The archive cannot be read: %s", clip(err.Error(), 120))
	}
	defer closeFn()
	count := 0
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, herr("bad_archive", "The archive cannot be read: %s", clip(err.Error(), 120))
		}
		count++
		if count-1 > maxMembers {
			return nil, herr("unsafe_archive", "The archive holds an unreasonable number of files.")
		}
		rel, cerr := cleanMember(h.Name)
		if cerr != nil {
			return nil, cerr
		}
		switch h.Typeflag {
		case tar.TypeSymlink, tar.TypeLink:
			a.skippedLinks++
		case tar.TypeDir:
		case tar.TypeReg, tar.TypeRegA:
			if rel != "" {
				if _, seen := a.files[rel]; !seen {
					a.order = append(a.order, rel)
				}
				a.files[rel] = h.Size
			}
		default:
			a.skippedLinks++ // devices, pipes: never copied
		}
	}
	return a, nil
}

func (a *archive) sha256() string {
	f, err := os.Open(a.path)
	if err != nil {
		return ""
	}
	defer f.Close()
	h := sha256.New()
	_, _ = io.Copy(h, f)
	return hex.EncodeToString(h.Sum(nil))
}

// reader opens one member for reading; the caller closes it.
func (a *archive) reader(rel string, limit int64) (io.Reader, func(), bool) {
	tr, closeFn, err := openTar(a.path)
	if err != nil {
		return nil, nil, false
	}
	for {
		h, err := tr.Next()
		if err != nil {
			closeFn()
			return nil, nil, false
		}
		if h.Typeflag == tar.TypeReg || h.Typeflag == tar.TypeRegA {
			if r, _ := cleanMember(h.Name); r == rel {
				return io.LimitReader(tr, limit), closeFn, true
			}
		}
	}
}

func (a *archive) text(rel string) (string, bool) {
	r, closeFn, ok := a.reader(rel, 8<<20)
	if !ok {
		return "", false
	}
	defer closeFn()
	raw, _ := io.ReadAll(r)
	return strings.ToValidUTF8(string(raw), "�"), true
}

// extract copies every regular file under prefix to target. Links and anything unsafe are skipped. Returns (copied, kept).
func (a *archive) extract(prefix, target string, replace bool, skip func(string) bool) (int, int, error) {
	tr, closeFn, err := openTar(a.path)
	if err != nil {
		return 0, 0, err
	}
	defer closeFn()
	target = filepath.Clean(target)
	copied, kept := 0, 0
	for {
		h, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return copied, kept, err
		}
		isFile := h.Typeflag == tar.TypeReg || h.Typeflag == tar.TypeRegA
		isDir := h.Typeflag == tar.TypeDir
		rel, cerr := cleanMember(h.Name)
		if cerr != nil {
			return copied, kept, cerr
		}
		if !(isFile || isDir) || !(rel == prefix || strings.HasPrefix(rel, prefix+"/")) {
			continue
		}
		inner := strings.TrimLeft(rel[len(prefix):], "/")
		if skip != nil && skip(inner) {
			continue
		}
		destination := filepath.Join(target, filepath.FromSlash(inner))
		if destination != target && !strings.HasPrefix(destination, target+string(filepath.Separator)) {
			continue
		}
		if isDir {
			_ = os.MkdirAll(destination, 0o755)
			continue
		}
		if _, err := os.Stat(destination); err == nil && !replace {
			kept++
			continue
		}
		if throughLink(target, destination) {
			continue // never write through a link that is already on the node
		}
		if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
			return copied, kept, err
		}
		out, err := os.OpenFile(destination, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o644)
		if err != nil {
			return copied, kept, err
		}
		_, cerr2 := io.Copy(out, tr)
		out.Close()
		if cerr2 != nil {
			return copied, kept, cerr2
		}
		copied++
	}
	return copied, kept, nil
}

func throughLink(target, destination string) bool {
	p := destination
	for {
		if info, err := os.Lstat(p); err == nil && info.Mode()&os.ModeSymlink != 0 {
			return true
		}
		if p == target {
			return false
		}
		next := filepath.Dir(p)
		if next == p || !strings.HasPrefix(next, target) {
			return false
		}
		p = next
	}
}

// --- parsers -------------------------------------------------------------------------------------------------------------------------------

func splitLines(text string) []string {
	text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
	lines := strings.Split(text, "\n")
	if len(lines) > 0 && lines[len(lines)-1] == "" {
		lines = lines[:len(lines)-1]
	}
	return lines
}

func parseKV(text string) map[string]string {
	out := map[string]string{}
	for _, line := range splitLines(text) {
		if strings.Contains(line, "=") && !strings.HasPrefix(line, "#") {
			k, v, _ := strings.Cut(line, "=")
			k = strings.TrimSpace(k)
			if _, seen := out[k]; !seen {
				out[k] = strings.TrimSpace(v)
			}
		}
	}
	return out
}

// parseYAMLFlat reads the few top-level facts needed from cPanel's userdata files: "key: value" and "key:" followed by "- item" lines. Not a general YAML reader.
func parseYAMLFlat(text string) (map[string]string, map[string][]string) {
	scalars, lists := map[string]string{}, map[string][]string{}
	current := ""
	has := false
	for _, line := range splitLines(text) {
		if strings.TrimSpace(line) == "" || strings.HasPrefix(strings.TrimLeft(line, " \t"), "#") {
			continue
		}
		if strings.HasPrefix(line, "- ") || strings.HasPrefix(line, "  - ") {
			if has {
				_, item, _ := strings.Cut(line, "- ")
				lists[current] = append(lists[current], strings.Trim(strings.TrimSpace(item), `'"`))
			}
			continue
		}
		if line[0] != ' ' && strings.Contains(line, ":") {
			k, v, _ := strings.Cut(line, ":")
			current, has = strings.TrimSpace(k), true
			v = strings.Trim(strings.TrimSpace(v), `'"`)
			if v != "" {
				scalars[current] = v
				delete(lists, current)
			} else {
				delete(scalars, current)
				lists[current] = []string{}
			}
		}
	}
	return scalars, lists
}

type mysqlGrant struct {
	db, user string
	privs    []string
}

func parseMysqlGrants(text string) (map[string]string, []mysqlGrant, []string) {
	hashes := map[string]string{}
	var grants []mysqlGrant
	unsupported := map[string]bool{}
	for _, line := range splitLines(text) {
		if m := usageRe.FindStringSubmatch(line); m != nil {
			hashes[m[1]] = strings.ToUpper(m[2])
			continue
		}
		m := grantRe.FindStringSubmatch(line)
		if m == nil {
			continue
		}
		var raw []string
		for _, p := range strings.Split(m[1], ",") {
			raw = append(raw, strings.ToUpper(strings.TrimSpace(p)))
		}
		db := strings.ReplaceAll(m[2], `\_`, "_")
		var privs []string
		if len(raw) == 1 && (raw[0] == "ALL PRIVILEGES" || raw[0] == "ALL") {
			privs = append(privs, allPrivs...)
		} else {
			set := map[string]bool{}
			for _, p := range raw {
				if v, ok := mapPriv[p]; ok {
					set[v] = true
				} else {
					unsupported[p] = true
				}
			}
			for v := range set {
				privs = append(privs, v)
			}
			sort.Strings(privs)
		}
		grants = append(grants, mysqlGrant{db, m[3], privs})
	}
	var un []string
	for p := range unsupported {
		un = append(un, p)
	}
	sort.Strings(un)
	return hashes, grants, un
}

func parseShadow(text string) map[string]string {
	out := map[string]string{}
	for _, line := range splitLines(text) {
		bits := strings.Split(line, ":")
		if len(bits) >= 2 && bits[0] != "" && bits[1] != "" && !strings.HasPrefix(bits[1], "!") && !strings.HasPrefix(bits[1], "*") {
			out[bits[0]] = bits[1]
		}
	}
	return out
}

type alias struct{ source, destination string }

func parseValiases(text string) []alias {
	var out []alias
	for _, line := range splitLines(text) {
		if strings.Contains(line, ":") && !strings.HasPrefix(line, "#") {
			source, destination, _ := strings.Cut(line, ":")
			for _, dest := range strings.Split(destination, ",") {
				dest = strings.TrimSpace(dest)
				if strings.TrimSpace(source) != "" && strings.Contains(dest, "@") && !strings.Contains(dest, "|") && !strings.Contains(dest, "/") {
					out = append(out, alias{strings.TrimSpace(source), dest})
				}
			}
		}
	}
	return out
}

// --- discovery and pre-flight --------------------------------------------------------------------------------------------------------------

func skippedHome(rel string) bool {
	top, _, _ := strings.Cut(rel, "/")
	return skipTop[top] || top == "" || !strings.Contains(rel, "/")
}


func sortedCopy(in []string) []string {
	out := append([]string{}, in...)
	sort.Strings(out)
	return out
}

func orEmpty(in []string) []string {
	if in == nil {
		return []string{}
	}
	return in
}

type mailEntry struct {
	domain    string
	mailboxes []string
	bytes     int64
}

func discover(a *archive, account string) map[string]any {
	user := ""
	for _, n := range a.order {
		if strings.HasPrefix(n, "cp/") {
			user = strings.SplitN(n, "/", 2)[1]
			break
		}
	}
	info := map[string]string{}
	if user != "" {
		if t, ok := a.text("cp/" + user); ok {
			info = parseKV(t)
		}
	}
	mainText, _ := a.text("userdata/main")
	mainScalars, mainLists := parseYAMLFlat(mainText)
	mainDomain := mainScalars["main_domain"]
	if mainDomain == "" {
		mainDomain = info["DNS"]
	}
	addon := sortedCopy(mainLists["addon_domains"])
	var subs, parked []string
	for _, s := range mainLists["sub_domains"] {
		if s != "" {
			subs = append(subs, s)
		}
	}
	for _, s := range mainLists["parked_domains"] {
		if s != "" {
			parked = append(parked, s)
		}
	}
	phpRaw := ""
	if mainDomain != "" {
		if t, ok := a.text("userdata/" + mainDomain); ok {
			sc, _ := parseYAMLFlat(t)
			phpRaw = sc["phpversion"]
		}
	}
	dbSet := map[string]bool{}
	for _, n := range a.order {
		if strings.HasPrefix(n, "mysql/") && strings.HasSuffix(n, ".sql") {
			dbSet[strings.TrimSuffix(strings.SplitN(n, "/", 2)[1], ".sql")] = true
		}
	}
	var dbNames []string
	for d := range dbSet {
		dbNames = append(dbNames, d)
	}
	sort.Strings(dbNames)
	grantText, _ := a.text("mysql.sql")
	hashes, grants, unsupported := parseMysqlGrants(grantText)
	mailBytes := map[string]map[string]int64{}
	for _, n := range a.order {
		parts := strings.Split(n, "/")
		if len(parts) >= 4 && parts[0] == "homedir" && parts[1] == "mail" && parts[2] != "cur" && parts[2] != "new" && parts[2] != "tmp" && !strings.HasPrefix(parts[2], ".") {
			if mailBytes[parts[2]] == nil {
				mailBytes[parts[2]] = map[string]int64{}
			}
			mailBytes[parts[2]][parts[3]] += a.files[n]
		}
	}
	var mailDomains []string
	for d := range mailBytes {
		mailDomains = append(mailDomains, d)
	}
	sort.Strings(mailDomains)
	var mail []mailEntry
	forwarders := 0
	for _, d := range mailDomains {
		var boxes []string
		var total int64
		for b, size := range mailBytes[d] {
			boxes = append(boxes, b)
			total += size
		}
		sort.Strings(boxes)
		mail = append(mail, mailEntry{d, boxes, total})
		if t, ok := a.text("va/" + d); ok {
			forwarders += len(parseValiases(t))
		}
	}
	cron := []string{}
	if user != "" {
		if t, ok := a.text("cron/" + user); ok {
			cron = cronJobsOf(t)
		}
	}
	var zones []string
	certs := 0
	var homeFiles []string
	hasHomedir := false
	for _, n := range a.order {
		if strings.HasPrefix(n, "dnszones/") && strings.HasSuffix(n, ".db") {
			zones = append(zones, strings.TrimSuffix(strings.SplitN(n, "/", 2)[1], ".db"))
		}
		if strings.HasPrefix(n, "sslcerts/") || strings.HasPrefix(n, "ssl/") {
			certs++
		}
		if strings.HasPrefix(n, "homedir/") {
			hasHomedir = true
			if !skippedHome(n[len("homedir/"):]) {
				homeFiles = append(homeFiles, n)
			}
		}
	}
	sort.Strings(zones)
	var homeBytes int64
	for _, n := range homeFiles {
		homeBytes += a.files[n]
	}
	phpTarget := "8.2"
	if v, ok := phpMap[phpRaw]; ok {
		phpTarget = v
	}
	databases := []map[string]any{}
	for _, d := range dbNames {
		databases = append(databases, map[string]any{"name": d, "bytes": a.files["mysql/"+d+".sql"]})
	}
	var dbUsers []string
	for u := range hashes {
		dbUsers = append(dbUsers, u)
	}
	sort.Strings(dbUsers)
	mailOut := []map[string]any{}
	for _, m := range mail {
		mailOut = append(mailOut, map[string]any{"domain": m.domain, "mailboxes": m.mailboxes, "bytes": m.bytes})
	}
	var archiveBytes int64
	if info, err := os.Stat(a.path); err == nil {
		archiveBytes = info.Size()
	}
	blockers, warnings := []string{}, []string{}
	if user == "" || mainDomain == "" {
		blockers = append(blockers, "This does not look like a cPanel account backup (no account or main domain found).")
	}
	if account != "" && user != "" && user != account {
		warnings = append(warnings, fmt.Sprintf("The cPanel account is called %s; on this platform it is %s. Database names starting with %s_ are renamed to %s_.", user, account, user, account))
	}
	if len(homeFiles) == 0 && !hasHomedir {
		blockers = append(blockers, "The backup holds no website files (homedir is missing).")
	}
	if len(addon) > 0 || len(subs) > 0 || len(parked) > 0 {
		var bits []string
		if len(addon) > 0 {
			bits = append(bits, fmt.Sprintf("%d addon", len(addon)))
		}
		if len(subs) > 0 {
			bits = append(bits, fmt.Sprintf("%d sub", len(subs)))
		}
		if len(parked) > 0 {
			bits = append(bits, fmt.Sprintf("%d parked", len(parked)))
		}
		blockers = append(blockers, "The account has "+strings.Join(bits, ", ")+" domain(s). A native account serves one domain, so these need a manual decision (give each its own account, or leave them out).")
	}
	if len(cron) > 0 {
		warnings = append(warnings, fmt.Sprintf("%d scheduled job(s) (cron): plain php scripts inside the account are recreated, anything else is listed so it can be recreated by hand.", len(cron)))
	}
	if len(unsupported) > 0 {
		warnings = append(warnings, "Some database privileges have no equivalent here and were left out: "+strings.Join(unsupported, ", ")+".")
	}
	if certs > 0 {
		warnings = append(warnings, "Certificates are not moved: a new one is issued for the site after the cutover.")
	}
	if len(mail) > 0 {
		warnings = append(warnings, "Mail is imported on the mail node; mailbox passwords keep working only if the archive holds their hashes.")
	}
	if _, known := phpMap[phpRaw]; phpRaw != "" && !known {
		warnings = append(warnings, fmt.Sprintf("PHP %s is unknown; %s will be used.", phpRaw, phpTarget))
	} else if phpRaw == "ea-php74" || phpRaw == "ea-php80" {
		warnings = append(warnings, fmt.Sprintf("The site ran %s, which is no longer offered; it will run on PHP %s. Test it before the cutover.", phpRaw, phpTarget))
	}
	if a.skippedLinks > 0 {
		warnings = append(warnings, fmt.Sprintf("%d link(s) in the archive were ignored for safety.", a.skippedLinks))
	}
	return map[string]any{
		"user": user, "main_domain": mainDomain, "addon_domains": orEmpty(addon), "sub_domains": orEmpty(subs), "parked_domains": orEmpty(parked),
		"php_cpanel": phpRaw, "php_target": phpTarget, "plan": info["PLAN"], "contact_email": info["CONTACTEMAIL"],
		"files": len(homeFiles), "files_mb": round2(float64(homeBytes) / 1048576),
		"databases": databases, "db_users": orEmpty(dbUsers), "db_grants": len(grants), "mail": mailOut, "forwarders": forwarders,
		"cron": cron, "dns_zones": orEmpty(zones), "certificates": certs, "skipped_links": a.skippedLinks,
		"archive_sha256": a.sha256(), "archive_mb": round2(float64(archiveBytes) / 1048576), "blockers": blockers, "warnings": warnings,
	}
}

// --- the node ------------------------------------------------------------------------------------------------------------------------------

func (a *Agent) migrationArchive(name string) (*archive, error) {
	return openArchive(filepath.Join(a.StageRoot, migrationDir, name))
}

func (a *Agent) migrateAnalyze(p map[string]any) (Result, error) {
	ar, err := a.migrationArchive(str(p, "archive"))
	if err != nil {
		return Result{}, err
	}
	report := discover(ar, str(p, "account"))
	if want := str(p, "expected_sha256"); want != "" && strings.ToLower(want) != report["archive_sha256"] {
		return Result{}, herr("checksum_mismatch", "The archive that arrived is not the one that was made (its checksum differs): copy it again.")
	}
	mail := []map[string]any{}
	for _, m := range report["mail"].([]map[string]any) {
		boxes := m["mailboxes"].([]string)
		if len(boxes) > 200 {
			boxes = boxes[:200]
		}
		mail = append(mail, map[string]any{"domain": m["domain"], "mailboxes": boxes, "bytes": m["bytes"]})
	}
	report["mail"] = mail
	if cron := report["cron"].([]string); len(cron) > 50 {
		report["cron"] = cron[:50]
	}
	return Result{"analyzed", report}, nil
}

func (a *Agent) migrateImport(p map[string]any) (Result, error) {
	account := str(p, "account")
	ar, err := a.migrationArchive(str(p, "archive"))
	if err != nil {
		return Result{}, err
	}
	report := discover(ar, account)
	switch str(p, "part") {
	case "files":
		return a.migrateFiles(account, ar, str(p, "replace") == "yes")
	case "databases":
		return a.migrateDatabases(account, ar, report)
	case "cron":
		return a.migrateCron(account, ar, report)
	}
	return a.migrateMail(account, ar, report)
}

func (a *Agent) migrateFiles(account string, ar *archive, replace bool) (Result, error) {
	domain := a.StateOf(account)
	if domain == "" || a.RootFor == nil || a.RootFor(account) == "" {
		return Result{}, herr("not_found", "Make the website on this node first.")
	}
	site := filepath.Join(a.RootFor(account), domain)
	copied, kept, err := ar.extract("homedir/public_html", filepath.Join(site, "public_html"), replace, nil)
	if err != nil {
		return Result{}, err
	}
	tops := map[string]bool{}
	for _, n := range ar.order {
		if strings.HasPrefix(n, "homedir/") && strings.Count(n, "/") >= 2 {
			tops[strings.Split(n, "/")[1]] = true
		}
	}
	delete(tops, "public_html")
	var names []string
	for t := range tops {
		names = append(names, t)
	}
	sort.Strings(names)
	extra := 0
	for _, top := range names {
		if skippedHome(top + "/x") {
			continue
		}
		c, k, err := ar.extract("homedir/"+top, filepath.Join(site, top), replace, nil)
		if err != nil {
			return Result{}, err
		}
		extra, kept = extra+c, kept+k
	}
	// the agent runs as root, so what it wrote is root's: hand it to the account (group apache, as for every other file of the site) and keep other accounts out
	if _, err := a.Sys.Run([]string{"chown", "-R", account + ":apache", site}, ""); err != nil {
		return Result{}, err
	}
	if _, err := a.Sys.Run([]string{"chmod", "-R", "u+rwX,g+rX,o-rwx", site}, ""); err != nil {
		return Result{}, err
	}
	return Result{"imported", map[string]any{"files": copied + extra, "kept_existing": kept, "outside_public_html": extra}}, nil
}

func renamed(name, source, account string) string {
	if source != account && strings.HasPrefix(name, source+"_") {
		return account + name[len(source):]
	}
	return name
}

func (a *Agent) migrateDatabases(account string, ar *archive, report map[string]any) (Result, error) {
	source, _ := report["user"].(string)
	if source == "" {
		source = account
	}
	grantText, _ := ar.text("mysql.sql")
	hashes, grants, _ := parseMysqlGrants(grantText)
	prefix := account + "_"
	existingList, err := a.Engine.Databases(prefix)
	if err != nil {
		return Result{}, err
	}
	existing := map[string]bool{}
	for _, d := range existingList {
		existing[d] = true
	}
	done, skipped, made := []string{}, []string{}, []string{}
	for _, entry := range report["databases"].([]map[string]any) {
		orig := entry["name"].(string)
		name, err := Ident(renamed(orig, source, account))
		if err != nil {
			return Result{}, err
		}
		if !strings.HasPrefix(name, prefix) {
			return Result{}, herr("forbidden", "The archive holds a database (%s) that does not belong to this account.", orig)
		}
		if existing[name] {
			skipped = append(skipped, name)
			continue
		}
		if err := a.Engine.CreateDB(name); err != nil {
			return Result{}, err
		}
		r, closeFn, ok := ar.reader("mysql/"+orig+".sql", 64<<20)
		var lerr error
		if ok {
			lerr = a.Engine.LoadSQL(name, r)
			closeFn()
		}
		if lerr != nil {
			_ = a.Engine.DropDB(name) // a half-loaded database is worse than none
			return Result{}, lerr
		}
		done = append(done, name)
	}
	usersList, err := a.Engine.UsersWith(prefix)
	if err != nil {
		return Result{}, err
	}
	users := map[string]bool{}
	for _, u := range usersList {
		users[u] = true
	}
	names := make([]string, 0, len(hashes))
	for u := range hashes {
		names = append(names, u)
	}
	sort.Strings(names)
	for _, user := range names {
		name, err := Ident(renamed(user, source, account))
		if err != nil {
			return Result{}, err
		}
		if !strings.HasPrefix(name, prefix) || users[name] {
			continue
		}
		if err := a.Engine.CreateUser(name, hashes[user], dbConnections); err != nil {
			return Result{}, err
		}
		made = append(made, name)
	}
	for _, g := range grants {
		dbName, userName := renamed(g.db, source, account), renamed(g.user, source, account)
		dbs, _ := a.Engine.Databases(prefix)
		us, _ := a.Engine.UsersWith(prefix)
		if contains(dbs, dbName) && contains(us, userName) && len(g.privs) > 0 {
			if err := a.Engine.SetGrants(dbName, userName, g.privs); err != nil {
				return Result{}, err
			}
		}
	}
	return Result{"imported", map[string]any{"databases": done, "already_there": skipped, "users": made}}, nil
}

func (a *Agent) migrateMail(account string, ar *archive, report map[string]any) (Result, error) {
	if a.MailRoot == "" {
		return Result{}, herr("not_applicable", "This node does not hold mail.")
	}
	mailboxes, messages, forwarders := 0, 0, 0
	skipped := []string{}
	for _, entry := range report["mail"].([]map[string]any) {
		domain := entry["domain"].(string)
		st := a.mailState(domain)
		if st == nil || st.Account != account {
			skipped = append(skipped, domain) // the domain must be set up for this account on this node first
			continue
		}
		shadowText, _ := ar.text("homedir/etc/" + domain + "/shadow")
		hashes := parseShadow(shadowText)
		all := a.mailAll()
		for _, local := range entry["mailboxes"].([]string) {
			if _, have := st.Mailboxes[local]; !have {
				if !strings.HasPrefix(hashes[local], "$") {
					skipped = append(skipped, local+"@"+domain) // no usable hash: the customer sets a new password
					continue
				}
				st.Mailboxes[local] = &mailBox{QuotaMB: 0, Hash: hashes[local], Sent: []float64{}}
				mailboxes++
			}
			target := a.maildir(domain, local)
			for _, folder := range mailFolder {
				for _, sub := range []string{"cur", "new", "tmp"} {
					if err := os.MkdirAll(filepath.Join(target, folder, sub), 0o755); err != nil {
						return Result{}, err
					}
				}
			}
			c, _, err := ar.extract("homedir/mail/"+domain+"/"+local, target, false, func(rel string) bool {
				return strings.HasSuffix(rel, ".index") || strings.HasSuffix(rel, ".log") || strings.HasSuffix(rel, ".cache")
			})
			if err != nil {
				return Result{}, err
			}
			messages += c
		}
		valiasText, _ := ar.text("va/" + domain)
		for _, al := range parseValiases(valiasText) {
			if !strings.HasSuffix(al.source, "@"+domain) {
				continue
			}
			dup := false
			for _, f := range st.Forwarders {
				if f.Source == al.source && f.Destination == al.destination {
					dup = true
				}
			}
			if !dup {
				st.Forwarders = append(st.Forwarders, forward{al.source, al.destination})
				forwarders++
			}
		}
		if err := a.mailCommit(st, all); err != nil {
			return Result{}, err
		}
		if _, err := a.Sys.Run([]string{"chown", "-R", "5000:5000", filepath.Join(a.MailRoot, domain)}, ""); err != nil {
			return Result{}, err
		}
	}
	return Result{"imported", map[string]any{"mailboxes": mailboxes, "messages": messages, "forwarders": forwarders, "skipped": skipped}}, nil
}

func (a *Agent) migrateValidate(p map[string]any) (Result, error) {
	account := str(p, "account")
	ar, err := a.migrationArchive(str(p, "archive"))
	if err != nil {
		return Result{}, err
	}
	report := discover(ar, account)
	domain := a.StateOf(account)
	if domain == "" || a.RootFor == nil || a.RootFor(account) == "" {
		return Result{}, herr("not_found", "No such website on this node.")
	}
	st := a.stateRead(pathsOfState(account))
	site := filepath.Join(a.RootFor(account), domain)
	expected := map[string]bool{}
	for _, n := range ar.order {
		if strings.HasPrefix(n, "homedir/") && !skippedHome(n[len("homedir/"):]) {
			expected[n[len("homedir/"):]] = true
		}
	}
	missing := []string{}
	for e := range expected {
		if info, err := os.Stat(filepath.Join(site, filepath.FromSlash(e))); err != nil || !info.Mode().IsRegular() {
			missing = append(missing, e)
		}
	}
	sort.Strings(missing)
	source, _ := report["user"].(string)
	if source == "" {
		source = account
	}
	dbs, _ := a.Engine.Databases(account + "_")
	databases := []map[string]any{}
	allThere := true
	for _, entry := range report["databases"].([]map[string]any) {
		name := renamed(entry["name"].(string), source, account)
		there := contains(dbs, name)
		tables := 0
		if there {
			t, _ := a.Engine.Tables(name)
			tables = len(t)
		} else {
			allThere = false
		}
		databases = append(databases, map[string]any{"name": name, "present": there, "tables": tables})
	}
	index := ""
	for _, n := range []string{"index.php", "index.html"} {
		if info, err := os.Stat(filepath.Join(site, "public_html", n)); err == nil && info.Mode().IsRegular() {
			index = n
			break
		}
	}
	ok := len(missing) == 0 && allThere
	sample := missing
	if len(sample) > 10 {
		sample = sample[:10]
	}
	code := "differences"
	if ok {
		code = "validated"
	}
	php := ""
	if st != nil {
		php = st.text("php_version")
	}
	return Result{code, map[string]any{"ok": ok, "files_expected": len(expected), "files_missing": len(missing), "missing_sample": orEmpty(sample), "databases": databases, "index": index, "php": php}}, nil
}

func (a *Agent) migrateDiscard(p map[string]any) (Result, error) {
	path := filepath.Join(a.StageRoot, migrationDir, str(p, "archive"))
	if info, err := os.Stat(path); err != nil || !info.Mode().IsRegular() {
		return Result{"absent", map[string]any{}}, nil
	}
	if err := os.Remove(path); err != nil {
		return Result{}, err
	}
	return Result{"discarded", map[string]any{}}, nil
}

// --- cron jobs --------------------------------------------------------------------------------------------------------------------------------

var (
	cronEnvLine   = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*\s*=`)
	cronPHPWord   = regexp.MustCompile(`^(?:/[A-Za-z0-9_./-]*/)?php[0-9.]*$`)
	cronShell     = regexp.MustCompile("[;|&<>$`()" + `\\` + "]") // any shell symbol, including a backslash
	cronRedirect  = regexp.MustCompile(`\s*(?:>>?\s*/dev/null)?(?:\s*2>&1)?\s*$`)
	cronMacroList = map[string]string{"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0", "@monthly": "0 0 1 * *"}
)

// cronJobsOf are the job lines of a crontab: comments, blank lines and NAME=value settings are not jobs.
func cronJobsOf(text string) []string {
	out := []string{}
	for _, l := range splitLines(text) {
		t := strings.TrimSpace(l)
		if t != "" && !strings.HasPrefix(strings.TrimLeft(l, " 	"), "#") && !cronEnvLine.MatchString(t) {
			out = append(out, l)
		}
	}
	return out
}

// convertCronLine turns a cPanel crontab line into (schedule, "php script args"), or says why it cannot become a native job (only php scripts inside the account can).
func convertCronLine(line, user string) (string, string, error) {
	line = strings.TrimSpace(line)
	var schedule, command string
	if strings.HasPrefix(line, "@") {
		macro, rest, _ := strings.Cut(line, " ")
		s, ok := cronMacroList[macro]
		if !ok {
			return "", "", fmt.Errorf("%s is not supported", macro)
		}
		schedule, command = s, rest
	} else {
		parts := strings.Fields(line)
		if len(parts) < 6 {
			return "", "", fmt.Errorf("not a schedule and a command")
		}
		schedule = strings.Join(parts[:5], " ")
		// the command is the rest of the line after the fifth field, exactly as written
		rest := line
		for i := 0; i < 5; i++ {
			rest = strings.TrimLeft(rest, " 	")
			rest = rest[strings.IndexAny(rest, " 	"):]
		}
		command = rest
	}
	command = cronRedirect.ReplaceAllString(strings.TrimSpace(command), "")
	if cronShell.MatchString(command) {
		return "", "", fmt.Errorf("uses shell features (pipes, redirects, variables)")
	}
	tokens := strings.Fields(command)
	if len(tokens) == 0 || !cronPHPWord.MatchString(tokens[0]) {
		return "", "", fmt.Errorf("is not a php script")
	}
	if len(tokens) < 2 || strings.HasPrefix(tokens[1], "-") {
		return "", "", fmt.Errorf("php options are not supported")
	}
	script := tokens[1]
	home := "/home/" + user + "/"
	switch {
	case strings.HasPrefix(script, home):
		script = script[len(home):]
	case strings.HasPrefix(script, "/"):
		return "", "", fmt.Errorf("runs a script outside the account")
	}
	script = strings.TrimPrefix(script, "./")
	return schedule, strings.Join(append([]string{"php", script}, tokens[2:]...), " "), nil
}

func (a *Agent) migrateCron(account string, ar *archive, report map[string]any) (Result, error) {
	user, _ := report["user"].(string)
	text, _ := ar.text("cron/" + user)
	created, exists := 0, 0
	skipped := []map[string]any{}
	add := func(line, reason string) {
		if len(skipped) < 30 {
			skipped = append(skipped, map[string]any{"line": clip(line, 120), "reason": reason})
		}
	}
	for _, line := range cronJobsOf(text) {
		schedule, command, err := convertCronLine(line, user)
		if err != nil {
			add(line, err.Error())
			continue
		}
		res, err := a.cronCreate(map[string]any{"account": account, "schedule": schedule, "command": command})
		if err != nil {
			if he, ok := err.(*Error); ok {
				add(line, clip(he.Message, 120)) // the node's own rules (every five minutes at most, ten jobs) have the last word
				continue
			}
			return Result{}, err
		}
		if res.Code == "created" {
			created++
		} else {
			exists++
		}
	}
	return Result{"imported", map[string]any{"created": created, "already_there": exists, "skipped": skipped}}, nil
}

// --- loading SQL ------------------------------------------------------------------------------------------------------------------------------

// LoadSQL streams plain SQL into one database through the client. The text comes from a customer's backup: it can only act inside this one database.
func (MariaDB) LoadSQL(db string, r io.Reader) error {
	name, err := Ident(db)
	if err != nil {
		return err
	}
	cmd := exec.Command("mariadb", "--max-allowed-packet=256M", name)
	cmd.Stdin = r
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return sqlFail("Loading the database failed: %s", clip(strings.TrimSpace(stderr.String()), 200))
	}
	return nil
}

// LoadSQL on the pretend engine understands CREATE TABLE and INSERT INTO ... VALUES, which is what a dump holds.
func (f *FakeEngine) LoadSQL(db string, r io.Reader) error {
	if _, err := Ident(db); err != nil {
		return err
	}
	raw, _ := io.ReadAll(r)
	text := string(raw)
	tables := f.DBs[db]
	if tables == nil {
		return sqlFail("No such database.")
	}
	for _, m := range createRe.FindAllStringSubmatch(text, -1) {
		cols := []Column{}
		for _, line := range strings.Split(m[2], "\n") {
			line = strings.TrimRight(strings.TrimSpace(line), ",")
			if c := columnRe.FindStringSubmatch(line); c != nil {
				cols = append(cols, Column{Name: c[1], Type: c[2], Nullable: !strings.Contains(c[3], "NOT NULL")})
			}
		}
		tables[m[1]] = &fakeTable{Columns: cols, Rows: [][]any{}}
	}
	for _, m := range insertRe.FindAllStringSubmatch(text, -1) {
		if t, ok := tables[m[1]]; ok {
			t.Rows = append(t.Rows, sqlRows(m[2])...)
		}
	}
	return nil
}

// sqlRows reads the (1,'a'),(2,'b') part of an INSERT into rows of values.
func sqlRows(text string) [][]any {
	rows := [][]any{}
	var row []any
	inRow := false
	i, n := 0, len(text)
	for i < n {
		c := text[i]
		switch {
		case c == '(' && !inRow:
			row, inRow = []any{}, true
			i++
		case !inRow:
			i++
		case c == ')':
			rows = append(rows, row)
			inRow = false
			i++
		case c == ',' || c == ' ':
			i++
		case c == '\'':
			j := i + 1
			var out []byte
			for j < n && text[j] != '\'' {
				if text[j] == '\\' && j+1 < n {
					j++
				}
				out = append(out, text[j])
				j++
			}
			row = append(row, string(out))
			i = j + 1
		default:
			j := i
			for j < n && text[j] != ',' && text[j] != ')' {
				j++
			}
			token := strings.TrimSpace(text[i:j])
			switch {
			case strings.EqualFold(token, "NULL"):
				row = append(row, nil)
			case intRe.MatchString(token):
				v, _ := strconv.ParseInt(token, 10, 64)
				row = append(row, v)
			case floatRe.MatchString(token):
				v, _ := strconv.ParseFloat(token, 64)
				row = append(row, v)
			default:
				row = append(row, token)
			}
			i = j
		}
	}
	return rows
}
