package main

import (
	"fmt"
	"strings"
	"testing"
)

func TestPerfFlagEnabledUsesExplicitTruthyValues(t *testing.T) {
	for _, value := range []string{"1", "true", "TRUE", " yes ", "on", "debug"} {
		if !perfFlagEnabled(value) {
			t.Fatalf("expected %q to enable performance measurements", value)
		}
	}
	for _, value := range []string{"", "0", "false", "off", "production", "2"} {
		if perfFlagEnabled(value) {
			t.Fatalf("expected %q to leave performance measurements disabled", value)
		}
	}
}

func TestNewAppOnlyAllocatesPerfRecorderWhenEnabled(t *testing.T) {
	t.Setenv(velaPerfEnv, "")
	if app := NewApp(); app.perf != nil {
		t.Fatal("performance recorder should be absent by default")
	}

	t.Setenv(velaPerfEnv, "1")
	if app := NewApp(); app.perf == nil {
		t.Fatal("performance recorder should be present when VELA_PERF is enabled")
	}
}

func TestPerfSpanRecordsDurationPayloadStatusAndCounters(t *testing.T) {
	var lines []string
	recorder := newPerfRecorder(func(format string, args ...interface{}) {
		lines = append(lines, fmt.Sprintf(format, args...))
	})

	recorder.increment("backend_commands")
	recorder.increment("backend_spawns")
	span := recorder.start("backend.apple_library")
	span.finish(
		321,
		nil,
		perfCount{name: "ffprobe_count", value: 7},
		perfCount{name: "event_count", value: 2},
	)

	if len(lines) != 1 {
		t.Fatalf("expected one local timing line, got %d", len(lines))
	}
	line := lines[0]
	for _, fragment := range []string{
		"[TIMING] go backend.apple_library",
		"count=1",
		"payload_bytes=321",
		"status=ok",
		"event_count=2",
		"ffprobe_count=7",
	} {
		if !strings.Contains(line, fragment) {
			t.Fatalf("timing line %q is missing %q", line, fragment)
		}
	}
	if recorder.counter("backend_commands") != 1 {
		t.Fatal("backend command counter was not retained")
	}
	if recorder.counter("backend_spawns") != 1 {
		t.Fatal("backend spawn counter was not retained")
	}
}

func BenchmarkDisabledPerfSpan(b *testing.B) {
	app := &App{}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		if span := app.beginPerf("disabled"); span != nil {
			b.Fatal("disabled instrumentation unexpectedly allocated a span")
		}
	}
}
