// Package catalog is the agent's allow-list: the only commands it will ever run and the exact shape of their parameters. It is built from the
// document WHPanel generates from its own catalogue, and a test checks it against thousands of valid and invalid commands answered by the Python
// original, so the two cannot disagree about what is allowed.
package catalog

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"

	"whagent/spec"
)

// Field describes one parameter of a command.
type Field struct {
	Kind      string   `json:"kind"`
	Choices   []string `json:"choices"`
	Low       int64    `json:"low"`
	High      int64    `json:"high"`
	Sensitive bool     `json:"sensitive"`
	Required  bool     `json:"required"`
}

// Command is one allow-listed command.
type Command struct {
	Timeout         int               `json:"timeout"`
	Idempotent      bool              `json:"idempotent"`
	MaxAttempts     int               `json:"max_attempts"`
	Ephemeral       bool              `json:"ephemeral"`
	MinAgentVersion []int             `json:"min_agent_version"`
	Fields          map[string]*Field `json:"fields"`
	order           []string
}

type pattern struct {
	Regex  string `json:"regex"`
	MaxLen int    `json:"max_len"`
	re     *regexp.Regexp
}

// Spec is the whole document.
type Spec struct {
	Protocol        int                 `json:"protocol"`
	MinAgentVersion []int               `json:"min_agent_version"`
	MaxInlineBytes  int                 `json:"max_inline_bytes"`
	HealServices    []string            `json:"heal_services"`
	Patterns        map[string]*pattern `json:"patterns"`
	Commands        map[string]*Command `json:"commands"`
}

var (
	once     sync.Once
	loaded   *Spec
	loadErr  error
	b64Re    = regexp.MustCompile(`^[A-Za-z0-9+/]*={0,2}$`)
	errBytes = errors.New("not valid")
)

// Load parses the embedded document once.
func Load() (*Spec, error) {
	once.Do(func() { loaded, loadErr = Parse(spec.Catalog) })
	return loaded, loadErr
}

// Parse reads a catalogue document (used by Load and by tests).
func Parse(raw []byte) (*Spec, error) {
	var s Spec
	if err := json.Unmarshal(raw, &s); err != nil {
		return nil, err
	}
	for kind, p := range s.Patterns {
		re, err := regexp.Compile(p.Regex)
		if err != nil {
			return nil, fmt.Errorf("pattern %s: %w", kind, err)
		}
		p.re = re
	}
	for _, c := range s.Commands {
		for name := range c.Fields {
			c.order = append(c.order, name)
		}
		sort.Strings(c.order)
	}
	return &s, nil
}

// Names lists every allowed command, sorted.
func (s *Spec) Names() []string {
	out := make([]string, 0, len(s.Commands))
	for name := range s.Commands {
		out = append(out, name)
	}
	sort.Strings(out)
	return out
}

// Validate checks a command and its parameters. Unknown commands, unknown or missing parameters and every malformed value are refused. On success it returns
// the parameters (numbers as int64, lists as []string) ready for a handler.
func (s *Spec) Validate(command string, params any) (map[string]any, error) {
	spec, ok := s.Commands[command]
	if !ok {
		return nil, errors.New("that command is not on the agent's allow-list")
	}
	obj, ok := params.(map[string]any)
	if !ok {
		return nil, errors.New("parameters must be an object")
	}
	var extra []string
	for key := range obj {
		if _, known := spec.Fields[key]; !known {
			extra = append(extra, key)
		}
	}
	if len(extra) > 0 {
		sort.Strings(extra)
		return nil, fmt.Errorf("unknown parameter(s): %s", strings.Join(extra, ", "))
	}
	cleaned := map[string]any{}
	for _, name := range spec.order {
		rule := spec.Fields[name]
		value, present := obj[name]
		if !present {
			if rule.Required {
				return nil, fmt.Errorf("%s is required", name)
			}
			continue
		}
		v, err := s.clean(name, rule, value)
		if err != nil {
			return nil, err
		}
		cleaned[name] = v
	}
	return cleaned, nil
}

func hasControl(text string, allowLineBreaks bool) bool {
	for _, r := range text {
		if r == 127 || (r < 32 && !(allowLineBreaks && (r == '\n' || r == '\r' || r == '\t'))) {
			return true
		}
	}
	return false
}

