// Package sandbox is one account's folder and the only way file commands reach it. Paths are relative to the folder; "..", absolute paths, backslashes, colons and control
// characters are refused outright; no link is ever followed; the folder itself cannot be renamed, copied or deleted. This is the Go version of WHPanel's Python sandbox and its messages
// are the same, because they are shown to customers.
//
// Everything here must run as the account's own Linux user (see the agent's file worker), never as root: a link swapped in between a check and its use can then reach nothing but the
// account's own files.
package sandbox

import (
	"archive/zip"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

// Limits shared with the Python sandbox.
const (
	MaxPath         = 1024
	MaxName         = 255
	MaxSearch       = 200
	MaxArchiveFiles = 5000
	MaxArchiveBytes = 200_000_000
	MaxSearchWalk   = 5000
	TrashDir        = ".trash"
	trashIndex      = ".index.json"
)

// Error carries a plain-language message for the customer.
type Error struct{ Msg string }

func (e *Error) Error() string { return e.Msg }

func fail(msg string) error { return &Error{msg} }

// Entry is one file or folder.
type Entry struct {
	Name        string `json:"name"`
	Path        string `json:"path"`
	IsDir       bool   `json:"is_dir"`
	SizeBytes   *int64 `json:"size_bytes"`
	ModifiedAt  string `json:"modified_at"`
	Permissions string `json:"permissions"`
}

// TrashItem is something in the trash.
type TrashItem struct {
	ItemID       string `json:"item_id"`
	Name         string `json:"name"`
	OriginalPath string `json:"original_path"`
	IsDir        bool   `json:"is_dir"`
	DeletedAt    string `json:"deleted_at"`
	SizeBytes    *int64 `json:"size_bytes"`
}

// Sandbox is the account's folder.
type Sandbox struct{ root string }

// New opens an existing folder. It never creates one: a missing folder means there is no such account.
func New(root string) (*Sandbox, error) {
	real, err := filepath.EvalSymlinks(root)
	if err != nil {
		return nil, fail("No such account folder.")
	}
	info, err := os.Stat(real)
	if err != nil || !info.IsDir() {
		return nil, fail("No such account folder.")
	}
	return &Sandbox{root: real}, nil
}

func hasControl(s string) bool {
	for _, r := range s {
		if r < 32 || r == 127 {
			return true
		}
	}
	return false
}

// CleanRelative is a path made of plain parts, relative to the folder. "" is the folder itself.
func CleanRelative(rel string) (string, error) {
	if utf8.RuneCountInString(rel) > MaxPath {
		return "", fail("That path is too long.")
	}
	if hasControl(rel) {
		return "", fail("That path contains characters that are not allowed.")
	}
	if strings.Contains(rel, `\`) || strings.Contains(rel, ":") {
		return "", fail("Use forward slashes and no drive letters in a path.")
	}
	if strings.HasPrefix(rel, "/") {
		return "", fail("Paths are relative to your website's folder.")
	}
	var parts []string
	for _, p := range strings.Split(rel, "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." {
			return "", fail("A path cannot go up a folder.")
		}
		if utf8.RuneCountInString(p) > MaxName {
			return "", fail("A name in that path is too long.")
		}
	}
	return strings.Join(parts, "/"), nil
}

// CleanName is a plain file or folder name.
func CleanName(name string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, `/\:`) {
		return "", fail("Enter a plain file or folder name (no slashes).")
	}
	if hasControl(name) || utf8.RuneCountInString(name) > MaxName {
		return "", fail("That name is not allowed.")
	}
	return name, nil
}

var modeRe = regexp.MustCompile(`^[0-7]{3,4}$`)

// CheckMode validates a permission such as 644 and returns it as four digits.
func CheckMode(mode string, isDir bool) (string, error) {
	mode = strings.TrimSpace(mode)
	if !modeRe.MatchString(mode) {
		return "", fail("Use a permission such as 644 or 755.")
	}
	digits := strings.Repeat("0", 4-len(mode)) + mode
	if digits[0] != '0' {
		return "", fail("Special permission bits (setuid, setgid, sticky) are not allowed here.")
	}
	owner, other := int(digits[1]-'0'), int(digits[3]-'0')
	if other&2 != 0 {
		return "", fail("Anyone-can-write permissions are not allowed: they let any other user on the server change your files.")
	}
	if isDir && owner != 7 {
		return "", fail("A folder needs full access for you (7 first), or you could lock yourself out of it.")
	}
	if !isDir && owner&6 != 6 {
		return "", fail("A file must stay readable and writable by you (6 or 7 first).")
	}
	return digits, nil
}

func lastPart(rel string) string {
	if i := strings.LastIndex(rel, "/"); i >= 0 {
		return rel[i+1:]
	}
	return rel
}

func parentOf(rel string) string {
	if i := strings.LastIndex(rel, "/"); i >= 0 {
		return rel[:i]
	}
	return ""
}

// Resolve turns a relative path into a real one, refusing links and anything outside the folder.
func (s *Sandbox) Resolve(rel string, mustExist bool) (string, error) {
	rel, err := CleanRelative(rel)
	if err != nil {
		return "", err
	}
	parts := []string{}
	if rel != "" {
		parts = strings.Split(rel, "/")
	}
	if len(parts) > 0 && parts[0] == TrashDir {
		return "", fail("That folder is reserved for the trash.")
	}
	current := s.root
	for _, p := range parts {
		current = filepath.Join(current, p)
		if info, err := os.Lstat(current); err == nil && info.Mode()&os.ModeSymlink != 0 {
			return "", fail("Links are not followed inside your website's folder.")
		}
	}
	if mustExist {
		if _, err := os.Lstat(current); err != nil {
			return "", fail("That file or folder does not exist.")
		}
	}
	return current, nil
}

// Relative is the path of an absolute location inside the folder, "" for the folder itself.
func (s *Sandbox) Relative(abs string) string {
	rel, err := filepath.Rel(s.root, abs)
	if err != nil || rel == "." {
		return ""
	}
	return filepath.ToSlash(rel)
}

func (s *Sandbox) entry(abs string) (Entry, error) {
	info, err := os.Stat(abs)
	if err != nil {
		return Entry{}, err
	}
	e := Entry{Name: filepath.Base(abs), Path: s.Relative(abs), IsDir: info.IsDir(), ModifiedAt: info.ModTime().UTC().Format("2006-01-02T15:04:05.000000+00:00"),
		Permissions: fmt.Sprintf("0%03o", info.Mode().Perm())}
	if !info.IsDir() {
		n := info.Size()
		e.SizeBytes = &n
	}
	return e, nil
}

func sortEntries(entries []Entry, byPath bool) {
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].IsDir != entries[j].IsDir {
			return entries[i].IsDir
		}
		a, b := entries[i].Name, entries[j].Name
		if byPath {
			a, b = entries[i].Path, entries[j].Path
		}
		return strings.ToLower(a) < strings.ToLower(b)
	})
}

// List is the entries directly inside a folder (links and the trash are left out).
func (s *Sandbox) List(rel string) ([]Entry, error) {
	folder, err := s.Resolve(rel, true)
	if err != nil {
		return nil, err
	}
	if info, _ := os.Stat(folder); info == nil || !info.IsDir() {
		return nil, fail("That is a file, not a folder.")
	}
	items, err := os.ReadDir(folder)
	if err != nil {
		return nil, fail("That folder cannot be read.")
	}
	out := []Entry{}
	for _, it := range items {
		if it.Type()&fs.ModeSymlink != 0 || (folder == s.root && it.Name() == TrashDir) {
			continue
		}
		if e, err := s.entry(filepath.Join(folder, it.Name())); err == nil {
			out = append(out, e)
		}
	}
	sortEntries(out, false)
	return out, nil
}

// Read returns a file's content, refusing one larger than max.
func (s *Sandbox) Read(rel string, max int64) ([]byte, error) {
	p, err := s.Resolve(rel, true)
	if err != nil {
		return nil, err
	}
	info, _ := os.Stat(p)
	if info == nil || info.IsDir() {
		return nil, fail("That is a folder, not a file.")
	}
	if info.Size() > max {
		return nil, fail("That file is too large to open here.")
	}
	return os.ReadFile(p)
}

func (s *Sandbox) parentDir(rel string, what string) (string, error) {
	parent, err := s.Resolve(parentOf(rel), true)
	if err != nil {
		return "", err
	}
	if info, _ := os.Stat(parent); info == nil || !info.IsDir() {
		return "", fail(what)
	}
	return parent, nil
}

// Write stores a file, refusing to overwrite unless asked.
func (s *Sandbox) Write(rel string, content []byte, overwrite bool) error {
	rel, err := CleanRelative(rel)
	if err != nil {
		return err
	}
	if rel == "" {
		return fail("Choose a file name.")
	}
	if _, err := CleanName(lastPart(rel)); err != nil {
		return err
	}
	if _, err := s.parentDir(rel, "The folder for that file does not exist."); err != nil {
		return err
	}
	target, err := s.Resolve(rel, false)
	if err != nil {
		return err
	}
	if info, err := os.Stat(target); err == nil {
		if info.IsDir() {
			return fail("A folder with that name already exists.")
		}
		if !overwrite {
			return fail("A file with that name already exists.")
		}
	}
	return os.WriteFile(target, content, 0o644)
}

// Mkdir makes a folder.
func (s *Sandbox) Mkdir(rel string) error {
	rel, err := CleanRelative(rel)
	if err != nil {
		return err
	}
	if rel == "" {
		return fail("Choose a folder name.")
	}
	if _, err := CleanName(lastPart(rel)); err != nil {
		return err
	}
	if _, err := s.parentDir(rel, "The parent folder does not exist."); err != nil {
		return err
	}
	target, err := s.Resolve(rel, false)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(target); err == nil {
		return fail("Something with that name already exists.")
	}
	return os.Mkdir(target, 0o755)
}

func (s *Sandbox) twoPaths(rel, newRel, rootMsg, emptyMsg string) (string, string, error) {
	source, err := s.Resolve(rel, true)
	if err != nil {
		return "", "", err
	}
	if source == s.root {
		return "", "", fail(rootMsg)
	}
	newRel, err = CleanRelative(newRel)
	if err != nil {
		return "", "", err
	}
	if newRel == "" {
		return "", "", fail(emptyMsg)
	}
	if _, err := CleanName(lastPart(newRel)); err != nil {
		return "", "", err
	}
	dest, err := s.Resolve(newRel, false)
	if err != nil {
		return "", "", err
	}
	if _, err := os.Lstat(dest); err == nil {
		return "", "", fail("Something with that name already exists.")
	}
	if info, _ := os.Stat(filepath.Dir(dest)); info == nil || !info.IsDir() {
		return "", "", fail("The destination folder does not exist.")
	}
	return source, dest, nil
}

func inside(parent, child string) bool {
	rel, err := filepath.Rel(parent, child)
	return err == nil && (rel == "." || (!strings.HasPrefix(rel, "..") && rel != ""))
}

// Rename moves a file or folder.
func (s *Sandbox) Rename(rel, newRel string) error {
	source, dest, err := s.twoPaths(rel, newRel, "Your website's main folder cannot be renamed.", "Choose a new name.")
	if err != nil {
		return err
	}
	if info, _ := os.Stat(source); info != nil && info.IsDir() && inside(source, dest) {
		return fail("A folder cannot be moved into itself.")
	}
	return os.Rename(source, dest)
}

func copyTree(src, dst string) error {
	info, err := os.Lstat(src)
	if err != nil {
		return err
	}
	if info.Mode()&os.ModeSymlink != 0 {
		return nil // links are never copied
	}
	if info.IsDir() {
		if err := os.MkdirAll(dst, info.Mode().Perm()|0o700); err != nil {
			return err
		}
		items, err := os.ReadDir(src)
		if err != nil {
			return err
		}
		for _, it := range items {
			if err := copyTree(filepath.Join(src, it.Name()), filepath.Join(dst, it.Name())); err != nil {
				return err
			}
		}
		return nil
	}
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_EXCL, info.Mode().Perm()|0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(out, in); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}

// Copy duplicates a file or folder (links inside are skipped).
func (s *Sandbox) Copy(rel, newRel string) error {
	source, dest, err := s.twoPaths(rel, newRel, "Your website's main folder cannot be copied.", "Choose where to copy it.")
	if err != nil {
		return err
	}
	if info, _ := os.Stat(source); info != nil && info.IsDir() && inside(source, dest) {
		return fail("A folder cannot be copied into itself.")
	}
	return copyTree(source, dest)
}

// Delete removes a file or folder for good.
func (s *Sandbox) Delete(rel string) error {
	target, err := s.Resolve(rel, true)
	if err != nil {
		return err
	}
	if target == s.root {
		return fail("Your website's main folder cannot be deleted.")
	}
	return os.RemoveAll(target)
}

// Chmod sets a permission after the safety rules.
func (s *Sandbox) Chmod(rel, mode string) error {
	target, err := s.Resolve(rel, true)
	if err != nil {
		return err
	}
	if target == s.root {
		return fail("The permissions of your website's main folder cannot be changed.")
	}
	info, _ := os.Stat(target)
	digits, err := CheckMode(mode, info != nil && info.IsDir())
	if err != nil {
		return err
	}
	var n uint32
	fmt.Sscanf(digits, "%o", &n)
	return os.Chmod(target, os.FileMode(n&0o777))
}

// Search finds entries whose NAME contains the query, anywhere under a folder (capped).
func (s *Sandbox) Search(rel, query string) ([]Entry, error) {
	query = strings.ToLower(strings.TrimSpace(query))
	if query == "" {
		return nil, fail("Type something to search for.")
	}
	base, err := s.Resolve(rel, true)
	if err != nil {
		return nil, err
	}
	if info, _ := os.Stat(base); info == nil || !info.IsDir() {
		return nil, fail("Search inside a folder.")
	}
	found, visited := []Entry{}, 0
	var walk func(dir string) bool
	walk = func(dir string) bool {
		items, err := os.ReadDir(dir)
		if err != nil {
			return false
		}
		var folders []string
		for _, it := range items {
			if it.Type()&fs.ModeSymlink != 0 || (it.IsDir() && it.Name() == TrashDir) {
				continue
			}
			visited++
			full := filepath.Join(dir, it.Name())
			if strings.Contains(strings.ToLower(it.Name()), query) {
				if e, err := s.entry(full); err == nil {
					found = append(found, e)
				}
			}
			if len(found) >= MaxSearch || visited >= MaxSearchWalk {
				return true
			}
			if it.IsDir() {
				folders = append(folders, full)
			}
		}
		for _, f := range folders {
			if walk(f) {
				return true
			}
		}
		return false
	}
	walk(base)
	sortEntries(found, true)
	return found, nil
}

// Archive zips files and folders into one new .zip (never overwriting anything). It returns the number of entries.
func (s *Sandbox) Archive(rels []string, archiveRel string) (int, error) {
	archiveRel, err := CleanRelative(archiveRel)
	if err != nil {
		return 0, err
	}
	if archiveRel == "" || !strings.HasSuffix(strings.ToLower(archiveRel), ".zip") {
		return 0, fail("Name the archive with a .zip ending.")
	}
	if _, err := CleanName(lastPart(archiveRel)); err != nil {
		return 0, err
	}
	target, err := s.Resolve(archiveRel, false)
	if err != nil {
		return 0, err
	}
	if _, err := os.Lstat(target); err == nil {
		return 0, fail("Something with that name already exists.")
	}
	if info, _ := os.Stat(filepath.Dir(target)); info == nil || !info.IsDir() {
		return 0, fail("The destination folder does not exist.")
	}
	var sources []string
	for _, rel := range rels {
		src, err := s.Resolve(rel, true)
		if err != nil {
			return 0, err
		}
		if src == s.root {
			return 0, fail("Your website's main folder cannot be archived as a whole; pick what is inside it.")
		}
		sources = append(sources, src)
	}
	if len(sources) == 0 {
		return 0, fail("Choose what to put in the archive.")
	}
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		return 0, err
	}
	zw := zip.NewWriter(out)
	count, total := 0, int64(0)
	cleanup := func(e error) (int, error) {
		zw.Close()
		out.Close()
		os.Remove(target)
		return 0, e
	}
	trash := filepath.Join(s.root, TrashDir)
	for _, src := range sources {
		base := filepath.Dir(src)
		var walkErr error
		_ = filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
			if err != nil || walkErr != nil {
				return nil
			}
			if d.Type()&fs.ModeSymlink != 0 || p == target || inside(trash, p) {
				if d.IsDir() {
					return filepath.SkipDir
				}
				return nil
			}
			count++
			if count > MaxArchiveFiles {
				walkErr = fail("That is too many files for one archive.")
				return nil
			}
			name, _ := filepath.Rel(base, p)
			name = filepath.ToSlash(name)
			if d.IsDir() {
				if _, err := zw.Create(name + "/"); err != nil {
					walkErr = err
				}
				return nil
			}
			info, err := d.Info()
			if err != nil {
				return nil
			}
			total += info.Size()
			if total > MaxArchiveBytes {
				walkErr = fail("Those files are too large for one archive.")
				return nil
			}
			header, _ := zip.FileInfoHeader(info)
			header.Name, header.Method = name, zip.Deflate
			w, err := zw.CreateHeader(header)
			if err != nil {
				walkErr = err
				return nil
			}
			in, err := os.Open(p)
			if err != nil {
				walkErr = err
				return nil
			}
			_, walkErr = io.Copy(w, in)
			in.Close()
			return nil
		})
		if walkErr != nil {
			return cleanup(walkErr)
		}
	}
	if err := zw.Close(); err != nil {
		return cleanup(err)
	}
	if err := out.Close(); err != nil {
		os.Remove(target)
		return 0, err
	}
	return count, nil
}

// Extract unpacks a .zip into a folder (created if missing): all or nothing, nothing overwritten, nothing unsafe.
func (s *Sandbox) Extract(rel, destRel string) (int, error) {
	source, err := s.Resolve(rel, true)
	if err != nil {
		return 0, err
	}
	zr, zerr := zip.OpenReader(source)
	if info, _ := os.Stat(source); info == nil || info.IsDir() || !strings.HasSuffix(strings.ToLower(filepath.Base(source)), ".zip") || zerr != nil {
		return 0, fail("That is not a .zip archive.")
	}
	defer zr.Close()
	destRel, err = CleanRelative(destRel)
	if err != nil {
		return 0, err
	}
	destination, err := s.Resolve(destRel, false)
	if err != nil {
		return 0, err
	}
	if info, err := os.Stat(destination); err == nil && !info.IsDir() {
		return 0, fail("Something with that name already exists.")
	}
	if info, _ := os.Stat(filepath.Dir(destination)); info == nil || !info.IsDir() {
		return 0, fail("The destination folder does not exist.")
	}
	if len(zr.File) > MaxArchiveFiles {
		return 0, fail("That archive has too many files.")
	}
	var sum uint64
	for _, f := range zr.File {
		sum += f.UncompressedSize64
	}
	if sum > MaxArchiveBytes {
		return 0, fail("That archive is too large to unpack here.")
	}
	type item struct {
		file   *zip.File
		target string
		isDir  bool
	}
	var plan []item
	for _, f := range zr.File {
		name := strings.ReplaceAll(f.Name, `\`, "/")
		isDir := strings.HasSuffix(name, "/")
		member, err := CleanRelative(strings.TrimRight(name, "/"))
		if err != nil {
			return 0, fail("That archive contains an unsafe path, so it was not unpacked.")
		}
		if member == "" {
			continue
		}
		if strings.Split(member, "/")[0] == TrashDir || f.Mode()&os.ModeSymlink != 0 {
			return 0, fail("That archive contains something that is not allowed, so it was not unpacked.")
		}
		targetRel := member
		if destRel != "" {
			targetRel = destRel + "/" + member
		}
		target, err := s.Resolve(targetRel, false)
		if err != nil {
			return 0, err
		}
		if _, err := os.Lstat(target); err == nil && !isDir {
			return 0, fail(fmt.Sprintf("'%s' already exists, so nothing was unpacked.", member))
		}
		plan = append(plan, item{f, target, isDir})
	}
	if err := os.MkdirAll(destination, 0o755); err != nil {
		return 0, err
	}
	for _, it := range plan {
		if it.isDir {
			if err := os.MkdirAll(it.target, 0o755); err != nil {
				return 0, err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(it.target), 0o755); err != nil {
			return 0, err
		}
		in, err := it.file.Open()
		if err != nil {
			return 0, err
		}
		out, err := os.OpenFile(it.target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err != nil {
			in.Close()
			return 0, err
		}
		_, err = io.Copy(out, io.LimitReader(in, MaxArchiveBytes+1))
		in.Close()
		out.Close()
		if err != nil {
			return 0, err
		}
	}
	return len(plan), nil
}

// ---- trash --------------------------------------------------------------------------------------------------------------------------------

func (s *Sandbox) trashPath() string { return filepath.Join(s.root, TrashDir) }

func (s *Sandbox) readIndex() map[string]map[string]any {
	raw, err := os.ReadFile(filepath.Join(s.trashPath(), trashIndex))
	index := map[string]map[string]any{}
	if err == nil {
		_ = json.Unmarshal(raw, &index)
	}
	return index
}

func (s *Sandbox) writeIndex(index map[string]map[string]any) error {
	if err := os.MkdirAll(s.trashPath(), 0o755); err != nil {
		return err
	}
	raw, _ := json.Marshal(index)
	return os.WriteFile(filepath.Join(s.trashPath(), trashIndex), raw, 0o644)
}

func newID() string {
	b := make([]byte, 6)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// Trash moves something to the trash (recoverable) and returns its id.
func (s *Sandbox) Trash(rel string) (string, error) {
	source, err := s.Resolve(rel, true)
	if err != nil {
		return "", err
	}
	if source == s.root {
		return "", fail("Your website's main folder cannot be deleted.")
	}
	id, original := newID(), s.Relative(source)
	if err := os.MkdirAll(s.trashPath(), 0o755); err != nil {
		return "", err
	}
	info, _ := os.Stat(source)
	var size *int64
	if !info.IsDir() {
		n := info.Size()
		size = &n
	}
	if err := os.Rename(source, filepath.Join(s.trashPath(), id)); err != nil {
		return "", err
	}
	index := s.readIndex()
	index[id] = map[string]any{"name": filepath.Base(source), "original": original, "is_dir": info.IsDir(), "size": size, "deleted_at": time.Now().UTC().Format("2006-01-02T15:04:05.000000+00:00")}
	return id, s.writeIndex(index)
}

// ListTrash is what is in the trash, newest first.
func (s *Sandbox) ListTrash() []TrashItem {
	items := []TrashItem{}
	for id, meta := range s.readIndex() {
		if _, err := os.Lstat(filepath.Join(s.trashPath(), id)); err != nil {
			continue
		}
		it := TrashItem{ItemID: id}
		it.Name, _ = meta["name"].(string)
		it.OriginalPath, _ = meta["original"].(string)
		it.IsDir, _ = meta["is_dir"].(bool)
		it.DeletedAt, _ = meta["deleted_at"].(string)
		if size, ok := meta["size"].(float64); ok {
			n := int64(size)
			it.SizeBytes = &n
		}
		items = append(items, it)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].DeletedAt > items[j].DeletedAt })
	return items
}

var gone = errors.New("gone")

// RestoreTrash puts an item back, or at the top if its old folder is gone, and returns where it went.
func (s *Sandbox) RestoreTrash(id string) (string, error) {
	index := s.readIndex()
	meta, ok := index[id]
	stored := filepath.Join(s.trashPath(), id)
	if !ok || id == "" || strings.ContainsAny(id, `/\`) {
		return "", fail("That item is no longer in the trash.")
	}
	if _, err := os.Lstat(stored); err != nil {
		return "", fail("That item is no longer in the trash.")
	}
	original, _ := meta["original"].(string)
	original, err := CleanRelative(original)
	if err != nil {
		return "", err
	}
	parent, err := func() (string, error) {
		p, err := s.Resolve(parentOf(original), true)
		if err != nil {
			return s.root, nil
		}
		if info, _ := os.Stat(p); info == nil || !info.IsDir() {
			return s.root, nil
		}
		return p, nil
	}()
	if err != nil {
		return "", err
	}
	name, _ := meta["name"].(string)
	candidate := ""
	for n := 0; candidate == "" || exists(candidate); n++ {
		label := name
		if n == 1 {
			label = name + " (restored)"
		} else if n > 1 {
			label = fmt.Sprintf("%s (restored %d)", name, n)
		}
		candidate = filepath.Join(parent, label)
	}
	if err := os.Rename(stored, candidate); err != nil {
		return "", err
	}
	delete(index, id)
	if err := s.writeIndex(index); err != nil {
		return "", err
	}
	return s.Relative(candidate), nil
}

func exists(p string) bool { _, err := os.Lstat(p); return err == nil }

// PurgeTrash deletes one trashed item for good, or the whole trash when id is empty.
func (s *Sandbox) PurgeTrash(id string) error {
	if id == "" {
		return os.RemoveAll(s.trashPath())
	}
	index := s.readIndex()
	if _, ok := index[id]; !ok || strings.ContainsAny(id, `/\`) {
		return fail("That item is no longer in the trash.")
	}
	_ = os.RemoveAll(filepath.Join(s.trashPath(), id))
	delete(index, id)
	return s.writeIndex(index)
}

var _ = gone
