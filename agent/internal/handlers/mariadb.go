package handlers

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"whagent/internal/system"
)

// MariaDB talks to the real database server as root over the local socket through the "mariadb" client. It has NEVER been run against a real server. Names are checked again here (letters, digits
// and underscore only) before they are placed in SQL; a password never appears in a command line (it is sent to the client already turned into a hash); the client is started from an
// argument list, never a shell.
type MariaDB struct{}

var (
	hashRe   = regexp.MustCompile(`^\*[0-9A-F]{40}$`)
	privSQL  = map[string]string{"select": "SELECT", "insert": "INSERT", "update": "UPDATE", "delete": "DELETE", "create": "CREATE", "alter": "ALTER", "drop": "DROP", "index": "INDEX"}
	privBack = map[string]string{"SELECT": "select", "INSERT": "insert", "UPDATE": "update", "DELETE": "delete", "CREATE": "create", "ALTER": "alter", "DROP": "drop", "INDEX": "index"}
)

func sqlFail(format string, a ...any) error { return &system.Error{Msg: fmt.Sprintf(format, a...)} }

func (MariaDB) sql(statement, database string) ([][]string, error) {
	argv := []string{"--batch", "--raw", "--skip-column-names"}
	if database != "" {
		argv = append(argv, database)
	}
	cmd := exec.Command("mariadb", argv...)
	cmd.Stdin = strings.NewReader(statement)
	var out, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &stderr
	if err := cmd.Run(); err != nil {
		text := strings.TrimSpace(stderr.String())
		if len(text) > 200 {
			text = text[:200]
		}
		return nil, sqlFail("mariadb failed: %s", text)
	}
	var rows [][]string
	for _, line := range strings.Split(strings.TrimRight(out.String(), "\n"), "\n") {
		if line != "" {
			rows = append(rows, strings.Split(line, "\t"))
		}
	}
	return rows, nil
}

func likePrefix(prefix string) (string, error) {
	base, err := Ident(strings.TrimSuffix(prefix, "_"))
	if err != nil {
		return "", err
	}
	return base + `\_%`, nil
}

