package handlers

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"whagent/internal/render"
	"whagent/internal/system"
)

// Databases (ADR 05): one MariaDB per node. Every database and user belongs to exactly one account and carries its name as a prefix ("<account>_"); the node refuses a name
// without it, so one account can never reach another's databases whatever it is asked. The plan's database count is checked here, where the databases really are. Passwords reach
// the engine only as a mysql_native_password hash; rows are only ever read, a page at a time, with long values cut.

const (
	maxCell       = 500
	dbConnections = 20 // simultaneous connections one database user may hold
)

// Column describes a table column.
type Column struct {
	Name, Type string
	Nullable   bool
	Primary    bool
	Default    *string
}

// Table is a table with its row count and size.
type Table struct {
	Name   string
	Rows   int64
	SizeKB int64
}

// Grant is what one user may do on one database.
type Grant struct {
	Database, User string
	Privileges     []string
}

// Engine is the database server.
type Engine interface {
	Databases(prefix string) ([]string, error)
	CreateDB(name string) error
	DropDB(name string) error
	UsersWith(prefix string) ([]string, error)
	CreateUser(name, hash string, maxConnections int) error
	SetHash(name, hash string) error
	DropUser(name string) error
	SetGrants(db, user string, privileges []string) error
	GrantsOf(prefix string) ([]Grant, error)
	SizeMB(db string) (float64, error)
	Tables(db string) ([]Table, error)
	Columns(db, table string) ([]Column, error)
	Rows(db, table string, offset, limit int) ([][]any, int64, error)
	Dump(db, path string) error
	Load(db, path string) error
	LoadSQL(db string, r io.Reader) error
}

// NativeHash is the mysql_native_password hash, so the clear password never has to be written into SQL.
func NativeHash(password string) string {
	first := sha1.Sum([]byte(password))
	second := sha1.Sum(first[:])
	return "*" + strings.ToUpper(hex.EncodeToString(second[:]))
}

