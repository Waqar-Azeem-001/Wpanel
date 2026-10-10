package handlers

import "time"

// The only repair the agent carries out on WHPanel's request: restart one of a short list of the node's own services. It does nothing else: no files are deleted, no customer
// process is touched, nothing is rebooted. The web and SSH servers' configuration is tested first and a failing test stops the restart; after the restart the service must report
// active, otherwise the command fails and WHPanel counts it as a failed attempt.

var testFirst = map[string][]string{"httpd": {"httpd", "-t"}, "sshd": {"sshd", "-t"}}

// Sleep is what waits between checks; tests replace it.
var Sleep = time.Sleep

func (a *Agent) restartService(p map[string]any) (Result, error) {
	service := str(p, "service")
	if argv, ok := testFirst[service]; ok {
		if _, err := a.Sys.Run(argv, ""); err != nil {
			return Result{}, herr("config_invalid", "The %s configuration does not pass its own test, so it was not restarted.", service)
		}
	}
	if _, err := a.Sys.Run([]string{"systemctl", "restart", service}, ""); err != nil {
		return Result{}, err
	}
	const tries = 5
	for attempt := 0; attempt < tries; attempt++ {
		if status, _ := a.Sys.Probe([]string{"systemctl", "is-active", service}); status == 0 {
			return Result{"restarted", map[string]any{"service": service}}, nil
		}
		if attempt+1 < tries {
			Sleep(time.Second)
		}
	}
	return Result{}, herr("still_down", "%s was restarted but is not running.", service)
}
