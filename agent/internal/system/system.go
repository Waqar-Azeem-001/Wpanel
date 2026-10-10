// Package system is the agent's only way to touch the machine. Every handler works through the System interface, so the same code is tested against Fake
// (files in memory, every command recorded) and run for real with Real. Both obey the same rules: a program is started from an argument list and never through
// a shell, and a path must be absolute, clean and inside the places the agent is allowed to touch.
package system

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// AllowedRoots are the only places the agent may write, and ReadOnlyRoots the places it may only read.
var (
	AllowedRoots  = []string{"/home/", "/etc/", "/var/lib/whp/", "/var/log/whp/", "/var/backups/whp/", "/run/whp/", "/srv/whp/", "/var/mail/"}
	ReadOnlyRoots = []string{"/proc/", "/sys/fs/cgroup/"}
)

// Error is a failure on the machine.
type Error struct{ Msg string }

func (e *Error) Error() string { return e.Msg }

func fail(format string, a ...any) error { return &Error{fmt.Sprintf(format, a...)} }

// System is what handlers use.
type System interface {
	Exists(p string) bool
	Read(p string) (string, bool)
	Write(p, content string, mode os.FileMode, owner, group string) error
	Mkdir(p string, mode os.FileMode, owner, group string) error
	Remove(p string) error
	List(prefix string) []string
	UID(user string) (int, bool)
	Run(argv []string, input string) (string, error)
	Probe(argv []string) (int, string)
}

// CheckPath refuses anything that is not a clean absolute path inside an allowed place.
func CheckPath(p string, readOnly bool) (string, error) {
	clean := path.Clean(p)
	if !strings.HasPrefix(clean, "/") || clean != strings.TrimRight(p, "/") || strings.Contains(p, "\x00") {
		return "", fail("Refused path %q.", p)
	}
	for _, part := range strings.Split(p, "/") {
		if part == ".." {
			return "", fail("Refused path %q.", p)
		}
	}
	roots := AllowedRoots
	if readOnly {
		roots = append(append([]string{}, AllowedRoots...), ReadOnlyRoots...)
	}
	for _, root := range roots {
		if strings.HasPrefix(clean+"/", root) {
			return clean, nil
		}
	}
	return "", fail("Path %q is outside the places the agent may touch.", p)
}

func checkArgv(argv []string) error {
	if len(argv) == 0 {
		return fail("A command must be a list of strings.")
	}
	return nil
}

// Mode renders a permission the way the Python agent logs it (0o644), so command logs can be compared across the two.
func Mode(m os.FileMode) string { return fmt.Sprintf("0o%o", uint32(m.Perm())|specialBits(m)) }

func specialBits(m os.FileMode) uint32 {
	var bits uint32
	if m&os.ModeSetuid != 0 {
		bits |= 0o4000
	}
	if m&os.ModeSetgid != 0 {
		bits |= 0o2000
	}
	if m&os.ModeSticky != 0 {
		bits |= 0o1000
	}
	return bits
}

// FileMode converts an octal number such as 0o2750 to the os.FileMode that carries its special bits.
func FileMode(octal uint32) os.FileMode {
	m := os.FileMode(octal & 0o777)
	if octal&0o4000 != 0 {
		m |= os.ModeSetuid
	}
	if octal&0o2000 != 0 {
		m |= os.ModeSetgid
	}
	if octal&0o1000 != 0 {
		m |= os.ModeSticky
	}
	return m
}

// ---- Fake ---------------------------------------------------------------------------------------------------------------------------------

// Fake is a pretend machine. Commands records every write, mkdir, remove and program run, in order.
type Fake struct {
	Files        map[string]string
	Dirs         map[string]bool
	Users        map[string]int
	Commands     [][]string
	NextUID      int
	FailOn       int // make the command at this position in Commands fail (-1: never)
	ServiceState map[string]string
	Probes       map[string]string // joined argv -> output
	Inputs       [][2]string       // (program, standard input) of runs that were given input, kept apart from Commands so passwords never appear in the log
}

