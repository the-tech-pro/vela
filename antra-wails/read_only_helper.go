package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"regexp"
	"strings"
	"sync"
	"time"
)

const (
	readOnlyHelperProtocolVersion  = 1
	readOnlyHelperMaxResponseBytes = 64 * 1024 * 1024
	readOnlyHelperStopGrace        = 750 * time.Millisecond
)

type helperProcessSpec struct {
	command string
	args    []string
	workDir string
	env     []string
}

type helperProcess struct {
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	stdout *bufio.Reader
	done   <-chan error
}

type readOnlyHelperClient struct {
	gate      chan struct{}
	closeCh   chan struct{}
	closeOnce sync.Once
	resolve   func() (helperProcessSpec, error)
	logf      func(string)
	onStart   func()
	process   *helperProcess
	nextID    uint64
	isClosed  bool
}

type readOnlyHelperRemoteError struct {
	Code    string
	Message string
}

func (e *readOnlyHelperRemoteError) Error() string {
	if e.Message != "" {
		return e.Message
	}
	if e.Code != "" {
		return e.Code
	}
	return "read-only helper command failed"
}

type readOnlyHelperProtocolError struct {
	message string
}

func (e *readOnlyHelperProtocolError) Error() string {
	return e.message
}

func newReadOnlyHelperClient(
	resolve func() (helperProcessSpec, error),
	logf func(string),
	onStart func(),
) *readOnlyHelperClient {
	gate := make(chan struct{}, 1)
	gate <- struct{}{}
	return &readOnlyHelperClient{
		gate:    gate,
		closeCh: make(chan struct{}),
		resolve: resolve,
		logf:    logf,
		onStart: onStart,
	}
}

func (h *readOnlyHelperClient) Call(
	ctx context.Context,
	command string,
	params interface{},
) (json.RawMessage, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-h.closeCh:
		return nil, errors.New("read-only helper is closed")
	case <-h.gate:
	}
	defer func() { h.gate <- struct{}{} }()

	select {
	case <-h.closeCh:
		return nil, errors.New("read-only helper is closed")
	default:
	}
	h.nextID++
	requestID := fmt.Sprintf("%d", h.nextID)
	request := map[string]interface{}{
		"protocol_version": readOnlyHelperProtocolVersion,
		"id":               requestID,
		"command":          command,
		"params":           params,
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	encoded = append(encoded, '\n')

	var transportErr error
	for attempt := 0; attempt < 2; attempt++ {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		select {
		case <-h.closeCh:
			return nil, errors.New("read-only helper is closed")
		default:
		}
		if h.process == nil {
			if err := h.startLocked(); err != nil {
				transportErr = err
				continue
			}
		}

		line, err := h.exchangeLocked(ctx, encoded)
		if err != nil {
			transportErr = err
			h.stopLocked(true)
			if ctxErr := ctx.Err(); ctxErr != nil {
				return nil, ctxErr
			}
			continue
		}

		result, err := parseReadOnlyHelperResponse(line, requestID)
		if _, protocolFailure := err.(*readOnlyHelperProtocolError); protocolFailure {
			h.stopLocked(true)
		}
		return result, err
	}
	if transportErr == nil {
		transportErr = errors.New("read-only helper did not respond")
	}
	return nil, transportErr
}

func (h *readOnlyHelperClient) exchangeLocked(
	ctx context.Context,
	request []byte,
) ([]byte, error) {
	process := h.process
	if process == nil {
		return nil, io.ErrClosedPipe
	}

	writeDone := make(chan error, 1)
	go func() {
		_, err := process.stdin.Write(request)
		writeDone <- err
	}()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-h.closeCh:
		return nil, errors.New("read-only helper is closed")
	case err := <-writeDone:
		if err != nil {
			return nil, err
		}
	}

	readDone := make(chan struct {
		line []byte
		err  error
	}, 1)
	go func() {
		line, err := readNDJSONLine(process.stdout, readOnlyHelperMaxResponseBytes)
		readDone <- struct {
			line []byte
			err  error
		}{line: line, err: err}
	}()
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case <-h.closeCh:
		return nil, errors.New("read-only helper is closed")
	case result := <-readDone:
		return result.line, result.err
	}
}

func (h *readOnlyHelperClient) startLocked() error {
	if h.resolve == nil {
		return errors.New("read-only helper resolver is not configured")
	}
	spec, err := h.resolve()
	if err != nil {
		return err
	}
	cmd := exec.Command(spec.command, spec.args...)
	hideProcess(cmd)
	if spec.workDir != "" {
		cmd.Dir = spec.workDir
	}
	if spec.env != nil {
		cmd.Env = spec.env
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		_ = stdin.Close()
		return err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		_ = stdin.Close()
		_ = stdout.Close()
		return err
	}
	if err := cmd.Start(); err != nil {
		_ = stdin.Close()
		_ = stdout.Close()
		_ = stderr.Close()
		return err
	}

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
		close(done)
	}()
	h.process = &helperProcess{
		cmd:    cmd,
		stdin:  stdin,
		stdout: bufio.NewReaderSize(stdout, 64*1024),
		done:   done,
	}
	if h.onStart != nil {
		h.onStart()
	}
	go h.readStderr(stderr)
	return nil
}

func (h *readOnlyHelperClient) readStderr(stderr io.Reader) {
	scanner := bufio.NewScanner(stderr)
	scanner.Buffer(make([]byte, 16*1024), 1024*1024)
	for scanner.Scan() {
		line := sanitizeHelperDiagnostic(scanner.Text())
		if line != "" && h.logf != nil {
			h.logf(line)
		}
	}
	if scanner.Err() != nil && h.logf != nil {
		h.logf("stderr stream ended unexpectedly")
	}
}

