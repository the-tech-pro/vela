package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestReadOnlyHelperFixtureProcess(t *testing.T) {
	if os.Getenv("VELA_READ_HELPER_FIXTURE") != "1" {
		return
	}
	mode := os.Getenv("VELA_READ_HELPER_MODE")
	marker := os.Getenv("VELA_READ_HELPER_MARKER")
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 16*1024), 2*1024*1024)
	for scanner.Scan() {
		var request struct {
			ID      string                 `json:"id"`
			Command string                 `json:"command"`
			Params  map[string]interface{} `json:"params"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &request); err != nil {
			os.Exit(2)
		}
		switch mode {
		case "mismatch":
			fmt.Printf(`{"id":"wrong","ok":true,"result":{"command":%q}}`+"\n", request.Command)
		case "crash-once":
			if _, err := os.Stat(marker); errors.Is(err, os.ErrNotExist) {
				if writeErr := os.WriteFile(marker, []byte("crashed"), 0600); writeErr != nil {
					os.Exit(3)
				}
				os.Exit(7)
			}
			fmt.Printf(`{"id":%q,"ok":true,"result":{"restarted":true}}`+"\n", request.ID)
		case "slow":
			time.Sleep(2 * time.Second)
			fmt.Printf(`{"id":%q,"ok":true,"result":null}`+"\n", request.ID)
		case "remote-error":
			fmt.Printf(
				`{"id":%q,"ok":false,"error":{"code":"fixture","message":"fixture failure"}}`+"\n",
				request.ID,
			)
		default:
			result, _ := json.Marshal(request.Params)
			fmt.Printf(`{"id":%q,"ok":true,"result":%s}`+"\n", request.ID, result)
		}
	}
	os.Exit(0)
}

func helperFixtureResolver(
	mode string,
	marker string,
	starts *atomic.Int32,
) func() (helperProcessSpec, error) {
	return func() (helperProcessSpec, error) {
		starts.Add(1)
		env := append(
			os.Environ(),
			"VELA_READ_HELPER_FIXTURE=1",
			"VELA_READ_HELPER_MODE="+mode,
			"VELA_READ_HELPER_MARKER="+marker,
		)
		return helperProcessSpec{
			command: os.Args[0],
			args:    []string{"-test.run=^TestReadOnlyHelperFixtureProcess$"},
			env:     env,
		}, nil
	}
}

func TestReadOnlyHelperClientCorrelatesResponses(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("echo", "", &starts),
		nil,
		nil,
	)
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	result, err := client.Call(ctx, "echo", map[string]interface{}{"value": "hello"})
	if err != nil {
		t.Fatal(err)
	}
	var payload map[string]interface{}
	if err := json.Unmarshal(result, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["value"] != "hello" {
		t.Fatalf("unexpected helper result: %s", result)
	}
	if starts.Load() != 1 {
		t.Fatalf("helper starts = %d, want 1", starts.Load())
	}
}

func TestReadOnlyHelperClientSerializesConcurrentRequests(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("echo", "", &starts),
		nil,
		nil,
	)
	defer client.Close()

	const callers = 24
	errors := make(chan error, callers)
	var wait sync.WaitGroup
	for caller := range callers {
		wait.Add(1)
		go func() {
			defer wait.Done()
			expected := fmt.Sprintf("caller-%d", caller)
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			result, err := client.Call(ctx, "echo", map[string]interface{}{"value": expected})
			if err != nil {
				errors <- err
				return
			}
			var payload map[string]interface{}
			if err := json.Unmarshal(result, &payload); err != nil {
				errors <- err
				return
			}
			if payload["value"] != expected {
				errors <- fmt.Errorf("result = %q, want %q", payload["value"], expected)
			}
		}()
	}
	wait.Wait()
	close(errors)
	for err := range errors {
		t.Error(err)
	}
	if starts.Load() != 1 {
		t.Fatalf("concurrent requests started %d helpers, want 1", starts.Load())
	}
}

func TestReadOnlyHelperClientRejectsMismatchedResponseID(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("mismatch", "", &starts),
		nil,
		nil,
	)
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err := client.Call(ctx, "echo", map[string]interface{}{})
	var protocolErr *readOnlyHelperProtocolError
	if !errors.As(err, &protocolErr) {
		t.Fatalf("expected protocol error, got %v", err)
	}
}

func TestReadOnlyHelperClientRestartsAfterCrash(t *testing.T) {
	var starts atomic.Int32
	marker := t.TempDir() + string(os.PathSeparator) + "crashed"
	client := newReadOnlyHelperClient(
		helperFixtureResolver("crash-once", marker, &starts),
		nil,
		nil,
	)
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	result, err := client.Call(ctx, "echo", map[string]interface{}{})
	if err != nil {
		t.Fatal(err)
	}
	if string(result) != `{"restarted":true}` {
		t.Fatalf("unexpected restart result: %s", result)
	}
	if starts.Load() != 2 {
		t.Fatalf("helper starts = %d, want 2", starts.Load())
	}
}

func TestReadOnlyHelperClientTimeoutStopsHungProcess(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("slow", "", &starts),
		nil,
		nil,
	)
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 75*time.Millisecond)
	defer cancel()
	_, err := client.Call(ctx, "slow", map[string]interface{}{})
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected deadline exceeded, got %v", err)
	}
}

func TestReadOnlyHelperClientCloseInterruptsActiveRequest(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("slow", "", &starts),
		nil,
		nil,
	)
	result := make(chan error, 1)
	go func() {
		_, err := client.Call(context.Background(), "slow", map[string]interface{}{})
		result <- err
	}()

	deadline := time.Now().Add(5 * time.Second)
	for starts.Load() == 0 {
		if time.Now().After(deadline) {
			t.Fatal("helper did not start")
		}
		time.Sleep(10 * time.Millisecond)
	}
	client.Close()
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("active request unexpectedly succeeded during close")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("close did not interrupt active helper request")
	}
}

func TestReadOnlyHelperClientReturnsStructuredRemoteError(t *testing.T) {
	var starts atomic.Int32
	client := newReadOnlyHelperClient(
		helperFixtureResolver("remote-error", "", &starts),
		nil,
		nil,
	)
	defer client.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err := client.Call(ctx, "fail", nil)
	var remoteErr *readOnlyHelperRemoteError
	if !errors.As(err, &remoteErr) ||
		remoteErr.Code != "fixture" ||
		remoteErr.Message != "fixture failure" {
		t.Fatalf("unexpected remote error: %#v", err)
	}
}

func TestSanitizeHelperDiagnosticRedactsSecrets(t *testing.T) {
	input := "authorization=secret Bearer abc.def?token=hidden password:guess"
	got := sanitizeHelperDiagnostic(input)
	for _, secret := range []string{"secret", "abc.def", "hidden", "guess"} {
		if contains := regexpContainsLiteral(got, secret); contains {
			t.Fatalf("diagnostic retained %q: %q", secret, got)
		}
	}
}

func regexpContainsLiteral(value, literal string) bool {
	for index := 0; index+len(literal) <= len(value); index++ {
		if value[index:index+len(literal)] == literal {
			return true
		}
	}
	return false
}
