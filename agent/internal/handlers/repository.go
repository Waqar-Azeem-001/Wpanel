package handlers

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"whagent/internal/render"
)

func pathsOfState(account string) string { return render.PathsOf(account, "").State }

func configPaths(account, domain string) []string {
	p := render.PathsOf(account, domain)
	return []string{p.State, p.FpmConf, p.Unit, p.Slice, p.Vhost}
}

func jsonMarshal(v any) ([]byte, error) { return json.Marshal(v) }

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// ---- a repository in a folder, for tests ------------------------------------------------------------------------------------------------------------

// FolderRepo keeps snapshots as folders with a manifest of every file's SHA-256, so the logic around backups (ownership, retention, damage detection, test restores) is tested for real.
// It does NOT encrypt; encryption belongs to restic (and to the Python reference agent's own fake, which uses real AES-GCM).
type FolderRepo struct{ Dir string }

type folderMeta struct {
	ID       string            `json:"id"`
	Tags     []string          `json:"tags"`
	Manifest map[string]string `json:"manifest"`
}

// Init creates the repository folder.
func (f *FolderRepo) Init() (bool, error) {
	if _, err := os.Stat(filepath.Join(f.Dir, "snapshots")); err == nil {
		return false, nil
	}
	return true, os.MkdirAll(filepath.Join(f.Dir, "snapshots"), 0o700)
}

// Backup stores a snapshot of a folder.
func (f *FolderRepo) Backup(folder string, tags []string) (string, int64, error) {
	id := randomHex(8)
	data := filepath.Join(f.Dir, "snapshots", id, "data")
	if _, err := copyTree(folder, data); err != nil {
		return "", 0, err
	}
	manifest, err := Manifest(data)
	if err != nil {
		return "", 0, err
	}
	raw, _ := json.Marshal(folderMeta{id, tags, manifest})
	if err := os.WriteFile(filepath.Join(f.Dir, "snapshots", id, "meta.json"), raw, 0o600); err != nil {
		return "", 0, err
	}
	var size int64
	for path := range manifest {
		if info, err := os.Stat(filepath.Join(data, filepath.FromSlash(path))); err == nil {
			size += info.Size()
		}
	}
	return id, size, nil
}

// Meta reads a snapshot's tags and manifest.
func (f *FolderRepo) Meta(id string) (Snapshot, error) {
	raw, err := os.ReadFile(filepath.Join(f.Dir, "snapshots", filepath.Base(id), "meta.json"))
	if err != nil {
		return Snapshot{}, errors.New("That backup does not exist.")
	}
	var m folderMeta
	if err := json.Unmarshal(raw, &m); err != nil {
		return Snapshot{}, errors.New("That backup could not be read.")
	}
	return Snapshot{m.ID, m.Tags, m.Manifest}, nil
}

// Restore copies a snapshot to target/data.
func (f *FolderRepo) Restore(id, target string) (string, error) {
	if _, err := f.Meta(id); err != nil {
		return "", err
	}
	out := filepath.Join(target, "data")
	_, err := copyTree(filepath.Join(f.Dir, "snapshots", filepath.Base(id), "data"), out)
	return out, err
}

// Check compares every stored file with the manifest made when the backup was taken.
func (f *FolderRepo) Check(id string) (bool, string) {
	meta, err := f.Meta(id)
	if err != nil {
		return false, err.Error()
	}
	found, err := Manifest(filepath.Join(f.Dir, "snapshots", filepath.Base(id), "data"))
	if err != nil || !sameManifest(found, meta.Manifest) {
		return false, "The files in the backup do not match what was backed up."
	}
	return true, fmt.Sprintf("%d files checked", len(found))
}

// Forget deletes a snapshot.
func (f *FolderRepo) Forget(id string) (bool, error) {
	path := filepath.Join(f.Dir, "snapshots", filepath.Base(id))
	if _, err := os.Stat(path); err != nil {
		return false, nil
	}
	return true, os.RemoveAll(path)
}

// ---- restic ------------------------------------------------------------------------------------------------------------------------------------------