func (m MariaDB) Databases(prefix string) ([]string, error) {
	like, err := likePrefix(prefix)
	if err != nil {
		return nil, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE '%s' ORDER BY 1;", like), "")
	return firstColumn(rows), err
}

func firstColumn(rows [][]string) []string {
	out := []string{}
	for _, r := range rows {
		out = append(out, r[0])
	}
	return out
}

func (m MariaDB) CreateDB(name string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	_, err := m.sql("CREATE DATABASE `"+name+"` CHARACTER SET utf8mb4;", "")
	return err
}

func (m MariaDB) DropDB(name string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	_, err := m.sql("DROP DATABASE IF EXISTS `"+name+"`;", "")
	return err
}

func (m MariaDB) UsersWith(prefix string) ([]string, error) {
	like, err := likePrefix(prefix)
	if err != nil {
		return nil, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT user FROM mysql.user WHERE user LIKE '%s' AND host='localhost' ORDER BY 1;", like), "")
	return firstColumn(rows), err
}

func (m MariaDB) CreateUser(name, hash string, maxConnections int) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	if !hashRe.MatchString(hash) {
		return sqlFail("Not a password hash.")
	}
	limit := ""
	if maxConnections > 0 {
		limit = " WITH MAX_USER_CONNECTIONS " + strconv.Itoa(maxConnections)
	}
	_, err := m.sql(fmt.Sprintf("CREATE USER '%s'@'localhost' IDENTIFIED BY PASSWORD '%s'%s;", name, hash, limit), "")
	return err
}

func (m MariaDB) SetHash(name, hash string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	if !hashRe.MatchString(hash) {
		return sqlFail("Not a password hash.")
	}
	_, err := m.sql(fmt.Sprintf("ALTER USER '%s'@'localhost' IDENTIFIED BY PASSWORD '%s';", name, hash), "")
	return err
}

func (m MariaDB) DropUser(name string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	_, err := m.sql("DROP USER IF EXISTS '"+name+"'@'localhost';", "")
	return err
}

func (m MariaDB) SetGrants(db, user string, privileges []string) error {
	if _, err := Ident(db); err != nil {
		return err
	}
	if _, err := Ident(user); err != nil {
		return err
	}
	if _, err := m.sql(fmt.Sprintf("REVOKE ALL PRIVILEGES ON `%s`.* FROM '%s'@'localhost';", db, user), ""); err != nil && !strings.Contains(err.Error(), "no such grant") {
		return err // revoking what was never granted is not a failure
	}
	if len(privileges) == 0 {
		return nil
	}
	var list []string
	for _, p := range privileges {
		sqlName, ok := privSQL[p]
		if !ok {
			return sqlFail("That privilege is not available.")
		}
		list = append(list, sqlName)
	}
	_, err := m.sql(fmt.Sprintf("GRANT %s ON `%s`.* TO '%s'@'localhost';", strings.Join(list, ", "), db, user), "")
	return err
}

var granteeRe = regexp.MustCompile(`^'([^']+)'@`)

func (m MariaDB) GrantsOf(prefix string) ([]Grant, error) {
	like, err := likePrefix(prefix)
	if err != nil {
		return nil, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT table_schema, grantee, privilege_type FROM information_schema.schema_privileges WHERE table_schema LIKE '%s';", like), "")
	if err != nil {
		return nil, err
	}
	found := map[[2]string]map[string]bool{}
	for _, r := range rows {
		if len(r) < 3 {
			continue
		}
		g := granteeRe.FindStringSubmatch(r[1])
		name, ok := privBack[strings.ToUpper(r[2])]
		if g == nil || !ok {
			continue
		}
		key := [2]string{strings.ReplaceAll(r[0], `\_`, "_"), g[1]}
		if found[key] == nil {
			found[key] = map[string]bool{}
		}
		found[key][name] = true
	}
	var out []Grant
	for k, set := range found {
		privs := keysOf(set)
		sort.Strings(privs)
		out = append(out, Grant{k[0], k[1], privs})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Database != out[j].Database {
			return out[i].Database < out[j].Database
		}
		return out[i].User < out[j].User
	})
	return out, nil
}

func (m MariaDB) SizeMB(db string) (float64, error) {
	if _, err := Ident(db); err != nil {
		return 0, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT ROUND(IFNULL(SUM(data_length+index_length),0)/1048576,2) FROM information_schema.tables WHERE table_schema='%s';", db), "")
	if err != nil || len(rows) == 0 {
		return 0, err
	}
	v, _ := strconv.ParseFloat(rows[0][0], 64)
	return v, nil
}

func (m MariaDB) Tables(db string) ([]Table, error) {
	if _, err := Ident(db); err != nil {
		return nil, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT table_name, IFNULL(table_rows,0), IFNULL(data_length+index_length,0) FROM information_schema.tables WHERE table_schema='%s' ORDER BY 1;", db), "")
	out := []Table{}
	for _, r := range rows {
		n, _ := strconv.ParseInt(r[1], 10, 64)
		size, _ := strconv.ParseInt(r[2], 10, 64)
		out = append(out, Table{r[0], n, size / 1024})
	}
	return out, err
}

func (m MariaDB) Columns(db, table string) ([]Column, error) {
	if _, err := Ident(db); err != nil {
		return nil, err
	}
	if _, err := Ident(table); err != nil {
		return nil, err
	}
	rows, err := m.sql(fmt.Sprintf("SELECT column_name, column_type, is_nullable, column_key, column_default FROM information_schema.columns WHERE table_schema='%s' AND table_name='%s' ORDER BY ordinal_position;", db, table), "")
	out := []Column{}
	for _, r := range rows {
		c := Column{Name: r[0], Type: r[1], Nullable: r[2] == "YES", Primary: r[3] == "PRI"}
		if len(r) > 4 && r[4] != "NULL" {
			d := r[4]
			c.Default = &d
		}
		out = append(out, c)
	}
	return out, err
}

func (m MariaDB) Rows(db, table string, offset, limit int) ([][]any, int64, error) {
	if _, err := Ident(db); err != nil {
		return nil, 0, err
	}
	if _, err := Ident(table); err != nil {
		return nil, 0, err
	}
	count, err := m.sql(fmt.Sprintf("SELECT COUNT(*) FROM `%s`.`%s`;", db, table), "")
	if err != nil || len(count) == 0 {
		return nil, 0, err
	}
	total, _ := strconv.ParseInt(count[0][0], 10, 64)
	rows, err := m.sql(fmt.Sprintf("SELECT * FROM `%s` LIMIT %d OFFSET %d;", table, limit, offset), db)
	out := [][]any{}
	for _, r := range rows {
		row := make([]any, len(r))
		for i, v := range r {
			if v == "NULL" {
				row[i] = nil
			} else {
				row[i] = v
			}
		}
		out = append(out, row)
	}
	return out, total, err
}

func (m MariaDB) Dump(db, path string) error {
	if _, err := Ident(db); err != nil {
		return err
	}
	out, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	defer out.Close()
	dump := exec.Command("mariadb-dump", "--single-transaction", "--routines", db)
	pack := exec.Command("zstd", "-q", "-T0")
	pipe, err := dump.StdoutPipe()
	if err != nil {
		return err
	}
	pack.Stdin, pack.Stdout = pipe, out
	if err := pack.Start(); err != nil {
		return sqlFail("The database dump failed.")
	}
	if err := dump.Run(); err != nil {
		_ = pack.Wait()
		return sqlFail("The database dump failed.")
	}
	if err := pack.Wait(); err != nil {
		return sqlFail("The database dump failed.")
	}
	return nil
}

func (m MariaDB) Load(db, path string) error {
	if err := m.DropDB(db); err != nil {
		return err
	}
	if err := m.CreateDB(db); err != nil {
		return err
	}
	unpack := exec.Command("zstd", "-dc", path)
	load := exec.Command("mariadb", db)
	pipe, err := unpack.StdoutPipe()
	if err != nil {
		return err
	}
	load.Stdin = pipe
	if err := load.Start(); err != nil {
		return sqlFail("Restoring the database failed.")
	}
	if err := unpack.Run(); err != nil {
		_ = load.Wait()
		return sqlFail("Restoring the database failed.")
	}
	if err := load.Wait(); err != nil {
		return sqlFail("Restoring the database failed.")
	}
	return nil
}
