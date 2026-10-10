package handlers

import (
	"crypto/rand"
	"crypto/sha512"
	"fmt"
	"strings"
)

// SHA-512 crypt ($6$), the password format Dovecot reads as {SHA512-CRYPT}. It is written from Drepper's published specification and checked against the specification's own test
// vectors (see the tests). The password never has to be written down: only this hash reaches the node's files.

const cryptAlphabet = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

var cryptOrder = [][3]int{{0, 21, 42}, {22, 43, 1}, {44, 2, 23}, {3, 24, 45}, {25, 46, 4}, {47, 5, 26}, {6, 27, 48}, {28, 49, 7}, {50, 8, 29}, {9, 30, 51}, {31, 52, 10},
	{53, 11, 32}, {12, 33, 54}, {34, 55, 13}, {56, 14, 35}, {15, 36, 57}, {37, 58, 16}, {59, 17, 38}, {18, 39, 60}, {40, 61, 19}, {62, 20, 41}}

func stretch(block []byte, length int) []byte {
	out := make([]byte, 0, length)
	for len(out) < length {
		out = append(out, block...)
	}
	return out[:length]
}

func b64from(value uint32, count int) string {
	var sb strings.Builder
	for i := 0; i < count; i++ {
		sb.WriteByte(cryptAlphabet[value&0x3f])
		value >>= 6
	}
	return sb.String()
}

// SHA512Crypt hashes a password with a salt (at most 16 characters) and a number of rounds (5000 is the default and is left out of the text).
func SHA512Crypt(password, salt string, rounds int) string {
	pw, sl := []byte(password), []byte(salt)
	if len(sl) > 16 {
		sl = sl[:16]
	}
	b := sha512.Sum512(append(append(append([]byte{}, pw...), sl...), pw...))
	a := sha512.New()
	a.Write(pw)
	a.Write(sl)
	a.Write(stretch(b[:], len(pw)))
	for n := len(pw); n > 0; n >>= 1 {
		if n&1 == 1 {
			a.Write(b[:])
		} else {
			a.Write(pw)
		}
	}
	da := a.Sum(nil)
	dp := sha512.Sum512(stretch(pw, len(pw)*len(pw)))
	p := stretch(dp[:], len(pw))
	ds := sha512.Sum512(stretch(sl, len(sl)*(16+int(da[0]))))
	s := stretch(ds[:], len(sl))
	prev := da
	for i := 0; i < rounds; i++ {
		c := sha512.New()
		if i&1 == 1 {
			c.Write(p)
		} else {
			c.Write(prev)
		}
		if i%3 != 0 {
			c.Write(s)
		}
		if i%7 != 0 {
			c.Write(p)
		}
		if i&1 == 1 {
			c.Write(prev)
		} else {
			c.Write(p)
		}
		prev = c.Sum(nil)
	}
	var out strings.Builder
	for _, o := range cryptOrder {
		out.WriteString(b64from(uint32(prev[o[0]])<<16|uint32(prev[o[1]])<<8|uint32(prev[o[2]]), 4))
	}
	out.WriteString(b64from(uint32(prev[63]), 2))
	prefix := "$6$"
	if rounds != 5000 {
		prefix += fmt.Sprintf("rounds=%d$", rounds)
	}
	return prefix + string(sl) + "$" + out.String()
}

// HashPassword is a fresh {SHA512-CRYPT} hash with a random salt, ready for Dovecot's password file.
func HashPassword(password string) string {
	salt := make([]byte, 16)
	raw := make([]byte, 16)
	_, _ = rand.Read(raw)
	for i, r := range raw {
		salt[i] = cryptAlphabet[int(r)%64]
	}
	return "{SHA512-CRYPT}" + SHA512Crypt(password, string(salt), 5000)
}