// Restic drives the restic program against an S3-compatible bucket. Its settings come from the environment file /etc/whp/restic.env, never from the command line. It has NEVER been run:
// the parsing of restic's output and the snapshot-subfolder restore below are written from restic's documentation.
type Restic struct {
	Env  map[string]string
	Run  func(env []string, args ...string) ([]byte, error) // replaceable in tests
	Wait time.Duration
}

// NewRestic builds one from the saved settings.
func NewRestic(env map[string]string) *Restic {
	r := &Restic{Env: env}
	r.Run = func(environ []string, args ...string) ([]byte, error) {
		cmd := exec.Command("restic", args...)
		cmd.Env = append(os.Environ(), environ...)
		out, err := cmd.Output()
		if err != nil {
			var ee *exec.ExitError
			if errors.As(err, &ee) && len(ee.Stderr) > 0 {
				lines := strings.Split(strings.TrimSpace(string(ee.Stderr)), "\n")
				return nil, errors.New(lines[len(lines)-1])
			}
			return nil, err
		}
		return out, nil
	}
	return r
}

func (r *Restic) env() []string {
	var out []string
	for k, v := range r.Env {
		out = append(out, k+"="+v)
	}
	return out
}

func (r *Restic) do(args ...string) ([]byte, error) { return r.Run(r.env(), args...) }

// Init creates the repository if it is not there.
func (r *Restic) Init() (bool, error) {
	if _, err := r.do("cat", "config"); err == nil {
		return false, nil
	}
	_, err := r.do("init")
	return err == nil, err
}

type resticSnapshot struct {
	ShortID string   `json:"short_id"`
	ID      string   `json:"id"`
	Tags    []string `json:"tags"`
	Paths   []string `json:"paths"`
}

// Backup stores a snapshot of a folder.
func (r *Restic) Backup(folder string, tags []string) (string, int64, error) {
	args := []string{"backup", "--json", folder}
	for _, t := range tags {
		args = append(args, "--tag", t)
	}
	out, err := r.do(args...)
	if err != nil {
		return "", 0, err
	}
	for _, line := range strings.Split(string(out), "\n") {
		var summary struct {
			MessageType string `json:"message_type"`
			SnapshotID  string `json:"snapshot_id"`
			DataAdded   int64  `json:"data_added"`
		}
		if json.Unmarshal([]byte(line), &summary) == nil && summary.MessageType == "summary" && summary.SnapshotID != "" {
			return summary.SnapshotID, summary.DataAdded, nil
		}
	}
	return "", 0, errors.New("restic did not report a snapshot")
}

func (r *Restic) snapshot(id string) (resticSnapshot, error) {
	out, err := r.do("snapshots", "--json", id)
	if err != nil {
		return resticSnapshot{}, err
	}
	var list []resticSnapshot
	if json.Unmarshal(out, &list) != nil || len(list) == 0 {
		return resticSnapshot{}, errors.New("That backup does not exist.")
	}
	return list[0], nil
}

// Meta reads a snapshot's tags.
func (r *Restic) Meta(id string) (Snapshot, error) {
	s, err := r.snapshot(id)
	return Snapshot{ID: s.ShortID, Tags: s.Tags}, err
}

// Restore puts the snapshot's staged folder into target/data (restic's "snapshot:folder" form restores one subfolder without its parents).
func (r *Restic) Restore(id, target string) (string, error) {
	s, err := r.snapshot(id)
	if err != nil || len(s.Paths) == 0 {
		return "", errors.New("That backup does not exist.")
	}
	out := filepath.Join(target, "data")
	_, err = r.do("restore", s.ID+":"+s.Paths[0], "--target", out)
	return out, err
}

// Check reads a sample of the stored data.
func (r *Restic) Check(string) (bool, string) {
	if _, err := r.do("check", "--read-data-subset=1/10"); err != nil {
		return false, err.Error()
	}
	return true, "checked"
}

// Forget deletes a snapshot and frees its space.
func (r *Restic) Forget(id string) (bool, error) {
	_, err := r.do("forget", "--prune", id)
	return err == nil, err
}
