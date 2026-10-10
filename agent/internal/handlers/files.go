package handlers

import (
	"encoding/base64"
	"errors"
	"os"
	"syscall"

	"whagent/internal/sandbox"
)

// File commands: one account's folder through the sandbox (no links followed, nothing outside the folder). In production they must run as the account's own Linux user: the agent
// starts a worker under the account's uid and gid (see FileRunner) so a mistake or a trick can reach no further than the account's own files. Content travels inside commands and is
// limited to 3 MB; bigger files go by SFTP.

const maxRead = 3_000_000

func init() {
	extraHandlers = append(extraHandlers, func(a *Agent) map[string]func(map[string]any) (Result, error) {
		out := map[string]func(map[string]any) (Result, error){}
		for _, name := range []string{"FILE_LIST", "FILE_READ", "FILE_WRITE", "FILE_MKDIR", "FILE_RENAME", "FILE_COPY", "FILE_CHMOD", "FILE_DELETE", "FILE_ARCHIVE", "FILE_EXTRACT", "FILE_TRASH",
			"FILE_TRASH_LIST", "FILE_TRASH_RESTORE", "FILE_TRASH_PURGE", "FILE_SEARCH"} {
			name := name
			out[name] = func(p map[string]any) (Result, error) {
				if a.FileRunner != nil {
					res, e := a.FileRunner(str(p, "account"), name, p)
					if e != nil {
						return Result{}, e
					}
					return res, nil
				}
				return a.FileCommand(name, p)
			}
		}
		return out
	})
}

// FileCommand carries out one file command directly (the worker process calls this as the account's user).
func (a *Agent) FileCommand(command string, p map[string]any) (Result, error) {
	root := ""
	if a.RootFor != nil {
		root = a.RootFor(str(p, "account"))
	}
	if root == "" {
		return Result{}, herr("not_found", "No such account on this node.")
	}
	box, err := sandbox.New(root) // never creates the folder: a missing one means no such account
	if err != nil {
		return Result{}, herr("not_found", "No such account on this node.")
	}
	res, err := a.fileCommand(box, command, p)
	return res, fileError(err)
}

func fileError(err error) error {
	if err == nil {
		return nil
	}
	var se *sandbox.Error
	var he *Error
	switch {
	case errors.As(err, &he):
		return he
	case errors.As(err, &se):
		return herr("file_error", "%s", se.Msg)
	case errors.Is(err, syscall.ENOSPC), isQuota(err):
		return herr("disk_full", "Your disk space is full, so this could not be saved. Delete files you do not need, or upgrade your plan.")
	}
	var pe *os.PathError
	if errors.As(err, &pe) {
		return herr("system_error", "The file system refused that (%s).", pe.Err.Error())
	}
	return herr("system_error", "The file system refused that (%s).", err.Error())
}

func entries(list []sandbox.Entry) []any {
	out := make([]any, 0, len(list))
	for _, e := range list {
		out = append(out, e)
	}
	return out
}

func (a *Agent) fileCommand(box *sandbox.Sandbox, command string, p map[string]any) (Result, error) {
	switch command {
	case "FILE_LIST":
		list, err := box.List(str(p, "path"))
		return Result{"listed", map[string]any{"entries": entries(list)}}, err
	case "FILE_READ":
		max := num(p, "max_bytes")
		if max > maxRead {
			max = maxRead
		}
		data, err := box.Read(str(p, "path"), max)
		if err != nil {
			return Result{}, err
		}
		return Result{"read", map[string]any{"content": base64.StdEncoding.EncodeToString(data)}}, nil
	case "FILE_SEARCH":
		list, err := box.Search(str(p, "path"), str(p, "query"))
		return Result{"listed", map[string]any{"entries": entries(list)}}, err
	case "FILE_TRASH_LIST":
		items := []any{}
		for _, it := range box.ListTrash() {
			items = append(items, it)
		}
		return Result{"listed", map[string]any{"items": items}}, nil
	case "FILE_WRITE":
		content, err := base64.StdEncoding.Strict().DecodeString(str(p, "content"))
		if err != nil {
			return Result{}, herr("invalid", "The content is not valid base64.")
		}
		if err := box.Write(str(p, "path"), content, str(p, "overwrite") == "yes"); err != nil {
			return Result{}, err
		}
		return Result{"written", map[string]any{"size": len(content)}}, nil
	case "FILE_MKDIR":
		return Result{"created", nil}, box.Mkdir(str(p, "path"))
	case "FILE_RENAME":
		return Result{"renamed", nil}, box.Rename(str(p, "path"), str(p, "new_path"))
	case "FILE_COPY":
		return Result{"copied", nil}, box.Copy(str(p, "path"), str(p, "new_path"))
	case "FILE_CHMOD":
		return Result{"changed", nil}, box.Chmod(str(p, "path"), str(p, "mode"))
	case "FILE_DELETE":
		return Result{"deleted", nil}, box.Delete(str(p, "path"))
	case "FILE_ARCHIVE":
		paths, _ := p["paths"].([]string)
		n, err := box.Archive(paths, str(p, "archive_path"))
		return Result{"archived", map[string]any{"count": n}}, err
	case "FILE_EXTRACT":
		n, err := box.Extract(str(p, "path"), str(p, "destination"))
		return Result{"extracted", map[string]any{"count": n}}, err
	case "FILE_TRASH":
		id, err := box.Trash(str(p, "path"))
		return Result{"trashed", map[string]any{"item_id": id}}, err
	case "FILE_TRASH_RESTORE":
		where, err := box.RestoreTrash(str(p, "item_id"))
		return Result{"restored", map[string]any{"path": where}}, err
	case "FILE_TRASH_PURGE":
		id := str(p, "item_id")
		if id == "ALL" {
			id = ""
		}
		return Result{"purged", nil}, box.PurgeTrash(id)
	}
	return Result{}, herr("not_implemented", "%s is not implemented on this node yet.", command)
}
