package main

import (
	"encoding/json"
	"io"
)

func writeJSON(w io.Writer, v any) error { return json.NewEncoder(w).Encode(v) }
