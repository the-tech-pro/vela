package main

import (
	"bufio"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRequestMapPreservesTypedIPodRequest(t *testing.T) {
	request := IPodPlanRequest{
		MountPath:   `E:\`,
		SourceFiles: []string{`C:\Music\one.mp3`, `C:\Music\two.m4a`},
		StagingID:   "stage-1",
	}
	payload, err := requestMap(request)
	if err != nil {
		t.Fatal(err)
	}
	if payload["mount_path"] != `E:\` || payload["staging_id"] != "stage-1" {
		t.Fatalf("unexpected payload: %#v", payload)
	}
	sources, ok := payload["source_files"].([]interface{})
	if !ok || len(sources) != 2 {
		t.Fatalf("source files not serialized: %#v", payload["source_files"])
	}
}

func TestPlanDetailsRequestIsBoundedByBackendContract(t *testing.T) {
	payload, err := requestMap(IPodPlanDetailsRequest{
		PlanID:   "opaque-plan",
		Group:    "metadata_updates",
		Page:     2,
		PageSize: 50,
	})
	if err != nil {
		t.Fatal(err)
	}
	if payload["plan_id"] != "opaque-plan" || payload["group"] != "metadata_updates" {
		t.Fatalf("unexpected detail request: %#v", payload)
	}
}

func TestValidatedCompletedLibraryFilesRejectsMissingAndOutside(t *testing.T) {
	root := t.TempDir()
	album := filepath.Join(root, "Album")
	if err := os.MkdirAll(album, 0755); err != nil {
		t.Fatal(err)
	}
	inside := filepath.Join(album, "one.flac")
	if err := os.WriteFile(inside, []byte("audio"), 0644); err != nil {
		t.Fatal(err)
	}
	outsideRoot := t.TempDir()
	outside := filepath.Join(outsideRoot, "provider-temp.flac")
	if err := os.WriteFile(outside, []byte("temporary"), 0644); err != nil {
		t.Fatal(err)
	}
	got := validatedCompletedLibraryFiles(
		[]string{inside, filepath.Join(root, "missing.flac"), outside, inside},
		root,
	)
	if len(got) != 1 {
		t.Fatalf("unexpected validated paths: %#v", got)
	}
	gotInfo, err := os.Stat(got[0])
	if err != nil {
		t.Fatal(err)
	}
	insideInfo, err := os.Stat(inside)
	if err != nil {
		t.Fatal(err)
	}
	if !os.SameFile(gotInfo, insideInfo) {
		t.Fatalf("validated %q, want the file %q", got[0], inside)
	}
}

func TestScanIPodEventsIgnoresNoiseAndKeepsStructuredProgress(t *testing.T) {
	input := strings.Join([]string{
		"backend diagnostic noise",
		`{"type":"ipod_progress","protocol_version":1,"stage":"backup","current":2,"total":3}`,
		`{"type":"ipod_execute","data":{"ok":true}}`,
	}, "\n")
	scanner := bufio.NewScanner(strings.NewReader(input))
	var events []map[string]interface{}
	if err := scanIPodEvents(scanner, func(event map[string]interface{}) {
		events = append(events, event)
	}); err != nil {
		t.Fatal(err)
	}
	if len(events) != 2 {
		t.Fatalf("got %d structured events", len(events))
	}
	if events[0]["type"] != "ipod_progress" || events[1]["type"] != "ipod_execute" {
		t.Fatalf("unexpected events: %#v", events)
	}
}

func TestThirdPartyNoticeContainsFullMITGrant(t *testing.T) {
	app := NewApp()
	notice := app.GetThirdPartyNotices()
	for _, required := range []string{
		"Copyright (c) John Gibbons and iOpenPod contributors",
		"Permission is hereby granted, free of charge",
		`THE SOFTWARE IS PROVIDED "AS IS"`,
		"Rockbox and the resulting modified helper are GPL-2.0-or-later",
		"Rockbox Utility 1.5.1 Windows archive",
		"3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d",
		"utils/mks5lboot",
		"Copyright (c) 2024 Olsro",
		"Apple's iPod Classic 2.0.2 IPSW is proprietary and is not redistributed",
		"pytsk3 20260715",
		"bounded, read-only",
		"Common Public License 1.0",
	} {
		if !strings.Contains(notice, required) {
			t.Fatalf("notice missing %q", required)
		}
	}
}

func TestRecoveryRequestSerialization(t *testing.T) {
	tests := []struct {
		name    string
		request interface{}
		want    map[string]interface{}
	}{
		{
			name: "snapshot",
			request: IPodBackupSnapshotRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1",
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1",
			},
		},
		{
			name: "snapshot page",
			request: IPodBackupSnapshotsRequest{
				ArchiveID: "archive-1", Page: 2, PageSize: 25,
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "page": float64(2), "page_size": float64(25),
			},
		},
		{
			name:    "manual backup",
			request: IPodManualBackupRequest{MountPath: `E:\`},
			want:    map[string]interface{}{"mount_path": `E:\`},
		},
		{
			name: "backup note",
			request: IPodBackupNoteRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1", Note: "Before migration",
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1", "note": "Before migration",
			},
		},
		{
			name: "backup export",
			request: IPodBackupExportRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1", DestinationDir: `C:\Exports`,
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1", "destination_dir": `C:\Exports`,
			},
		},
		{
			name: "backup delete",
			request: IPodBackupDeleteRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1", Confirmed: true,
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1", "confirmed": true,
			},
		},
		{
			name: "restore preflight",
			request: IPodRestorePreflightRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1", MountPath: `E:\`,
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1", "mount_path": `E:\`,
			},
		},
		{
			name: "confirmed restore",
			request: IPodRestoreRequest{
				RestorePlanID: "restore-plan-1", Confirmed: true,
			},
			want: map[string]interface{}{
				"restore_plan_id": "restore-plan-1", "confirmed": true,
			},
		},
		{
			name: "migration target",
			request: IPodMigrationPreflightRequest{
				ArchiveID: "archive-1", SnapshotID: "snapshot-1", MountPath: `F:\`,
			},
			want: map[string]interface{}{
				"archive_id": "archive-1", "snapshot_id": "snapshot-1", "mount_path": `F:\`,
			},
		},
		{
			name: "confirmed migration",
			request: IPodMigrationRequest{
				MigrationPlanID: "migration-plan-1", Confirmed: true,
			},
			want: map[string]interface{}{
				"migration_plan_id": "migration-plan-1", "confirmed": true,
			},
		},
		{
			name: "unlock status",
			request: IPodCapacityUnlockAdvanceRequest{
				SessionID: "session-1",
				Action:    "status",
				Confirmed: false,
				Data:      map[string]interface{}{"include_history": true},
			},
			want: map[string]interface{}{
				"session_id": "session-1",
				"action":     "status",
				"confirmed":  false,
				"data":       map[string]interface{}{"include_history": true},
			},
		},
		{
			name: "unlock eligibility",
			request: IPodCapacityUnlockEligibilityRequest{
				MountPath: `E:\`,
			},
			want: map[string]interface{}{"mount_path": `E:\`},
		},
		{
			name: "unlock start",
			request: IPodCapacityUnlockStartRequest{
				MountPath: `E:\`, Confirmed: true,
				Acknowledgements: map[string]bool{"destructive_wipe": true},
			},
			want: map[string]interface{}{
				"mount_path": `E:\`, "confirmed": true,
				"acknowledgements": map[string]interface{}{"destructive_wipe": true},
			},
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got, err := requestMap(test.request)
			if err != nil {
				t.Fatal(err)
			}
			if len(got) != len(test.want) {
				t.Fatalf("unexpected serialized fields: got %#v want %#v", got, test.want)
			}
			for key, want := range test.want {
				gotValue, ok := got[key]
				if !ok {
					t.Fatalf("serialized request is missing %q: %#v", key, got)
				}
				if wantMap, isMap := want.(map[string]interface{}); isMap {
					gotMap, mapOK := gotValue.(map[string]interface{})
					if !mapOK || len(gotMap) != len(wantMap) {
						t.Fatalf("unexpected %s map: %#v", key, gotValue)
					}
					for mapKey, mapWant := range wantMap {
						if gotMap[mapKey] != mapWant {
							t.Fatalf("unexpected %s.%s: %#v", key, mapKey, gotMap[mapKey])
						}
					}
					continue
				}
				if gotValue != want {
					t.Fatalf("unexpected %s: got %#v want %#v", key, gotValue, want)
				}
			}
		})
	}
}

func TestRecoveryOperationRoutesMatchPythonCLI(t *testing.T) {
	tests := []struct {
		route        ipodOperationRoute
		operation    string
		terminalType string
		kind         string
	}{
		{ipodBackupDevicesRoute, "backup-devices", "ipod_backup_devices", "backup"},
		{ipodBackupSnapshotsRoute, "backup-snapshots", "ipod_backup_snapshots", "backup"},
		{ipodBackupDetailsRoute, "backup-details", "ipod_backup_details", "backup"},
		{ipodBackupVerifyRoute, "backup-verify", "ipod_backup_verify", "backup_verify"},
		{ipodBackupManualRoute, "backup-manual", "ipod_backup_manual", "manual_backup"},
		{ipodBackupNoteRoute, "backup-note", "ipod_backup_note", "backup_note"},
		{ipodBackupExportRoute, "backup-export", "ipod_backup_export", "backup_export"},
		{ipodBackupDeleteRoute, "backup-delete", "ipod_backup_delete", "backup_delete"},
		{ipodRestorePreflightRoute, "restore-preflight", "ipod_restore_preflight", "restore"},
		{ipodRestoreRoute, "restore", "ipod_restore", "restore"},
		{ipodMigrationPreflightRoute, "migration-preflight", "ipod_migration_preflight", "migration"},
		{ipodMigrationRoute, "migration", "ipod_migration", "migration"},
		{ipodRecoveryStateRoute, "recovery-state", "ipod_recovery_state", "recovery"},
		{
			ipodCapacityUnlockEligibilityRoute,
			"capacity-unlock-eligibility",
			"ipod_capacity_unlock_eligibility",
			"capacity_unlock",
		},
		{
			ipodCapacityUnlockStartRoute,
			"capacity-unlock-start",
			"ipod_capacity_unlock_start",
			"capacity_unlock",
		},
		{
			ipodCapacityUnlockAdvanceRoute,
			"capacity-unlock-advance",
			"ipod_capacity_unlock_advance",
			"capacity_unlock",
		},
	}

	for _, test := range tests {
		if test.route.operation != test.operation ||
			test.route.terminalType != test.terminalType ||
			test.route.kind != test.kind {
			t.Fatalf("unexpected route: %#v", test.route)
		}
	}
}

func TestIPodRequestTimeoutsAreRouteBounded(t *testing.T) {
	if got := ipodRequestTimeout("browse"); got != 5*time.Minute {
		t.Fatalf("normal read timeout = %s, want 5m", got)
	}
	for _, operation := range []string{
		"backup-verify",
		"restore-preflight",
		"migration-preflight",
	} {
		if got := ipodRequestTimeout(operation); got != 2*time.Hour {
			t.Fatalf("%s timeout = %s, want 2h", operation, got)
		}
	}
	if got := ipodRequestTimeout("migration"); got != 5*time.Minute {
		t.Fatalf("mutation route unexpectedly received extended read timeout: %s", got)
	}
}

func TestCapacityUnlockStatusAndListDoNotRequireConfirmation(t *testing.T) {
	for _, action := range []string{"status", " STATUS ", "list", "List"} {
		if capacityUnlockActionRequiresConfirmation(action) {
			t.Fatalf("%q should be read-only", action)
		}
	}
	for _, action := range []string{"", "advance", "acknowledge_manual_nor", "download"} {
		if !capacityUnlockActionRequiresConfirmation(action) {
			t.Fatalf("%q must require confirmation", action)
		}
	}
}

func TestStartIPodMigrationRequiresConfirmation(t *testing.T) {
	err := NewApp().StartIPodMigration(IPodMigrationRequest{
		MigrationPlanID: "migration-plan-1",
		Confirmed:       false,
	})
	if err == nil || !strings.Contains(err.Error(), "confirmed") {
		t.Fatalf("unconfirmed migration returned %v", err)
	}
}

func TestIPodMutationBoundaryUsesStructuredProgress(t *testing.T) {
	phase, canCancel := updateIPodMutationBoundary("running", true, map[string]interface{}{
		"type": "ipod_progress", "stage": "restore:copying", "can_cancel": true,
	})
	if phase != "restore:copying" || !canCancel {
		t.Fatalf("copy phase should remain cancellable: %q %v", phase, canCancel)
	}

	phase, canCancel = updateIPodMutationBoundary(phase, canCancel, map[string]interface{}{
		"type": "ipod_progress", "event": "commit_started", "can_cancel": true,
	})
	if phase != "commit_started" || canCancel {
		t.Fatalf("commit must override a contradictory cancel flag: %q %v", phase, canCancel)
	}

	phase, canCancel = updateIPodMutationBoundary("running", true, map[string]interface{}{
		"data": map[string]interface{}{"phase": "finalizing", "canCancel": false},
	})
	if phase != "finalizing" || canCancel {
		t.Fatalf("nested finalizing state must be protected: %q %v", phase, canCancel)
	}

	phase, canCancel = updateIPodMutationBoundary("cancelling", false, map[string]interface{}{
		"phase": "copying", "can_cancel": true,
	})
	if phase != "cancelling" || canCancel {
		t.Fatalf("late progress must not undo a cancellation request: %q %v", phase, canCancel)
	}
}

func TestBeforeCloseDecisionOnlyProtectsCommitBoundary(t *testing.T) {
	for _, phase := range []string{"restore:commit", "finalizing", "database-flush"} {
		if !shouldPreventIPodClose(true, phase) {
			t.Fatalf("close should be prevented during %q", phase)
		}
	}
	for _, phase := range []string{"", "starting", "copying", "cancelling"} {
		if shouldPreventIPodClose(true, phase) {
			t.Fatalf("close should be allowed during %q", phase)
		}
	}
	if shouldPreventIPodClose(false, "committing") {
		t.Fatal("an inactive operation must not block close")
	}
}

func TestIPodOperationEnvelopeUsesHostIdentity(t *testing.T) {
	event := withIPodOperationMetadata(
		map[string]interface{}{"type": "ipod_progress", "operation_id": "backend-id"},
		"restore",
		"host-id",
		"restore",
	)
	if event["operation_id"] != "host-id" || event["kind"] != "restore" || event["operation"] != "restore" {
		t.Fatalf("unexpected operation envelope: %#v", event)
	}
	if event["backend_operation_id"] != "backend-id" {
		t.Fatalf("backend journal identity was not preserved: %#v", event)
	}
}

func TestCancelIPodOperationRejectsProtectedPhase(t *testing.T) {
	cancelPath := filepath.Join(t.TempDir(), "cancel")
	app := NewApp()
	app.ipodCancelPath = cancelPath
	app.ipodMutationOperationID = "restore-1"
	app.ipodMutationPhase = "committing"
	app.ipodMutationCanCancel = true

	if err := app.CancelIPodOperationByID("restore-1"); err == nil {
		t.Fatal("commit phase cancellation should be rejected")
	}
	if _, err := os.Stat(cancelPath); !os.IsNotExist(err) {
		t.Fatalf("protected cancellation wrote a sentinel: %v", err)
	}
}

func TestShutdownDoesNotKillProtectedIPodCommit(t *testing.T) {
	app := NewApp()
	command := &exec.Cmd{}
	app.ipodMutationCmd = command
	app.ipodMutationOperationID = "restore-1"
	app.ipodMutationPhase = "restore:flush"
	app.ipodMutationCanCancel = false
	app.indexRestartAfterDownload = true

	app.shutdown(context.Background())

	app.mu.Lock()
	defer app.mu.Unlock()
	if app.ipodMutationCmd != command {
		t.Fatal("shutdown detached a protected commit process")
	}
	if app.ipodMutationOperationID != "restore-1" {
		t.Fatal("shutdown cleared protected operation metadata")
	}
	if app.indexRestartAfterDownload {
		t.Fatal("shutdown left a deferred index restart armed")
	}
}
