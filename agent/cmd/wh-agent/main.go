// wh-agent is the program on a native node that does what WHPanel asks. It can only run commands from a fixed catalogue (generated from WHPanel's own), only if they carry WHPanel's signature,
// and never through a shell.
//
//	wh-agent enroll --panel URL --token TOKEN [--capabilities web,php,database] [--acme-email ADDRESS]
//	wh-agent run                     (the service: poll, heartbeat, recurring jobs, certificate renewal)
//	wh-agent once                    (handle at most one command and exit: for checking a node by hand)
//	wh-agent file-worker             (internal: runs ONE file command as the account's own user)
//	wh-agent check                   (is the catalogue and the saved configuration sound?)
//	wh-agent version
//
// Status: compiled and tested on a development machine only. It has never been run on a real node.
package main

import (
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/envelope"
	"whagent/internal/handlers"
	"whagent/internal/panel"
	"whagent/internal/runas"
	"whagent/internal/service"
	"whagent/internal/system"
)

const defaultConfig = "/etc/whp/agent.json"

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "enroll":
		err = enroll(os.Args[2:])
	case "run":
		err = run(os.Args[2:], false)
	case "once":
		err = run(os.Args[2:], true)
	case "file-worker":
		err = fileWorker(os.Stdin, os.Stdout)
	case "check":
		err = check(os.Args[2:])
	case "version":
		fmt.Println("wh-agent", panel.Version)
	default:
		usage()
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "wh-agent:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: wh-agent enroll|run|once|check|version (see the comment at the top of cmd/wh-agent/main.go)")
}

func enroll(args []string) error {
	fs := flag.NewFlagSet("enroll", flag.ExitOnError)
	panelURL := fs.String("panel", "", "WHPanel's address")
	token := fs.String("token", "", "the one-time enrolment token")
	caps := fs.String("capabilities", "web,php,database", "what this node can do: web,php,database,ssl,dns,mail,backup,metrics")
	email := fs.String("acme-email", "", "contact address for the certificate authority")
	configPath := fs.String("config", defaultConfig, "where to save the configuration")
	_ = fs.Parse(args)
	if *panelURL == "" || *token == "" {
		return fmt.Errorf("--panel and --token are required")
	}
	_, disk, _ := handlers.DiskUsage("/home")
	inventory := panel.Gather(splitList(*caps), int64(disk>>30))
	cfg, err := panel.Enroll(*panelURL, *token, inventory, filepath.Join(filepath.Dir(*configPath), "agent"), *email, nil)
	if err != nil {
		return err
	}
	if err := cfg.Save(*configPath); err != nil {
		return err
	}
	fmt.Printf("enrolled as node %d; configuration saved to %s\n", cfg.NodeID, *configPath)
	return nil
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func build(cfg *panel.Config, fake bool, root string) (*service.Service, error) {
	spec, err := catalog.Load()
	if err != nil {
		return nil, err
	}
	key, err := envelope.PublicKey(cfg.PublicKey)
	if err != nil {
		return nil, err
	}
	client, err := panel.New(cfg)
	if err != nil {
		return nil, err
	}
	var sys system.System = system.Real{}
	var engine handlers.Engine = handlers.MariaDB{}
	agent := handlers.New(spec, sys)
	agent.MailRoot, agent.StageRoot = "/var/mail/vhosts", "/var/lib/whp/stage"
	agent.RootFor = func(account string) string {
		p := "/home/" + account + "/sites"
		if info, err := os.Stat(p); err == nil && info.IsDir() {
			return p
		}
		return ""
	}
	agent.LogFor = func(account string) string {
		st := agent.StateOf(account)
		if st == "" {
			return ""
		}
		return "/home/" + account + "/logs/" + st + ".access.log"
	}
	agent.Restic = func(env map[string]string) (handlers.Repository, error) { return handlers.NewRestic(env), nil }
	agent.Acme = &handlers.Certbot{Email: cfg.AcmeEmail, Read: func(p string) ([]byte, error) { return os.ReadFile(p) }}
	agent.FileRunner = runas.Runner()
	if fake { // for testing the conversation with WHPanel only: nothing real is touched
		f := system.NewFake()
		agent.Sys, agent.FileRunner, agent.Engine = f, nil, handlers.NewFakeEngine()
		agent.Restic = nil
		agent.RootFor = func(account string) string { return "" }
		_ = root
	} else {
		agent.Engine = engine
	}
	svc := &service.Service{Spec: spec, Agent: agent, Client: client, Guard: envelope.NewGuard("/var/lib/whp/replay.json"), PublicKey: key, NodeID: cfg.NodeID,
		Log: log.New(os.Stderr, "wh-agent ", log.LstdFlags), WaitSeconds: 20, RenewBefore: 30 * 24 * time.Hour}
	if fake {
		svc.Guard = envelope.NewGuard("")
	}
	svc.Metrics = &handlers.Metrics{Agent: agent, Capabilities: cfg.Capabilities, DiskPath: "/home", DiskUsage: handlers.DiskUsage}
	var mailLog func() []string
	if contains(cfg.Capabilities, "mail") {
		mailLog = func() []string { return tailLines("/var/log/maillog", 5000) }
	}
	svc.Jobs = agent.StandardJobs(agent.Accounts, mailLog)
	return svc, nil
}

func contains(list []string, s string) bool {
	for _, c := range list {
		if c == s {
			return true
		}
	}
	return false
}

func tailLines(path string, n int) []string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	lines := strings.Split(string(raw), "\n")
	if len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	return lines
}

