package sandbox

import (
	"archive/zip"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func newBox(t *testing.T) (*Sandbox, string) {
	t.Helper()
	root := filepath.Join(t.TempDir(), "sites")
	if err := os.MkdirAll(filepath.Join(root, "acme.com", "public_html"), 0o755); err != nil {
		t.Fatal(err)
	}
	box, err := New(root)
	if err != nil {
		t.Fatal(err)
	}
	return box, root
}

func wantError(t *testing.T, err error, contains string) {
	t.Helper()
	if err == nil {
		t.Fatalf("expected an error containing %q", contains)
	}
	if !strings.Contains(err.Error(), contains) {
		t.Fatalf("error %q does not contain %q", err.Error(), contains)
	}
}

func TestANewBoxNeverCreatesAFolder(t *testing.T) {
	if _, err := New(filepath.Join(t.TempDir(), "nope")); err == nil {
		t.Fatal("a missing folder must be an error, not created")
	}
}

func TestPathsThatLeaveTheFolderAreRefused(t *testing.T) {
	box, _ := newBox(t)
	for _, p := range []string{"../outside", "/etc/passwd", "a/../../b", `bad\name`, "c:/x", "a\x00b", strings.Repeat("x", 1025), strings.Repeat("y", 300)} {
		if _, err := box.Read(p, 100); err == nil {
			t.Errorf("%q was accepted", p)
		}
		if err := box.Write(p, []byte("x"), true); err == nil {
			t.Errorf("writing %q was accepted", p)
		}
	}
}

func TestTheBasicOperationsWork(t *testing.T) {
	box, root := newBox(t)
	base := "acme.com/public_html"
	if err := box.Write(base+"/a.txt", []byte("hello"), false); err != nil {
		t.Fatal(err)
	}
	wantError(t, box.Write(base+"/a.txt", []byte("x"), false), "already exists")
	if err := box.Write(base+"/a.txt", []byte("hello world"), true); err != nil {
		t.Fatal(err)
	}
	got, _ := box.Read(base+"/a.txt", 100)
	if string(got) != "hello world" {
		t.Fatal(string(got))
	}
	_, err := box.Read(base+"/a.txt", 3)
	wantError(t, err, "too large")
	if err := box.Mkdir(base + "/css"); err != nil {
		t.Fatal(err)
	}
	wantError(t, box.Mkdir(base+"/css"), "already exists")
	wantError(t, box.Mkdir("nope/css"), "does not exist")
	if err := box.Copy(base+"/css", base+"/css2"); err != nil {
		t.Fatal(err)
	}
	if err := box.Rename(base+"/css2", base+"/styles"); err != nil {
		t.Fatal(err)
	}
	wantError(t, box.Rename(base+"/styles", base+"/styles/inner"), "into itself")
	wantError(t, box.Copy(base+"/styles", base+"/styles/inner"), "into itself")
	entries, _ := box.List(base)
	names := []string{}
	for _, e := range entries {
		names = append(names, e.Name)
	}
	if strings.Join(names, ",") != "css,styles,a.txt" {
		t.Fatalf("folders first: %v", names)
	}
	if err := box.Delete(base + "/styles"); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "acme.com", "public_html", "styles")); err == nil {
		t.Fatal("not deleted")
	}
	wantError(t, box.Delete(""), "main folder")
	wantError(t, box.Rename("", "other"), "main folder")
	wantError(t, box.Copy("", "other"), "main folder")
}

func TestUnsafePermissionsAreRefused(t *testing.T) {
	box, _ := newBox(t)
	_ = box.Write("acme.com/public_html/a.txt", []byte("x"), false)
	for _, mode := range []string{"777", "666", "4755", "abc", "600777", "000"} {
		if err := box.Chmod("acme.com/public_html/a.txt", mode); err == nil {
			t.Errorf("%s was accepted", mode)
		}
	}
	if err := box.Chmod("acme.com/public_html/a.txt", "600"); err != nil {
		t.Fatal(err)
	}
	wantError(t, box.Chmod("acme.com/public_html", "644"), "full access")
	wantError(t, box.Chmod("", "755"), "main folder")
}

func TestALinkIsNeverFollowed(t *testing.T) {
	box, root := newBox(t)
	outside := filepath.Join(filepath.Dir(root), "secret.txt")
	_ = os.WriteFile(outside, []byte("secret"), 0o644)
	if err := os.Symlink(outside, filepath.Join(root, "acme.com", "public_html", "leak.txt")); err != nil {
		t.Skip("this machine cannot make symbolic links")
	}
	_, err := box.Read("acme.com/public_html/leak.txt", 100)
	wantError(t, err, "Links are not followed")
	entries, _ := box.List("acme.com/public_html")
	for _, e := range entries {
		if e.Name == "leak.txt" {
			t.Fatal("a link must not be listed")
		}
	}
}

