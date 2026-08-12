package main

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestDownloadedCoordinationDoesNotTerminateAppleIndexing(t *testing.T) {
	coordinator := newIndexResourceCoordinator(time.Millisecond)
	defer coordinator.close()
	appleStarted := make(chan struct{})
	appleStopped := make(chan struct{})
	releaseApple := make(chan struct{})
	downloadedStarted := make(chan struct{})

	coordinator.schedule(backgroundIndexApple, func(ctx context.Context) {
		close(appleStarted)
		select {
		case <-ctx.Done():
			close(appleStopped)
		case <-releaseApple:
		}
	})
	select {
	case <-appleStarted:
	case <-time.After(time.Second):
		t.Fatal("Apple background job did not start")
	}
	coordinator.schedule(backgroundIndexDownloaded, func(context.Context) {
		close(downloadedStarted)
	})
	select {
	case <-downloadedStarted:
	case <-time.After(time.Second):
		t.Fatal("Downloaded background job did not start")
	}
	select {
	case <-appleStopped:
		t.Fatal("Downloaded reconciliation cancelled the active Apple index")
	default:
	}
	close(releaseApple)
}

func TestCoordinatorStagesLocalReconciliationBeforeAppleStartup(t *testing.T) {
	coordinator := newIndexResourceCoordinator(10 * time.Millisecond)
	defer coordinator.close()
	starts := make(chan backgroundIndexKind, 2)
	coordinator.schedule(backgroundIndexApple, func(context.Context) {
		starts <- backgroundIndexApple
	})
	coordinator.schedule(backgroundIndexDownloaded, func(context.Context) {
		starts <- backgroundIndexDownloaded
	})
	select {
	case first := <-starts:
		if first != backgroundIndexDownloaded {
			t.Fatalf("first staged background job=%q, want downloaded", first)
		}
	case <-time.After(time.Second):
		t.Fatal("staged background jobs did not start")
	}
	select {
	case second := <-starts:
		if second != backgroundIndexApple {
			t.Fatalf("second staged background job=%q, want Apple", second)
		}
	case <-time.After(time.Second):
		t.Fatal("Apple background job did not follow downloaded startup")
	}
}

func TestExplicitWorkPreemptsAndResumesBackgroundJobsWithoutBlocking(t *testing.T) {
	coordinator := newIndexResourceCoordinator(time.Millisecond)
	defer coordinator.close()
	firstAppleStarted := make(chan struct{})
	firstAppleStopped := make(chan struct{})
	resumedApple := make(chan struct{})
	var appleRuns atomic.Int32

	coordinator.schedule(backgroundIndexApple, func(ctx context.Context) {
		run := appleRuns.Add(1)
		if run == 1 {
			close(firstAppleStarted)
			<-ctx.Done()
			close(firstAppleStopped)
			return
		}
		close(resumedApple)
	})
	select {
	case <-firstAppleStarted:
	case <-time.After(time.Second):
		t.Fatal("Apple background job did not start")
	}

	started := time.Now()
	releaseExplicit := coordinator.beginExplicit()
	if elapsed := time.Since(started); elapsed > 100*time.Millisecond {
		t.Fatalf("explicit priority acquisition blocked for %v", elapsed)
	}
	select {
	case <-firstAppleStopped:
	case <-time.After(time.Second):
		t.Fatal("explicit work did not cancel the background job")
	}
	select {
	case <-resumedApple:
		t.Fatal("background job resumed before explicit work released priority")
	case <-time.After(20 * time.Millisecond):
	}
	releaseExplicit()
	select {
	case <-resumedApple:
	case <-time.After(time.Second):
		t.Fatal("background job did not resume after explicit work")
	}
}

func TestCoordinatorCloseDoesNotDeadlockCancelledWorkers(t *testing.T) {
	coordinator := newIndexResourceCoordinator(time.Millisecond)
	started := make(chan struct{})
	workerDone := make(chan struct{})
	coordinator.schedule(backgroundIndexDownloaded, func(ctx context.Context) {
		close(started)
		<-ctx.Done()
		close(workerDone)
	})
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("background worker did not start")
	}
	closed := make(chan struct{})
	go func() {
		coordinator.close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("coordinator close deadlocked")
	}
	select {
	case <-workerDone:
	case <-time.After(time.Second):
		t.Fatal("cancelled worker did not exit")
	}
}

func TestEmbeddedArtworkExtractionIsSingleflight(t *testing.T) {
	var group embeddedArtworkSingleflight
	const callers = 24
	start := make(chan struct{})
	finishExtraction := make(chan struct{})
	var calls atomic.Int32
	var waiters sync.WaitGroup
	waiters.Add(callers)
	for index := 0; index < callers; index++ {
		go func() {
			defer waiters.Done()
			<-start
			path, err := group.do(context.Background(), "same-artwork", func() (string, error) {
				calls.Add(1)
				<-finishExtraction
				return "cached.png", nil
			})
			if err != nil || path != "cached.png" {
				t.Errorf("singleflight result path=%q err=%v", path, err)
			}
		}()
	}
	close(start)
	deadline := time.Now().Add(time.Second)
	for calls.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if calls.Load() != 1 {
		t.Fatalf("started %d artwork extractions, want 1", calls.Load())
	}
	// Keep the owner blocked long enough for all concurrent callers to join it.
	time.Sleep(20 * time.Millisecond)
	close(finishExtraction)
	done := make(chan struct{})
	go func() {
		waiters.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("singleflight callers did not finish")
	}
	if calls.Load() != 1 {
		t.Fatalf("ran %d artwork extractions, want 1", calls.Load())
	}
}
