package handlers

import (
	"strings"
	"testing"
)

func TestSHA512CryptMatchesThePublishedTestVectors(t *testing.T) {
	if got := SHA512Crypt("Hello world!", "saltstring", 5000); got != "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1" {
		t.Fatal(got)
	}
	if got := SHA512Crypt("Hello world!", "saltstringsaltstring", 10000); got != "$6$rounds=10000$saltstringsaltst$OW1/O6BYHV6BcXZu8QVeXbDWra3Oeqh0sbHbbMCVNSnCM/UrjmM0Dp8vOuZeHBy/YTBmSK6H9qs/y3RnOaw5v." {
		t.Fatal(got)
	}
	h := HashPassword("correct horse battery")
	if !strings.HasPrefix(h, "{SHA512-CRYPT}$6$") || strings.Contains(h, "correct horse") {
		t.Fatal(h)
	}
	salt := strings.Split(strings.TrimPrefix(h, "{SHA512-CRYPT}"), "$")[2]
	if SHA512Crypt("correct horse battery", salt, 5000) != strings.TrimPrefix(h, "{SHA512-CRYPT}") {
		t.Fatal("a hash must be reproducible from its own salt")
	}
}

func TestNativeHashIsTheWellKnownMySQLValue(t *testing.T) {
	if NativeHash("password") != "*2470C0C06DEE42FD1618BB99005ADCA2EC9D1E19" {
		t.Fatal(NativeHash("password"))
	}
}