func TestSearchFindsByNameOnly(t *testing.T) {
	box, _ := newBox(t)
	_ = box.Write("acme.com/public_html/Report.TXT", []byte("needle"), false)
	_ = box.Mkdir("acme.com/public_html/reports")
	got, err := box.Search("", "report")
	if err != nil || len(got) != 2 || !got[0].IsDir {
		t.Fatalf("%v %v", got, err)
	}
	if none, _ := box.Search("", "needle"); len(none) != 0 {
		t.Fatal("content must not be searched")
	}
	_, err = box.Search("", " ")
	wantError(t, err, "Type something")
}

func TestArchiveAndExtractRoundTripAndRefuseUnsafeArchives(t *testing.T) {
	box, root := newBox(t)
	base := "acme.com/public_html"
	_ = box.Write(base+"/a.txt", []byte("A"), false)
	_ = box.Mkdir(base + "/d")
	_ = box.Write(base+"/d/b.txt", []byte("B"), false)
	n, err := box.Archive([]string{base + "/a.txt", base + "/d"}, base+"/out.zip")
	if err != nil || n != 3 {
		t.Fatalf("%d %v", n, err)
	}
	wantError(t, mustErr(box.Archive([]string{base + "/a.txt"}, base+"/out.zip")), "already exists")
	wantError(t, mustErr(box.Archive([]string{base + "/a.txt"}, base+"/x.tar")), ".zip")
	wantError(t, mustErr(box.Archive([]string{""}, base+"/y.zip")), "main folder")
	_ = box.Mkdir(base + "/unpacked")
	if n, err := box.Extract(base+"/out.zip", base+"/unpacked"); err != nil || n != 3 {
		t.Fatalf("%d %v", n, err)
	}
	if b, _ := os.ReadFile(filepath.Join(root, "acme.com", "public_html", "unpacked", "d", "b.txt")); string(b) != "B" {
		t.Fatal("content lost")
	}
	_, err = box.Extract(base+"/out.zip", base+"/unpacked")
	wantError(t, err, "already exists")

	// a hostile archive: a file that tries to escape, and one aimed at the trash
	evil := filepath.Join(root, "acme.com", "public_html", "evil.zip")
	f, _ := os.Create(evil)
	zw := zip.NewWriter(f)
	w, _ := zw.Create("../../../escaped.txt")
	_, _ = w.Write([]byte("x"))
	_ = zw.Close()
	_ = f.Close()
	_, err = box.Extract(base+"/evil.zip", base+"/elsewhere")
	wantError(t, err, "unsafe path")
	if _, err := os.Stat(filepath.Join(filepath.Dir(root), "escaped.txt")); err == nil {
		t.Fatal("the archive wrote outside the folder")
	}
	if _, err := os.Stat(filepath.Join(root, "acme.com", "public_html", "elsewhere")); err == nil {
		t.Fatal("all or nothing: nothing may be created")
	}
	_ = os.WriteFile(filepath.Join(root, "acme.com", "public_html", "notzip.zip"), []byte("not a zip"), 0o644)
	_, err = box.Extract(base+"/notzip.zip", base+"/z")
	wantError(t, err, "not a .zip")
}

func mustErr(_ int, err error) error { return err }

func TestTrashRestorePurge(t *testing.T) {
	box, _ := newBox(t)
	base := "acme.com/public_html"
	_ = box.Write(base+"/a.txt", []byte("A"), false)
	id, err := box.Trash(base + "/a.txt")
	if err != nil || len(id) != 12 {
		t.Fatalf("%q %v", id, err)
	}
	items := box.ListTrash()
	if len(items) != 1 || items[0].OriginalPath != base+"/a.txt" || items[0].Name != "a.txt" {
		t.Fatalf("%+v", items)
	}
	// the trash folder is reserved and invisible
	_, err = box.Resolve(".trash/x", false)
	wantError(t, err, "reserved")
	if got, _ := box.List(""); len(got) != 1 {
		t.Fatalf("trash must not be listed: %v", got)
	}
	_ = box.Write(base+"/a.txt", []byte("new"), false) // the name is taken again
	back, err := box.RestoreTrash(id)
	if err != nil || back != base+"/a.txt (restored)" {
		t.Fatalf("%q %v", back, err)
	}
	_, err = box.RestoreTrash(id)
	wantError(t, err, "no longer in the trash")
	id2, _ := box.Trash(base + "/a.txt")
	if err := box.PurgeTrash(id2); err != nil {
		t.Fatal(err)
	}
	wantError(t, box.PurgeTrash("abcdefabcdef"), "no longer in the trash")
	_, _ = box.Trash(base + "/a.txt (restored)")
	if err := box.PurgeTrash(""); err != nil || len(box.ListTrash()) != 0 {
		t.Fatal("emptying the trash")
	}
}

func TestRestoreFallsBackToTheTopWhenTheFolderIsGone(t *testing.T) {
	box, _ := newBox(t)
	_ = box.Mkdir("acme.com/public_html/old")
	_ = box.Write("acme.com/public_html/old/f.txt", []byte("x"), false)
	id, _ := box.Trash("acme.com/public_html/old/f.txt")
	_ = box.Delete("acme.com/public_html/old")
	back, err := box.RestoreTrash(id)
	if err != nil || back != "f.txt" {
		t.Fatalf("%q %v", back, err)
	}
}
