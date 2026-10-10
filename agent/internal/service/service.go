// Package service is the agent's main loop: fetch a signed command, check it, carry it out, report; send heartbeats; run the recurring jobs; renew the certificate before it expires.
// Everything in it can be driven one step at a time (Once, Beat, Maintain) so it is tested without waiting for clocks.
package service

import (
	"crypto/ed25519"
	"fmt"
	"log"
	"sync"
	"time"

	"whagent/internal/catalog"
	"whagent/internal/envelope"
	"whagent/internal/handlers"
	"whagent/internal/panel"
)

// Service wires the parts together.
type Service struct {
	Spec      *catalog.Spec
	Agent     *handlers.Agent
	Client    *panel.Client
	Guard     *envelope.Guard
	PublicKey ed25519.PublicKey
	NodeID    int64
	Metrics   *handlers.Metrics
	Jobs      []handlers.Job
	Log       *log.Logger
	Now       func() time.Time
	// WaitSeconds is how long WHPanel may hold a poll open (long polling): small for a customer-facing screen waiting for an answer, at most 25.
	WaitSeconds int
	// RenewBefore is how long before expiry the certificate is renewed.
	RenewBefore time.Duration
	mu          sync.Mutex
}

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}

func (s *Service) logf(format string, a ...any) {
	if s.Log != nil {
		s.Log.Printf(format, a...)
	}
}

// Once fetches at most one command and handles it. It returns true when there was one.
func (s *Service) Once() (bool, error) {
	env, err := s.Client.Poll(s.WaitSeconds)
	if err != nil {
		return false, err
	}
	if env == nil {
		return false, nil
	}
	payload, err := envelope.Verify(s.Spec, envelope.Envelope{Payload: env.Payload, Signature: env.Signature}, s.PublicKey, s.NodeID, s.Guard, s.now())
	if err != nil {
		if payload == nil {
			s.logf("dropped an envelope that failed its signature check (not reported: it cannot be trusted)")
			return true, nil
		}
		s.logf("refused job %d: %v", payload.Job, err)
		return true, s.Client.Result(payload.Job, panel.Report{OK: false, Code: "refused", Message: clip(err.Error(), 200)})
	}
	s.mu.Lock() // one command at a time on a node
	res, herr := s.Agent.Run(payload.Command, payload.RawParams)
	s.mu.Unlock()
	report := panel.Report{OK: herr == nil, Code: res.Code, Data: res.Data}
	if herr != nil {
		report.Code, report.Message = herr.Code, herr.Message
	}
	s.logf("job %d %s -> %s", payload.Job, payload.Command, report.Code)
	return true, s.Client.Result(payload.Job, report)
}

// Beat sends one heartbeat.
func (s *Service) Beat() error {
	if s.Metrics == nil {
		return nil
	}
	return s.Client.Heartbeat(s.Metrics.Collect())
}

// Maintain runs the recurring jobs that are due and renews the certificate when it is close to expiry.
func (s *Service) Maintain() [][2]string {
	s.mu.Lock()
	done := s.Agent.RunDue(s.Jobs)
	s.mu.Unlock()
	for _, d := range done {
		s.logf("job %s: %s", d[0], d[1])
	}
	if s.Client.Config != nil && s.Client.Config.CertFile != "" {
		if expires, err := panel.CertExpires(s.Client.Config); err == nil && expires.Sub(s.now()) < s.RenewBefore {
			if err := s.Client.Renew(); err != nil {
				s.logf("certificate renewal failed: %v", err)
			} else {
				s.logf("certificate renewed")
			}
		}
	}
	return done
}

func clip(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

// Run is the loop. It never returns until stop is closed. A failing poll waits and tries again; a bad day on the network must not stop the agent.
func (s *Service) Run(stop <-chan struct{}) {
	if s.WaitSeconds == 0 {
		s.WaitSeconds = 20
	}
	if s.RenewBefore == 0 {
		s.RenewBefore = 30 * 24 * time.Hour
	}
	go every(stop, 30*time.Second, func() {
		if err := s.Beat(); err != nil {
			s.logf("heartbeat failed: %v", err)
		}
	})
	go every(stop, time.Minute, func() { s.Maintain() })
	backoff := time.Second
	for {
		select {
		case <-stop:
			return
		default:
		}
		got, err := s.Once()
		switch {
		case err != nil:
			s.logf("poll failed: %v", err)
			sleep(stop, backoff)
			if backoff < 60*time.Second {
				backoff *= 2
			}
		default:
			backoff = time.Second
			if !got {
				sleep(stop, time.Second) // the poll already waited; a short pause keeps a misbehaving server from being hammered
			}
		}
	}
}

func every(stop <-chan struct{}, d time.Duration, f func()) {
	f()
	t := time.NewTicker(d)
	defer t.Stop()
	for {
		select {
		case <-stop:
			return
		case <-t.C:
			f()
		}
	}
}

func sleep(stop <-chan struct{}, d time.Duration) {
	select {
	case <-stop:
	case <-time.After(d):
	}
}

var _ = fmt.Sprint
