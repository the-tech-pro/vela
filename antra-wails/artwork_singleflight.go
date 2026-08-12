package main

import (
	"context"
	"fmt"
	"sync"
)

type embeddedArtworkCall struct {
	done chan struct{}
	path string
	err  error
}

type embeddedArtworkSingleflight struct {
	mu    sync.Mutex
	calls map[string]*embeddedArtworkCall
}

func (g *embeddedArtworkSingleflight) do(
	ctx context.Context,
	key string,
	extract func() (string, error),
) (string, error) {
	g.mu.Lock()
	if g.calls == nil {
		g.calls = make(map[string]*embeddedArtworkCall)
	}
	if call, ok := g.calls[key]; ok {
		g.mu.Unlock()
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-call.done:
			return call.path, call.err
		}
	}
	call := &embeddedArtworkCall{done: make(chan struct{})}
	g.calls[key] = call
	g.mu.Unlock()

	var panicValue interface{}
	func() {
		defer func() {
			if recovered := recover(); recovered != nil {
				call.err = fmt.Errorf("embedded artwork extraction panic: %v", recovered)
				panicValue = recovered
			}
			close(call.done)
			g.mu.Lock()
			delete(g.calls, key)
			g.mu.Unlock()
		}()
		call.path, call.err = extract()
	}()
	if panicValue != nil {
		panic(panicValue)
	}
	return call.path, call.err
}
