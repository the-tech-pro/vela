package main

import (
	"context"
	"sort"
	"sync"
	"time"
)

type backgroundIndexKind string

const (
	backgroundIndexApple      backgroundIndexKind = "apple"
	backgroundIndexDownloaded backgroundIndexKind = "downloaded"
	backgroundIndexStageDelay                     = 75 * time.Millisecond
)

type backgroundIndexJob struct {
	kind backgroundIndexKind
	run  func(context.Context)
}

type activeBackgroundIndex struct {
	job    backgroundIndexJob
	token  uint64
	cancel context.CancelFunc
}

// indexResourceCoordinator staggers background starts and gives explicit
// downloads and iPod writes a non-blocking preemption path. It never waits for
// a worker while holding its mutex.
type indexResourceCoordinator struct {
	mu         sync.Mutex
	stageDelay time.Duration
	pending    map[backgroundIndexKind]backgroundIndexJob
	active     map[backgroundIndexKind]activeBackgroundIndex
	timer      *time.Timer
	nextToken  uint64
	explicit   int
	closed     bool
}

func newIndexResourceCoordinator(stageDelay time.Duration) *indexResourceCoordinator {
	return &indexResourceCoordinator{
		stageDelay: stageDelay,
		pending:    make(map[backgroundIndexKind]backgroundIndexJob),
		active:     make(map[backgroundIndexKind]activeBackgroundIndex),
	}
}

func (c *indexResourceCoordinator) schedule(kind backgroundIndexKind, run func(context.Context)) bool {
	if c == nil || run == nil {
		return false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed {
		return false
	}
	if _, running := c.active[kind]; running {
		return false
	}
	if _, queued := c.pending[kind]; queued {
		c.pending[kind] = backgroundIndexJob{kind: kind, run: run}
		return false
	}
	c.pending[kind] = backgroundIndexJob{kind: kind, run: run}
	c.armLocked()
	return true
}

func (c *indexResourceCoordinator) armLocked() {
	if c.closed || c.explicit > 0 || c.timer != nil || !c.hasRunnablePendingLocked() {
		return
	}
	c.timer = time.AfterFunc(c.stageDelay, c.dispatch)
}

func (c *indexResourceCoordinator) hasRunnablePendingLocked() bool {
	for kind := range c.pending {
		if _, running := c.active[kind]; !running {
			return true
		}
	}
	return false
}

func (c *indexResourceCoordinator) dispatch() {
	c.mu.Lock()
	c.timer = nil
	if c.closed || c.explicit > 0 {
		c.mu.Unlock()
		return
	}
	job, ok := c.nextPendingLocked()
	if !ok {
		c.mu.Unlock()
		return
	}
	delete(c.pending, job.kind)
	ctx, cancel := context.WithCancel(context.Background())
	c.nextToken++
	active := activeBackgroundIndex{job: job, token: c.nextToken, cancel: cancel}
	c.active[job.kind] = active
	c.armLocked()
	c.mu.Unlock()

	go func() {
		defer c.complete(job.kind, active.token)
		job.run(ctx)
	}()
}

func (c *indexResourceCoordinator) nextPendingLocked() (backgroundIndexJob, bool) {
	for _, kind := range []backgroundIndexKind{backgroundIndexDownloaded, backgroundIndexApple} {
		if job, ok := c.pending[kind]; ok {
			if _, running := c.active[kind]; !running {
				return job, true
			}
		}
	}
	kinds := make([]string, 0, len(c.pending))
	for kind := range c.pending {
		if _, running := c.active[kind]; !running {
			kinds = append(kinds, string(kind))
		}
	}
	sort.Strings(kinds)
	if len(kinds) == 0 {
		return backgroundIndexJob{}, false
	}
	job, ok := c.pending[backgroundIndexKind(kinds[0])]
	return job, ok
}

func (c *indexResourceCoordinator) complete(kind backgroundIndexKind, token uint64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	active, ok := c.active[kind]
	if !ok || active.token != token {
		return
	}
	delete(c.active, kind)
	c.armLocked()
}

func (c *indexResourceCoordinator) beginExplicit() func() {
	if c == nil {
		return func() {}
	}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return func() {}
	}
	c.explicit++
	if c.timer != nil {
		c.timer.Stop()
		c.timer = nil
	}
	cancellations := make([]context.CancelFunc, 0, len(c.active))
	for kind, active := range c.active {
		if _, queued := c.pending[kind]; !queued {
			c.pending[kind] = active.job
		}
		cancellations = append(cancellations, active.cancel)
	}
	c.mu.Unlock()
	for _, cancel := range cancellations {
		cancel()
	}

	var once sync.Once
	return func() {
		once.Do(func() {
			c.mu.Lock()
			if c.explicit > 0 {
				c.explicit--
			}
			c.armLocked()
			c.mu.Unlock()
		})
	}
}

func (c *indexResourceCoordinator) cancelKind(kind backgroundIndexKind) {
	if c == nil {
		return
	}
	c.mu.Lock()
	delete(c.pending, kind)
	active, running := c.active[kind]
	c.mu.Unlock()
	if running {
		active.cancel()
	}
}

func (c *indexResourceCoordinator) close() {
	if c == nil {
		return
	}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return
	}
	c.closed = true
	if c.timer != nil {
		c.timer.Stop()
		c.timer = nil
	}
	c.pending = make(map[backgroundIndexKind]backgroundIndexJob)
	cancellations := make([]context.CancelFunc, 0, len(c.active))
	for _, active := range c.active {
		cancellations = append(cancellations, active.cancel)
	}
	c.mu.Unlock()
	for _, cancel := range cancellations {
		cancel()
	}
}

func (a *App) libraryIndexCoordinator() *indexResourceCoordinator {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.indexCoordinator == nil {
		a.indexCoordinator = newIndexResourceCoordinator(backgroundIndexStageDelay)
	}
	return a.indexCoordinator
}

func (a *App) beginExplicitLibraryWork() func() {
	return a.libraryIndexCoordinator().beginExplicit()
}

func (a *App) closeLibraryIndexCoordinator() {
	a.mu.Lock()
	coordinator := a.indexCoordinator
	a.mu.Unlock()
	if coordinator != nil {
		coordinator.close()
	}
}
