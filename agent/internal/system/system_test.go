package system

import "testing"

func TestPathsOutsideTheAllowedPlacesAreRefused(t *testing.T) {
	for _, p := range []string{"/etc/../etc/passwd", "relative/path", "/root/.ssh/authorized_keys", "/home/../root", "/etc/x\x00", "/usr/bin/x", "/home//x", "/proc/mounts"} {
		if _, err := CheckPath(p, false); err == nil {
			t.Errorf("%q was accepted", p)
		}
	}
	for _, p := range []string{"/home/abcd1234/sites", "/etc/whp/php/x.conf", "/var/lib/whp/accounts/a.json", "/srv/whp/sftp/a", "/var/mail/vhosts/x"} {
		if _, err := CheckPath(p, false); err != nil {
			t.Errorf("%q was refused: %v", p, err)
		}
	}
	if _, err := CheckPath("/proc/mounts", true); err != nil {
		t.Error("/proc may be read")
	}
	f := NewFake()
	if err := f.Write("/proc/sys/x", "1", 0o644, "root", ""); err == nil {
		t.Error("/proc must never be written")
	}
}

func TestTheFakeRecordsEverythingTheWayPythonDoes(t *testing.T) {
	f := NewFake()
	_ = f.Mkdir("/home/a", FileMode(0o710), "root", "apache")
	_ = f.Write("/etc/whp/x", "hello", 0o600, "root", "")
	_, _ = f.Run([]string{"useradd", "-m", "bob"}, "")
	_, _ = f.Run([]string{"chpasswd"}, "bob:secret")
	want := [][]string{{"mkdir", "/home/a", "0o710", "root", "apache"}, {"write", "/etc/whp/x", "0o600", "root"}, {"useradd", "-m", "bob"}, {"chpasswd"}}
	if len(f.Commands) != 4 {
		t.Fatal(f.Commands)
	}
	for i, w := range want {
		if len(f.Commands[i]) != len(w) {
			t.Fatalf("%v != %v", f.Commands[i], w)
		}
		for j := range w {
			if f.Commands[i][j] != w[j] {
				t.Fatalf("%v != %v", f.Commands[i], w)
			}
		}
	}
	if uid, _ := f.UID("bob"); uid != 2000 {
		t.Error("first user gets 2000")
	}
	if Mode(FileMode(0o2750)) != "0o2750" {
		t.Errorf("setgid mode %s", Mode(FileMode(0o2750)))
	}
	if len(f.Inputs) != 1 || f.Inputs[0][1] != "bob:secret" {
		t.Error("the input is kept apart from the command log")
	}
}

func TestFailureOnTheNthCommandAndServiceProbes(t *testing.T) {
	f := NewFake()
	f.FailOn = 1
	_, _ = f.Run([]string{"a"}, "")
	if _, err := f.Run([]string{"b"}, ""); err == nil {
		t.Fatal("the second command should fail")
	}
	if f.Commands[1][0] != "FAILED" {
		t.Error("failures are logged")
	}
	f.ServiceState["httpd"] = "active"
	if st, _ := f.Probe([]string{"systemctl", "is-active", "httpd"}); st != 0 {
		t.Error("active is status 0")
	}
	if st, out := f.Probe([]string{"systemctl", "is-active", "x"}); st != 3 || out != "inactive" {
		t.Error("unknown services are inactive")
	}
}

func TestRemoveAndList(t *testing.T) {
	f := NewFake()
	_ = f.Write("/etc/whp/a/1", "x", 0o644, "root", "")
	_ = f.Write("/etc/whp/a/2", "y", 0o644, "root", "")
	if got := f.List("/etc/whp/a/"); len(got) != 2 {
		t.Fatal(got)
	}
	_ = f.Remove("/etc/whp/a")
	if len(f.List("/etc/whp/a")) != 0 {
		t.Fatal("removed")
	}
}
