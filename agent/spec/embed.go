// Package spec embeds the contract files generated from WHPanel's Python command catalogue (manage.py export_agent_spec). Nothing in the agent
// hard-codes the list of commands, their parameters, patterns or timeouts: they all come from here.
package spec

import _ "embed"

// Catalog is spec/catalog.json: every allowed command with its typed fields, and every pattern.
//
//go:embed catalog.json
var Catalog []byte