var identRe = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,63}$`)

// Ident refuses anything that is not a plain database or user name before it can reach SQL.
func Ident(name string) (string, error) {
	if !identRe.MatchString(name) {
		return "", &system.Error{Msg: fmt.Sprintf("Refused database name %q.", name)}
	}
	return name, nil
}

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		return map[string]func(map[string]any) (Result, error){
			"DB_INVENTORY": a.dbInventory, "CREATE_DATABASE": a.dbCreate, "DELETE_DATABASE": a.dbDelete, "CREATE_DB_USER": a.dbCreateUser, "SET_DB_PASSWORD": a.dbSetPassword,
			"DELETE_DB_USER": a.dbDeleteUser, "GRANT_DB_ACCESS": a.dbGrant, "DB_TABLES": a.dbTables, "DB_DESCRIBE": a.dbDescribe, "DB_BROWSE": a.dbBrowse,
			"BACKUP_DATABASE": a.dbBackup, "RESTORE_DATABASE": a.dbRestore}
	})
}

func (a *Agent) dbLimit(account string) (int64, error) {
	st := a.stateRead(render.PathsOf(account, "").State)
	if st == nil {
		return 0, herr("not_found", "No such account on this node.")
	}
	if v, ok := st.Limits()["databases"]; ok {
		return v, nil
	}
	return render.Defaults["databases"], nil
}

func (a *Agent) dbOwn(account, name, kind string) error {
	if !strings.HasPrefix(name, account+"_") {
		return herr("forbidden", "A %s name must start with %s_.", kind, account)
	}
	return nil
}

func contains(list []string, item string) bool {
	for _, c := range list {
		if c == item {
			return true
		}
	}
	return false
}

func (a *Agent) dbOwned(account, name string) error {
	if err := a.dbOwn(account, name, "database"); err != nil {
		return err
	}
	list, err := a.Engine.Databases(account + "_")
	if err != nil {
		return err
	}
	if !contains(list, name) {
		return herr("not_found", "That database does not exist on this account.")
	}
	return nil
}

func (a *Agent) dbOwnedUser(account, name string) error {
	if err := a.dbOwn(account, name, "user"); err != nil {
		return err
	}
	list, err := a.Engine.UsersWith(account + "_")
	if err != nil {
		return err
	}
	if !contains(list, name) {
		return herr("not_found", "That database user does not exist on this account.")
	}
	return nil
}

func (a *Agent) dbInventory(p map[string]any) (Result, error) {
	account, prefix := str(p, "account"), str(p, "account")+"_"
	limit, err := a.dbLimit(account)
	if err != nil {
		return Result{}, err
	}
	grants, err := a.Engine.GrantsOf(prefix)
	if err != nil {
		return Result{}, err
	}
	users, _ := a.Engine.UsersWith(prefix)
	names, _ := a.Engine.Databases(prefix)
	dbs, urows, grows := []any{}, []any{}, []any{}
	for _, d := range names {
		size, _ := a.Engine.SizeMB(d)
		set := map[string]bool{}
		for _, g := range grants {
			if g.Database == d {
				set[g.User] = true
			}
		}
		dbs = append(dbs, map[string]any{"name": d, "size_mb": size, "users": sortedSet(set)})
	}
	for _, u := range users {
		set := map[string]bool{}
		for _, g := range grants {
			if g.User == u {
				set[g.Database] = true
			}
		}
		urows = append(urows, map[string]any{"name": u, "databases": sortedSet(set)})
	}
	for _, g := range grants {
		grows = append(grows, map[string]any{"database": g.Database, "user": g.User, "privileges": toAny(g.Privileges)})
	}
	return Result{"listed", map[string]any{"limit": limit, "databases": dbs, "users": urows, "grants": grows}}, nil
}

func sortedSet(set map[string]bool) []any {
	keys := make([]string, 0, len(set))
	for k := range set {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return toAny(keys)
}

func toAny(list []string) []any {
	out := make([]any, 0, len(list))
	for _, s := range list {
		out = append(out, s)
	}
	return out
}

func (a *Agent) dbCreate(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwn(account, name, "database"); err != nil {
		return Result{}, err
	}
	existing, err := a.Engine.Databases(account + "_")
	if err != nil {
		return Result{}, err
	}
	if contains(existing, name) {
		return Result{"exists", nil}, nil
	}
	limit, err := a.dbLimit(account)
	if err != nil {
		return Result{}, err
	}
	if int64(len(existing)) >= limit {
		return Result{}, herr("limit_reached", "Your plan's database limit has been reached.")
	}
	return Result{"created", nil}, a.Engine.CreateDB(name)
}

func (a *Agent) dbDelete(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwn(account, name, "database"); err != nil {
		return Result{}, err
	}
	existing, _ := a.Engine.Databases(account + "_")
	if !contains(existing, name) {
		return Result{"absent", nil}, nil
	}
	return Result{"deleted", nil}, a.Engine.DropDB(name)
}

func (a *Agent) dbCreateUser(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwn(account, name, "user"); err != nil {
		return Result{}, err
	}
	users, _ := a.Engine.UsersWith(account + "_")
	if contains(users, name) {
		return Result{}, herr("exists", "That database user already exists.")
	}
	return Result{"created", nil}, a.Engine.CreateUser(name, NativeHash(str(p, "password")), dbConnections) // one tenant cannot use every connection the server has
}

func (a *Agent) dbSetPassword(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwnedUser(account, name); err != nil {
		return Result{}, err
	}
	return Result{"changed", nil}, a.Engine.SetHash(name, NativeHash(str(p, "password")))
}

func (a *Agent) dbDeleteUser(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwn(account, name, "user"); err != nil {
		return Result{}, err
	}
	users, _ := a.Engine.UsersWith(account + "_")
	if !contains(users, name) {
		return Result{"absent", nil}, nil
	}
	return Result{"deleted", nil}, a.Engine.DropUser(name)
}

func (a *Agent) dbGrant(p map[string]any) (Result, error) {
	account := str(p, "account")
	if err := a.dbOwned(account, str(p, "database")); err != nil {
		return Result{}, err
	}
	if err := a.dbOwnedUser(account, str(p, "user")); err != nil {
		return Result{}, err
	}
	privs, _ := p["privileges"].([]string)
	return Result{"granted", nil}, a.Engine.SetGrants(str(p, "database"), str(p, "user"), privs)
}

func (a *Agent) dbTables(p map[string]any) (Result, error) {
	account, db := str(p, "account"), str(p, "database")
	if err := a.dbOwned(account, db); err != nil {
		return Result{}, err
	}
	tables, err := a.Engine.Tables(db)
	if err != nil {
		return Result{}, err
	}
	rows := []any{}
	for _, t := range tables {
		rows = append(rows, map[string]any{"name": t.Name, "rows": t.Rows, "size_kb": t.SizeKB})
	}
	return Result{"listed", map[string]any{"tables": rows}}, nil
}

func (a *Agent) dbTable(p map[string]any) (string, error) {
	account, db := str(p, "account"), str(p, "database")
	if err := a.dbOwned(account, db); err != nil {
		return "", err
	}
	tables, err := a.Engine.Tables(db)
	if err != nil {
		return "", err
	}
	for _, t := range tables {
		if t.Name == str(p, "table") {
			return db, nil
		}
	}
	return "", herr("not_found", "That table does not exist.")
}

func (a *Agent) dbDescribe(p map[string]any) (Result, error) {
	db, err := a.dbTable(p)
	if err != nil {
		return Result{}, err
	}
	columns, err := a.Engine.Columns(db, str(p, "table"))
	if err != nil {
		return Result{}, err
	}
	rows := []any{}
	for _, c := range columns {
		var def any
		if c.Default != nil {
			def = *c.Default
		}
		rows = append(rows, map[string]any{"name": c.Name, "type": c.Type, "nullable": c.Nullable, "primary_key": c.Primary, "default": def})
	}
	return Result{"listed", map[string]any{"columns": rows}}, nil
}

func (a *Agent) dbBrowse(p map[string]any) (Result, error) {
	db, err := a.dbTable(p)
	if err != nil {
		return Result{}, err
	}
	columns, err := a.Engine.Columns(db, str(p, "table"))
	if err != nil {
		return Result{}, err
	}
	rows, total, err := a.Engine.Rows(db, str(p, "table"), int(num(p, "offset")), int(num(p, "limit")))
	if err != nil {
		return Result{}, err
	}
	cut := [][]any{}
	for _, row := range rows {
		out := make([]any, 0, len(row))
		for _, v := range row {
			if v == nil {
				out = append(out, nil)
			} else {
				out = append(out, clip(fmt.Sprint(v), maxCell))
			}
		}
		cut = append(cut, out)
	}
	names, keys := []any{}, []any{}
	for _, c := range columns {
		names = append(names, c.Name)
		if c.Primary {
			keys = append(keys, c.Name)
		}
	}
	return Result{"listed", map[string]any{"columns": names, "rows": cut, "total": total, "key_columns": keys}}, nil
}

func (a *Agent) dbDump(account, name string) (string, error) {
	id := time.Now().UTC().Format("20060102T150405Z") + "-" + randomHex(4)
	dir := "/var/backups/whp/db/" + account
	path := dir + "/" + name + "-" + id + ".sql.zst"
	if err := a.Sys.Mkdir(dir, 0o700, "root", ""); err != nil {
		return "", err
	}
	if err := a.Engine.Dump(name, path); err != nil {
		return "", err
	}
	return id, a.Sys.Write(path+".ok", "", 0o600, "root", "") // a marker: only a dump that finished is ever restored
}

func (a *Agent) dbBackup(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwned(account, name); err != nil {
		return Result{}, err
	}
	id, err := a.dbDump(account, name)
	if err != nil {
		return Result{}, err
	}
	return Result{"created", map[string]any{"backup_id": id}}, nil
}

func (a *Agent) dbRestore(p map[string]any) (Result, error) {
	account, name := str(p, "account"), str(p, "name")
	if err := a.dbOwn(account, name, "database"); err != nil {
		return Result{}, err
	}
	path := "/var/backups/whp/db/" + account + "/" + name + "-" + str(p, "backup_id") + ".sql.zst"
	if !a.Sys.Exists(path + ".ok") {
		return Result{}, herr("not_found", "No such backup for this database.")
	}
	existing, _ := a.Engine.Databases(account + "_")
	data := map[string]any{}
	if contains(existing, name) {
		safety, err := a.dbDump(account, name) // the current contents are kept first, so a wrong restore can be undone
		if err != nil {
			return Result{}, err
		}
		data["safety_backup_id"] = safety
	}
	return Result{"restored", data}, a.Engine.Load(name, path)
}

// ---- the fake engine used by tests -------------------------------------------------------------------------------------------------------------

type fakeTable struct {
	Columns []Column
	Rows    [][]any
}

// FakeEngine keeps databases in memory, like the Python FakeMariadb.
type FakeEngine struct {
	DBs            map[string]map[string]*fakeTable
	Users          map[string]string
	MaxConnections map[string]int
	Grants         map[[2]string]map[string]bool
	Dumps          map[string]map[string]*fakeTable
}

// NewFakeEngine returns an empty engine.
func NewFakeEngine() *FakeEngine {
	return &FakeEngine{DBs: map[string]map[string]*fakeTable{}, Users: map[string]string{}, MaxConnections: map[string]int{}, Grants: map[[2]string]map[string]bool{}, Dumps: map[string]map[string]*fakeTable{}}
}

// AddTable is a test helper.
func (f *FakeEngine) AddTable(db, table string, columns []Column, rows [][]any) {
	f.DBs[db][table] = &fakeTable{columns, rows}
}

func prefixed(names []string, prefix string) []string {
	out := []string{}
	for _, n := range names {
		if strings.HasPrefix(n, prefix) {
			out = append(out, n)
		}
	}
	sort.Strings(out)
	return out
}

func keysOf[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func (f *FakeEngine) Databases(prefix string) ([]string, error) {
	return prefixed(keysOf(f.DBs), prefix), nil
}
func (f *FakeEngine) CreateDB(name string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	f.DBs[name] = map[string]*fakeTable{}
	return nil
}
func (f *FakeEngine) DropDB(name string) error {
	delete(f.DBs, name)
	for k := range f.Grants {
		if k[0] == name {
			delete(f.Grants, k)
		}
	}
	return nil
}
func (f *FakeEngine) UsersWith(prefix string) ([]string, error) {
	return prefixed(keysOf(f.Users), prefix), nil
}
func (f *FakeEngine) CreateUser(name, hash string, maxConnections int) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	f.Users[name], f.MaxConnections[name] = hash, maxConnections
	return nil
}
func (f *FakeEngine) SetHash(name, hash string) error {
	if _, err := Ident(name); err != nil {
		return err
	}
	f.Users[name] = hash
	return nil
}
func (f *FakeEngine) DropUser(name string) error {
	delete(f.Users, name)
	for k := range f.Grants {
		if k[1] == name {
			delete(f.Grants, k)
		}
	}
	return nil
}
func (f *FakeEngine) SetGrants(db, user string, privileges []string) error {
	if len(privileges) == 0 {
		delete(f.Grants, [2]string{db, user})
		return nil
	}
	set := map[string]bool{}
	for _, p := range privileges {
		set[p] = true
	}
	f.Grants[[2]string{db, user}] = set
	return nil
}
func (f *FakeEngine) GrantsOf(prefix string) ([]Grant, error) {
	var out []Grant
	for k, set := range f.Grants {
		if strings.HasPrefix(k[0], prefix) {
			privs := keysOf(set)
			sort.Strings(privs)
			out = append(out, Grant{k[0], k[1], privs})
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Database != out[j].Database {
			return out[i].Database < out[j].Database
		}
		return out[i].User < out[j].User
	})
	return out, nil
}
func (f *FakeEngine) SizeMB(db string) (float64, error) {
	rows := 0
	for _, t := range f.DBs[db] {
		rows += len(t.Rows)
	}
	return math.Round(float64(rows)*0.01*100) / 100, nil
}
func (f *FakeEngine) Tables(db string) ([]Table, error) {
	out := []Table{}
	for _, name := range sortedKeys2(f.DBs[db]) {
		n := int64(len(f.DBs[db][name].Rows))
		size := n
		if size < 16 {
			size = 16
		}
		out = append(out, Table{name, n, size})
	}
	return out, nil
}
func sortedKeys2[V any](m map[string]V) []string {
	keys := keysOf(m)
	sort.Strings(keys)
	return keys
}
func (f *FakeEngine) Columns(db, table string) ([]Column, error) {
	return f.DBs[db][table].Columns, nil
}
func (f *FakeEngine) Rows(db, table string, offset, limit int) ([][]any, int64, error) {
	data := f.DBs[db][table].Rows
	end := offset + limit
	if offset > len(data) {
		offset = len(data)
	}
	if end > len(data) {
		end = len(data)
	}
	return data[offset:end], int64(len(data)), nil
}
func (f *FakeEngine) Dump(db, path string) error {
	snap := map[string]*fakeTable{}
	for name, t := range f.DBs[db] {
		rows := make([][]any, len(t.Rows))
		for i, r := range t.Rows {
			rows[i] = append([]any{}, r...)
		}
		snap[name] = &fakeTable{append([]Column{}, t.Columns...), rows}
	}
	f.Dumps[path] = snap
	if info, err := os.Stat(filepath.Dir(path)); err == nil && info.IsDir() { // a real folder (backup staging): the dump is a real file, so it travels with the backup
		raw, _ := json.Marshal(snap)
		return os.WriteFile(path, raw, 0o600)
	}
	return nil
}
func (f *FakeEngine) Load(db, path string) error {
	snap := f.Dumps[path]
	if raw, err := os.ReadFile(path); err == nil {
		snap = map[string]*fakeTable{}
		if err := json.Unmarshal(raw, &snap); err != nil {
			return &system.Error{Msg: "The dump could not be read."}
		}
	}
	if snap == nil {
		return &system.Error{Msg: "No such dump."}
	}
	f.DBs[db] = snap
	return nil
}