func (h *readOnlyHelperClient) stopLocked(force bool) {
	process := h.process
	h.process = nil
	if process == nil {
		return
	}
	_ = process.stdin.Close()
	if !force {
		select {
		case <-process.done:
			return
		case <-time.After(readOnlyHelperStopGrace):
		}
	}
	_ = killCommandTree(process.cmd)
	select {
	case <-process.done:
	case <-time.After(5 * time.Second):
	}
}

func (h *readOnlyHelperClient) Close() {
	if h == nil {
		return
	}
	h.closeOnce.Do(func() { close(h.closeCh) })
	<-h.gate
	defer func() { h.gate <- struct{}{} }()
	if h.isClosed {
		return
	}
	h.isClosed = true
	h.stopLocked(false)
}

func parseReadOnlyHelperResponse(line []byte, expectedID string) (json.RawMessage, error) {
	var response struct {
		ID     string          `json:"id"`
		OK     *bool           `json:"ok"`
		Result json.RawMessage `json:"result"`
		Error  struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(bytes.TrimSpace(line), &response); err != nil {
		return nil, &readOnlyHelperProtocolError{message: "invalid read-only helper response"}
	}
	if response.ID != expectedID {
		return nil, &readOnlyHelperProtocolError{
			message: fmt.Sprintf(
				"read-only helper response id mismatch: expected %q, received %q",
				expectedID,
				response.ID,
			),
		}
	}
	if response.OK == nil {
		return nil, &readOnlyHelperProtocolError{message: "read-only helper response omitted status"}
	}
	if !*response.OK {
		return nil, &readOnlyHelperRemoteError{
			Code:    response.Error.Code,
			Message: response.Error.Message,
		}
	}
	if len(response.Result) == 0 {
		return nil, &readOnlyHelperProtocolError{message: "read-only helper response omitted result"}
	}
	return append(json.RawMessage(nil), response.Result...), nil
}

func readNDJSONLine(reader *bufio.Reader, maxBytes int) ([]byte, error) {
	var line []byte
	for {
		fragment, err := reader.ReadSlice('\n')
		if len(line)+len(fragment) > maxBytes {
			return nil, fmt.Errorf("read-only helper response exceeds %d bytes", maxBytes)
		}
		line = append(line, fragment...)
		switch {
		case err == nil:
			return bytes.TrimSuffix(line, []byte{'\n'}), nil
		case errors.Is(err, bufio.ErrBufferFull):
			continue
		case errors.Is(err, io.EOF) && len(line) > 0:
			return nil, io.ErrUnexpectedEOF
		default:
			return nil, err
		}
	}
}

var (
	helperSecretAssignment = regexp.MustCompile(
		`(?i)\b(authorization|password|secret|cookie|sp_dc|arl|(?:music[_ -]?user|access|refresh)[_ -]?token)(\s*[:=]\s*)([^\s,;]+)`,
	)
	helperBearerToken = regexp.MustCompile(`(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+`)
	helperQuerySecret = regexp.MustCompile(
		`(?i)([?&](?:token|key|secret|authorization|signature)=)[^&#\s]+`,
	)
)

func sanitizeHelperDiagnostic(value string) string {
	value = strings.TrimSpace(value)
	value = helperSecretAssignment.ReplaceAllString(value, `${1}${2}<redacted>`)
	value = helperBearerToken.ReplaceAllString(value, "Bearer <redacted>")
	value = helperQuerySecret.ReplaceAllString(value, `${1}<redacted>`)
	if len(value) > 2000 {
		value = value[:2000]
	}
	return value
}

func (a *App) getReadOnlyHelper() (*readOnlyHelperClient, error) {
	a.readOnlyHelperMu.Lock()
	defer a.readOnlyHelperMu.Unlock()
	if a.readOnlyHelperClosed {
		return nil, errors.New("read-only helper is closed")
	}
	if a.readOnlyHelper == nil {
		a.readOnlyHelper = newReadOnlyHelperClient(
			a.resolveReadOnlyHelperProcess,
			func(message string) {
				a.logWarningf("Read-only backend: %s", message)
			},
			func() { a.incrementPerf("backend_spawns") },
		)
	}
	return a.readOnlyHelper, nil
}

func (a *App) resolveReadOnlyHelperProcess() (helperProcessSpec, error) {
	command, args, workDir, env, err := a.resolveBackendCommand(nil)
	if err != nil {
		return helperProcessSpec{}, err
	}
	args = append(append([]string(nil), args...), "--read-only-helper")
	return helperProcessSpec{
		command: command,
		args:    args,
		workDir: workDir,
		env:     env,
	}, nil
}

func (a *App) callReadOnlyHelper(
	ctx context.Context,
	command string,
	params interface{},
) (json.RawMessage, error) {
	span := a.beginBackendPerf("helper." + command)
	helper, err := a.getReadOnlyHelper()
	if err != nil {
		span.finish(0, err)
		return nil, err
	}
	result, err := helper.Call(ctx, command, params)
	span.finish(len(result), err)
	return result, err
}

func (a *App) closeReadOnlyHelper() {
	a.readOnlyHelperMu.Lock()
	helper := a.readOnlyHelper
	a.readOnlyHelper = nil
	a.readOnlyHelperClosed = true
	a.readOnlyHelperMu.Unlock()
	if helper != nil {
		helper.Close()
	}
}
