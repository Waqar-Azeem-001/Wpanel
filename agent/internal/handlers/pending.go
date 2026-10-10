package handlers

// Acme gets certificates (the real one runs certbot; tests use a fake that makes real self-signed ones).
type Acme interface {
	Obtain(names []string, webroot, method string) (chain, key string, err error)
}