func relpath(name string, value any, allowRoot bool) (string, error) {
	text, ok := value.(string)
	if !ok || utf8.RuneCountInString(text) > 1024 || hasControl(text, false) || strings.Contains(text, `\`) || strings.Contains(text, ":") || strings.HasPrefix(text, "/") {
		return "", fmt.Errorf("%s is not a valid path", name)
	}
	var parts []string
	for _, p := range strings.Split(text, "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	for _, p := range parts {
		if p == ".." || utf8.RuneCountInString(p) > 255 {
			return "", fmt.Errorf("%s is not a valid path", name)
		}
	}
	if len(parts) == 0 && !allowRoot {
		return "", fmt.Errorf("%s is not a valid path", name)
	}
	return strings.Join(parts, "/"), nil
}

func integer(value any) (int64, bool) {
	n, ok := value.(json.Number)
	if !ok {
		return 0, false
	}
	if strings.ContainsAny(n.String(), ".eE") { // a number written as 1.0 or 1e3 is a float, which is not a whole number here
		return 0, false
	}
	i, err := strconv.ParseInt(n.String(), 10, 64)
	return i, err == nil
}

func (s *Spec) clean(name string, rule *Field, value any) (any, error) {
	switch rule.Kind {
	case "int":
		i, ok := integer(value)
		if !ok || i < rule.Low || i > rule.High {
			return nil, fmt.Errorf("%s must be a whole number from %d to %d", name, rule.Low, rule.High)
		}
		return i, nil
	case "enum_list":
		list, ok := value.([]any)
		if !ok || len(list) > len(rule.Choices) {
			return nil, fmt.Errorf("%s must be a list of: %s", name, strings.Join(rule.Choices, ", "))
		}
		seen := map[string]bool{}
		out := make([]string, 0, len(list))
		for _, item := range list {
			text, ok := item.(string)
			if !ok || !contains(rule.Choices, text) || seen[text] {
				return nil, fmt.Errorf("%s must be a list of: %s", name, strings.Join(rule.Choices, ", "))
			}
			seen[text] = true
			out = append(out, text)
		}
		return out, nil
	case "relpath":
		return relpath(name, value, true)
	case "path_list":
		list, ok := value.([]any)
		if !ok || len(list) < 1 || len(list) > 200 {
			return nil, fmt.Errorf("%s must be a list of 1 to 200 paths", name)
		}
		out := make([]string, 0, len(list))
		for _, item := range list {
			p, err := relpath(name, item, false)
			if err != nil {
				return nil, err
			}
			out = append(out, p)
		}
		return out, nil
	case "b64":
		text, ok := value.(string)
		if !ok || len(text) > s.MaxInlineBytes*4/3+8 || !b64Re.MatchString(text) {
			return nil, fmt.Errorf("%s must be base64 text", name)
		}
		return text, nil
	}
	text, ok := value.(string)
	if !ok {
		return nil, fmt.Errorf("%s must be text", name)
	}
	n := utf8.RuneCountInString(text)
	switch rule.Kind {
	case "text":
		if int64(n) > rule.High || hasControl(text, true) {
			return nil, fmt.Errorf("%s must be text of at most %d characters", name, rule.High)
		}
		return text, nil
	case "query":
		if n < 1 || n > 100 || hasControl(text, false) {
			return nil, fmt.Errorf("%s must be 1 to 100 printable characters", name)
		}
		return text, nil
	case "enum":
		if !contains(rule.Choices, text) {
			return nil, fmt.Errorf("%s must be one of: %s", name, strings.Join(rule.Choices, ", "))
		}
		return text, nil
	case "secret":
		if n < 12 || n > 128 || hasControl(text, false) {
			return nil, fmt.Errorf("%s must be 12 to 128 printable characters", name)
		}
		return text, nil
	}
	p, ok := s.Patterns[rule.Kind]
	if !ok {
		return nil, fmt.Errorf("%s: unknown kind %q", name, rule.Kind)
	}
	if !p.re.MatchString(text) || (p.MaxLen > 0 && (n < 1 || n > p.MaxLen)) {
		return nil, fmt.Errorf("%s is not a valid %s", name, strings.ReplaceAll(rule.Kind, "_", " "))
	}
	return text, nil
}

func contains(list []string, item string) bool {
	for _, c := range list {
		if c == item {
			return true
		}
	}
	return false
}

// Scrub returns the parameters with secret values hidden, for logs.
func (s *Spec) Scrub(command string, params map[string]any) map[string]any {
	out := map[string]any{}
	spec := s.Commands[command]
	for k, v := range params {
		if spec != nil && spec.Fields[k] != nil && spec.Fields[k].Sensitive {
			out[k] = "***"
		} else {
			out[k] = v
		}
	}
	return out
}

// Decode parses JSON the way the agent must: numbers stay numbers (not floats) so a whole number can be told from 1.0.
func Decode(raw []byte) (any, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	return v, nil
}

var _ = errBytes