// NewFake returns an empty pretend machine.
func NewFake() *Fake {
	return &Fake{Files: map[string]string{}, Dirs: map[string]bool{}, Users: map[string]int{}, NextUID: 2000, FailOn: -1, ServiceState: map[string]string{}, Probes: map[string]string{}}
}

func (f *Fake) log(parts ...string) { f.Commands = append(f.Commands, parts) }

// Exists reports whether a file or folder is there.
func (f *Fake) Exists(p string) bool {
	c, err := CheckPath(p, false)
	if err != nil {
		return false
	}
	_, file := f.Files[c]
	return file || f.Dirs[c]
}

// Read returns a file's content.
func (f *Fake) Read(p string) (string, bool) {
	c, err := CheckPath(p, true)
	if err != nil {
		return "", false
	}
	v, ok := f.Files[c]
	return v, ok
}

// Write stores a file.
func (f *Fake) Write(p, content string, mode os.FileMode, owner, group string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	f.Files[c] = content
	entry := []string{"write", p, Mode(mode), owner}
	if group != "" {
		entry = append(entry, group)
	}
	f.log(entry...)
	return nil
}

// Mkdir records a folder.
func (f *Fake) Mkdir(p string, mode os.FileMode, owner, group string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	f.Dirs[c] = true
	entry := []string{"mkdir", p, Mode(mode), owner}
	if group != "" {
		entry = append(entry, group)
	}
	f.log(entry...)
	return nil
}

// Remove deletes a file or everything under a folder.
func (f *Fake) Remove(p string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	for k := range f.Files {
		if k == c || strings.HasPrefix(k, c+"/") {
			delete(f.Files, k)
		}
	}
	for k := range f.Dirs {
		if k == c || strings.HasPrefix(k, c+"/") {
			delete(f.Dirs, k)
		}
	}
	f.log("remove", p)
	return nil
}

