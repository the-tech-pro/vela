package main

import (
	"fmt"
	"log"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

const velaPerfEnv = "VELA_PERF"

type perfLogFunc func(format string, args ...interface{})

type perfRecorder struct {
	mu       sync.Mutex
	started  time.Time
	counters map[string]uint64
	logf     perfLogFunc
}

type perfSpan struct {
	recorder *perfRecorder
	name     string
	sequence uint64
	started  time.Time
}

type perfCount struct {
	name  string
	value uint64
}

func perfFlagEnabled(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on", "debug":
		return true
	default:
		return false
	}
}

func newPerfRecorderFromEnv() *perfRecorder {
	if !perfFlagEnabled(os.Getenv(velaPerfEnv)) {
		return nil
	}
	return newPerfRecorder(log.Printf)
}

func newPerfRecorder(logf perfLogFunc) *perfRecorder {
	if logf == nil {
		logf = log.Printf
	}
	return &perfRecorder{
		started:  time.Now(),
		counters: make(map[string]uint64),
		logf:     logf,
	}
}

func (p *perfRecorder) increment(name string) uint64 {
	if p == nil {
		return 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.counters[name]++
	return p.counters[name]
}

func (p *perfRecorder) counter(name string) uint64 {
	if p == nil {
		return 0
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.counters[name]
}

func (p *perfRecorder) mark(name string) {
	if p == nil {
		return
	}
	p.logf("[TIMING] go %s %.2fms", name, float64(time.Since(p.started).Microseconds())/1000)
}

func (p *perfRecorder) start(name string) *perfSpan {
	if p == nil {
		return nil
	}
	return &perfSpan{
		recorder: p,
		name:     name,
		sequence: p.increment("span." + name),
		started:  time.Now(),
	}
}

func (s *perfSpan) finish(payloadBytes int, err error, counts ...perfCount) {
	if s == nil || s.recorder == nil {
		return
	}
	status := "ok"
	if err != nil {
		status = "error"
	}
	sort.Slice(counts, func(i, j int) bool {
		return counts[i].name < counts[j].name
	})
	extra := ""
	for _, count := range counts {
		extra += fmt.Sprintf(" %s=%d", count.name, count.value)
	}
	s.recorder.logf(
		"[TIMING] go %s %.2fms count=%d payload_bytes=%d status=%s%s",
		s.name,
		float64(time.Since(s.started).Microseconds())/1000,
		s.sequence,
		max(0, payloadBytes),
		status,
		extra,
	)
}

func (a *App) beginPerf(name string) *perfSpan {
	if a == nil || a.perf == nil {
		return nil
	}
	return a.perf.start(name)
}

func (a *App) beginBackendPerf(name string) *perfSpan {
	if a == nil || a.perf == nil {
		return nil
	}
	a.perf.increment("backend_commands")
	return a.perf.start("backend." + name)
}

func (a *App) incrementPerf(name string) uint64 {
	if a == nil || a.perf == nil {
		return 0
	}
	return a.perf.increment(name)
}

func (a *App) perfCounter(name string) uint64 {
	if a == nil || a.perf == nil {
		return 0
	}
	return a.perf.counter(name)
}

func (a *App) markPerf(name string) {
	if a == nil || a.perf == nil {
		return
	}
	a.perf.mark(name)
}

func (a *App) logPerfSummary() {
	if a == nil || a.perf == nil {
		return
	}
	a.perf.logf(
		"[TIMING] go summary backend_commands=%d backend_spawns=%d ffprobe_count=%d ffmpeg_count=%d",
		a.perf.counter("backend_commands"),
		a.perf.counter("backend_spawns"),
		a.perf.counter("ffprobe"),
		a.perf.counter("ffmpeg"),
	)
}