func run(args []string, once bool) error {
	fs := flag.NewFlagSet("run", flag.ExitOnError)
	configPath := fs.String("config", defaultConfig, "the saved configuration")
	fake := fs.Bool("fake-machine", false, "TESTING ONLY: run commands against a pretend machine, touching nothing real")
	_ = fs.Parse(args)
	cfg, err := panel.LoadConfig(*configPath)
	if err != nil {
		return fmt.Errorf("not enrolled? %w", err)
	}
	svc, err := build(cfg, *fake, "")
	if err != nil {
		return err
	}
	if once {
		if err := svc.Beat(); err != nil {
			return err
		}
		got, err := svc.Once()
		if err != nil {
			return err
		}
		fmt.Println("handled a command:", got)
		return nil
	}
	stop := make(chan struct{})
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-signals; close(stop) }()
	svc.Run(stop)
	return nil
}

func fileWorker(in io.Reader, out io.Writer) error {
	spec, err := catalog.Load()
	if err != nil {
		return err
	}
	raw, err := io.ReadAll(io.LimitReader(in, 16<<20))
	if err != nil {
		return err
	}
	decoded, err := catalog.Decode(raw)
	request, ok := decoded.(map[string]any)
	if err != nil || !ok {
		return fmt.Errorf("the request is not valid")
	}
	command, _ := request["command"].(string)
	agent := handlers.New(spec, system.Real{})
	agent.RootFor = func(account string) string {
		p := "/home/" + account + "/sites"
		if info, err := os.Stat(p); err == nil && info.IsDir() {
			return p
		}
		return ""
	}
	res, herr := agent.Run(command, request["params"])
	if herr != nil {
		return writeJSON(out, map[string]any{"error": map[string]any{"code": herr.Code, "message": herr.Message}})
	}
	return writeJSON(out, map[string]any{"code": res.Code, "data": res.Data})
}

func check(args []string) error {
	fs := flag.NewFlagSet("check", flag.ExitOnError)
	configPath := fs.String("config", defaultConfig, "the saved configuration")
	_ = fs.Parse(args)
	spec, err := catalog.Load()
	if err != nil {
		return err
	}
	fmt.Printf("catalogue: %d commands, protocol %d\n", len(spec.Commands), spec.Protocol)
	cfg, err := panel.LoadConfig(*configPath)
	if err != nil {
		fmt.Println("configuration: not enrolled yet")
		return nil
	}
	if _, err := envelope.PublicKey(cfg.PublicKey); err != nil {
		return err
	}
	fmt.Printf("configuration: node %d, panel %s, capabilities %s\n", cfg.NodeID, cfg.Panel, strings.Join(cfg.Capabilities, ","))
	if expires, err := panel.CertExpires(cfg); err == nil {
		fmt.Println("certificate valid until", expires.Format("2006-01-02"))
	}
	return nil
}