// List returns the files under a folder.
func (f *Fake) List(prefix string) []string {
	c, err := CheckPath(strings.TrimRight(prefix, "/"), false)
	if err != nil {
		return nil
	}
	var out []string
	for k := range f.Files {
		if strings.HasPrefix(k, c+"/") {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}

// UID returns a user's id.
func (f *Fake) UID(name string) (int, bool) { v, ok := f.Users[name]; return v, ok }

// Run pretends to run a program.
func (f *Fake) Run(argv []string, input string) (string, error) {
	if err := checkArgv(argv); err != nil {
		return "", err
	}
	if f.FailOn >= 0 && len(f.Commands) == f.FailOn {
		f.log(append([]string{"FAILED"}, argv...)...)
		return "", fail("%s failed", argv[0])
	}
	f.log(argv...)
	if input != "" {
		f.Inputs = append(f.Inputs, [2]string{argv[0], input})
	}
	switch argv[0] {
	case "useradd":
		f.Users[argv[len(argv)-1]] = f.NextUID
		f.NextUID++
	case "userdel":
		name := argv[len(argv)-1]
		delete(f.Users, name)
		for _, a := range argv {
			if a == "--remove" {
				for k := range f.Files {
					if strings.HasPrefix(k, "/home/"+name+"/") {
						delete(f.Files, k)
					}
				}
				for k := range f.Dirs {
					if k == "/home/"+name || strings.HasPrefix(k, "/home/"+name+"/") {
						delete(f.Dirs, k)
					}
				}
			}
		}
	}
	return "", nil
}

// Probe answers a question that may have a "no" answer.
func (f *Fake) Probe(argv []string) (int, string) {
	if checkArgv(argv) != nil {
		return 127, ""
	}
	if len(argv) >= 3 && argv[0] == "systemctl" && argv[1] == "is-active" {
		state, ok := f.ServiceState[argv[2]]
		if !ok {
			state = "inactive"
		}
		if state == "active" {
			return 0, state
		}
		return 3, state
	}
	if out, ok := f.Probes[strings.Join(argv, " ")]; ok {
		return 0, out
	}
	return 127, ""
}

// ---- Real ---------------------------------------------------------------------------------------------------------------------------------

// Real works on the actual machine. It has never been run on a real node.
type Real struct{}

// Exists reports whether the path exists.
func (Real) Exists(p string) bool {
	c, err := CheckPath(p, false)
	if err != nil {
		return false
	}
	_, err = os.Stat(c)
	return err == nil
}

// Read returns a file's content.
func (Real) Read(p string) (string, bool) {
	c, err := CheckPath(p, true)
	if err != nil {
		return "", false
	}
	raw, err := os.ReadFile(c)
	if err != nil {
		return "", false
	}
	return string(raw), true
}

func chown(owner, group, p string) error {
	if owner == "root" && group == "" {
		return nil
	}
	if group == "" {
		group = owner
	}
	if out, err := exec.Command("chown", owner+":"+group, p).CombinedOutput(); err != nil {
		return fail("chown failed: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

// Write stores a file so that a half-written file is never visible.
func (Real) Write(p, content string, mode os.FileMode, owner, group string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(c), 0o755); err != nil {
		return fail("could not create the folder: %v", err)
	}
	tmp := c + ".whp-tmp"
	if err := os.WriteFile(tmp, []byte(content), 0o600); err != nil {
		return fail("could not write: %v", err)
	}
	if err := os.Chmod(tmp, mode); err != nil {
		return fail("could not set the mode: %v", err)
	}
	if err := chown(owner, group, tmp); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, c)
}

// Mkdir makes a folder with exactly this mode and owner.
func (Real) Mkdir(p string, mode os.FileMode, owner, group string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(c, 0o755); err != nil {
		return fail("could not create the folder: %v", err)
	}
	if err := chown(owner, group, c); err != nil {
		return err
	}
	return os.Chmod(c, mode) // after chown, which can clear the setgid bit
}

// Remove deletes recursively, refusing the top-level folders.
func (Real) Remove(p string) error {
	c, err := CheckPath(p, false)
	if err != nil {
		return err
	}
	if c == "/home" || c == "/etc" || c == "/var/lib/whp" || strings.Count(c, "/") < 3 {
		return fail("Refusing to remove a top-level folder.")
	}
	if out, err := exec.Command("rm", "-rf", "--one-file-system", "--", c).CombinedOutput(); err != nil {
		return fail("rm failed: %s", strings.TrimSpace(string(out)))
	}
	return nil
}

// List returns every file under a folder.
func (Real) List(prefix string) []string {
	c, err := CheckPath(strings.TrimRight(prefix, "/"), false)
	if err != nil {
		return nil
	}
	var out []string
	_ = filepath.WalkDir(c, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			out = append(out, filepath.ToSlash(p))
		}
		return nil
	})
	sort.Strings(out)
	return out
}

// UID returns a user's id.
func (Real) UID(name string) (int, bool) {
	u, err := user.Lookup(name)
	if err != nil {
		return 0, false
	}
	n, err := strconv.Atoi(u.Uid)
	return n, err == nil
}

// Run starts a program from an argument list.
func (Real) Run(argv []string, input string) (string, error) {
	if err := checkArgv(argv); err != nil {
		return "", err
	}
	cmd := exec.Command(argv[0], argv[1:]...)
	if input != "" {
		cmd.Stdin = strings.NewReader(input)
	}
	out, err := cmd.CombinedOutput()
	if err != nil {
		last := strings.TrimSpace(string(out))
		if i := strings.LastIndex(last, "\n"); i >= 0 {
			last = last[i+1:]
		}
		if len(last) > 200 {
			last = last[:200]
		}
		return "", fail("%s failed: %s", argv[0], last)
	}
	return string(out), nil
}

// Probe asks a question and returns the exit status instead of failing.
func (Real) Probe(argv []string) (int, string) {
	if checkArgv(argv) != nil {
		return 127, ""
	}
	out, err := exec.Command(argv[0], argv[1:]...).Output()
	if err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode(), strings.TrimSpace(string(out))
		}
		return 127, ""
	}
	return 0, strings.TrimSpace(string(out))
}
