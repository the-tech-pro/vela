package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

const thirdPartyNotices = `Third-Party Notices

iOpenPod
Vela uses headless device, database, artwork, checksum, backup, and sync
components from https://github.com/TheRealSavi/iOpenPod.

Copyright (c) John Gibbons and iOpenPod contributors.

MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Rockbox and mks5lboot
The experimental iPod Classic capacity-unlock workflow is designed around
Rockbox's iPod 6G firmware and the upstream Rockbox Utility/mks5lboot
bootloader process. Vela does not bundle or execute an opaque community
firmware binary, does not replace USB drivers, and does not automate a
bootloader or NOR installation.

The guided workflow pins the official Rockbox Utility 1.5.1 Windows archive
at SHA-256
3226b5ede00bd7d7a0458af4f5428b8080c7983650e14087b6b4050d6a23c46d
and its GPL corresponding-source archive at SHA-256
82e34ed756b4777d117b13c400040622057d5b5ef38138d9fcb373fe8527e073.
The source archive contains the iPod 6G bootloader/DFU implementation under
utils/mks5lboot. Both files come directly from download.rockbox.org and are
transferred only after explicit user action.

The reproducible helper recipe pins Rockbox source commit
2df1172e985c45e9bf7fe3283bbb42dfaa36c735, applies the pinned Olsro patch,
then applies Vela's byte-exact NOR-readback patch. Rockbox and the resulting modified helper are GPL-2.0-or-later. The full license is at
packaging/ipod-unlock/licenses/GPL-2.0-or-later.txt; exact source, patches,
build script, and lock metadata are under packaging/ipod-unlock/.

If a helper binary is distributed, its exact corresponding-source archive,
build manifest, and license must be offered from the same location. The binary
must not be distributed by itself. Rockbox project:
https://www.rockbox.org/

Olsro iPod SysCfg research
The narrow 2.0.2 compatibility transform and Rockbox debug-menu patch were
audited against Olsro's reddit-ipod-guides revision
1f3d33805259c1c2b58a5076bb3580e86bacdaf1.

Copyright (c) 2024 Olsro.

The repository is MIT licensed. Its complete notice is copied at
packaging/ipod-unlock/licenses/Olsro-MIT.txt. Vela ports only the audited
transformation and validates its byte-level diff; it does not bundle or run
the published SysCfg editor binaries.

Apple iPod firmware
Apple's iPod Classic 2.0.2 IPSW is proprietary and is not redistributed by
Vela. It can be downloaded from Apple's server only after an explicit user
action and is accepted only at the exact pinned size, SHA-1, and SHA-256.
Neither the IPSW nor iTunes may be included in Vela packages. Apple and iPod
are trademarks of Apple Inc.; the experimental workflow is not Apple factory
support.

pytsk3 and The Sleuth Kit
Windows desktop builds use pytsk3 20260715 solely for bounded, read-only
identification of attached HFS+ iPods that Windows cannot mount. Vela opens
only removable drive handles, inspects partition/filesystem metadata, and
requires an iPod_Control or iTunes_Control root before reporting a device.
This path does not extract files and does not expose a write operation.

pytsk3 is Apache-2.0. Bundled Sleuth Kit and talloc components retain their
upstream IBM Public License, Common Public License 1.0, and LGPL notices.
Projects: https://github.com/py4n6/pytsk and
https://github.com/sleuthkit/sleuthkit

FFmpeg and FFprobe
Desktop packages require owner-supplied, architecture-native FFmpeg and
FFprobe binaries. FFmpeg licensing depends on the exact build configuration:
it may be LGPL or GPL and may include components with additional obligations.
The release SBOM must record the exact version, build configuration, upstream
source, checksum, license text, and corresponding-source offer/location for
the binaries actually packaged. No binary is approved merely by being present
in VELA_TOOLS_DIR.

Upstream project: https://ffmpeg.org/

Chromaprint / fpcalc
Desktop packages require an owner-supplied, architecture-native fpcalc from
Chromaprint. The release SBOM must record its exact version, upstream source,
checksum, applicable license, license text, and corresponding-source
obligations for the packaged binary.

Upstream project: https://acoustid.org/chromaprint

Distribution status
These notices are an inventory aid, not legal approval. Distribution remains
blocked pending owner/legal review of GPL-family dependencies, Widevine/DRM
authorization and service terms, and the repository/product distribution
license. Vela must never package WVD device files, cookies, credentials,
tokens, or signing secrets.`

type IPodBrowseRequest struct {
	MountPath string `json:"mount_path"`
	Resource  string `json:"resource"`
	Page      int    `json:"page"`
	PageSize  int    `json:"page_size"`
}

type IPodPlanRequest struct {
	MountPath   string   `json:"mount_path"`
	SourceFiles []string `json:"source_files"`
	StagingID   string   `json:"staging_id,omitempty"`
}

type IPodExecuteRequest struct {
	PlanID    string `json:"plan_id"`
	Confirmed bool   `json:"confirmed"`
}

type IPodPlanDetailsRequest struct {
	PlanID   string `json:"plan_id"`
	Group    string `json:"group"`
	Page     int    `json:"page"`
	PageSize int    `json:"page_size"`
}

type IPodStageRequest struct {
	MountPath      string   `json:"mount_path"`
	CompletedFiles []string `json:"completed_files"`
}

type IPodRecoveryUSBDevice struct {
	VendorID   string `json:"vendor_id"`
	ProductID  string `json:"product_id"`
	Mode       string `json:"mode"`
	ModelHint  string `json:"model_hint,omitempty"`
	Name       string `json:"name,omitempty"`
	InstanceID string `json:"instance_id,omitempty"`
}

type IPodRecoveryUSBInspection struct {
	Supported bool                    `json:"supported"`
	Available bool                    `json:"available"`
	ReadOnly  bool                    `json:"read_only"`
	Platform  string                  `json:"platform"`
	Devices   []IPodRecoveryUSBDevice `json:"devices"`
	Message   string                  `json:"message,omitempty"`
	Error     string                  `json:"error,omitempty"`
}

type IPodBackupSnapshotsRequest struct {
	ArchiveID string `json:"archive_id"`
	Page      int    `json:"page"`
	PageSize  int    `json:"page_size"`
}

type IPodBackupSnapshotRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
}

type IPodBackupVerifyRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
}

type IPodManualBackupRequest struct {
	MountPath string `json:"mount_path"`
}

type IPodBackupNoteRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
	Note       string `json:"note"`
}

type IPodBackupExportRequest struct {
	ArchiveID      string `json:"archive_id"`
	SnapshotID     string `json:"snapshot_id"`
	DestinationDir string `json:"destination_dir"`
}

type IPodBackupDeleteRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
	Confirmed  bool   `json:"confirmed"`
}

type IPodRestorePreflightRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
	MountPath  string `json:"mount_path"`
}

type IPodRestoreRequest struct {
	RestorePlanID string `json:"restore_plan_id"`
	Confirmed     bool   `json:"confirmed"`
}

type IPodMigrationPreflightRequest struct {
	ArchiveID  string `json:"archive_id"`
	SnapshotID string `json:"snapshot_id"`
	MountPath  string `json:"mount_path"`
}

type IPodMigrationRequest struct {
	MigrationPlanID string `json:"migration_plan_id"`
	Confirmed       bool   `json:"confirmed"`
}

type IPodCapacityUnlockEligibilityRequest struct {
	MountPath string `json:"mount_path"`
}

type IPodCapacityUnlockStartRequest struct {
	MountPath        string          `json:"mount_path"`
	Confirmed        bool            `json:"confirmed"`
	Acknowledgements map[string]bool `json:"acknowledgements,omitempty"`
}

type IPodCapacityUnlockAdvanceRequest struct {
	SessionID string                 `json:"session_id"`
	Action    string                 `json:"action"`
	Confirmed bool                   `json:"confirmed"`
	Data      map[string]interface{} `json:"data,omitempty"`
}

type ipodOperationRoute struct {
	operation    string
	terminalType string
	kind         string
}

type ipodCommandFactory func(
	context.Context,
	string,
	interface{},
) (*exec.Cmd, *bufio.Scanner, func(), error)

func newIPodOperationRoute(operation string, kind string) ipodOperationRoute {
	return ipodOperationRoute{
		operation:    operation,
		terminalType: "ipod_" + strings.ReplaceAll(operation, "-", "_"),
		kind:         kind,
	}
}

var (
	ipodBackupDevicesRoute             = newIPodOperationRoute("backup-devices", "backup")
	ipodBackupSnapshotsRoute           = newIPodOperationRoute("backup-snapshots", "backup")
	ipodBackupDetailsRoute             = newIPodOperationRoute("backup-details", "backup")
	ipodBackupVerifyRoute              = newIPodOperationRoute("backup-verify", "backup_verify")
	ipodBackupManualRoute              = newIPodOperationRoute("backup-manual", "manual_backup")
	ipodBackupNoteRoute                = newIPodOperationRoute("backup-note", "backup_note")
	ipodBackupExportRoute              = newIPodOperationRoute("backup-export", "backup_export")
	ipodBackupDeleteRoute              = newIPodOperationRoute("backup-delete", "backup_delete")
	ipodRestorePreflightRoute          = newIPodOperationRoute("restore-preflight", "restore")
	ipodRestoreRoute                   = newIPodOperationRoute("restore", "restore")
	ipodMigrationPreflightRoute        = newIPodOperationRoute("migration-preflight", "migration")
	ipodMigrationRoute                 = newIPodOperationRoute("migration", "migration")
	ipodRecoveryStateRoute             = newIPodOperationRoute("recovery-state", "recovery")
	ipodCapacityUnlockEligibilityRoute = newIPodOperationRoute("capacity-unlock-eligibility", "capacity_unlock")
	ipodCapacityUnlockStartRoute       = newIPodOperationRoute("capacity-unlock-start", "capacity_unlock")
	ipodCapacityUnlockAdvanceRoute     = newIPodOperationRoute("capacity-unlock-advance", "capacity_unlock")
)

func (a *App) GetThirdPartyNotices() string {
	return thirdPartyNotices
}

func (a *App) StartIPodWatcher() error {
	baseContext := a.ctx
	if baseContext == nil {
		baseContext = context.Background()
	}

	a.mu.Lock()
	if a.ipodWatcherCmd != nil || a.ipodWatcherStarting {
		a.mu.Unlock()
		return nil
	}
	a.ipodWatcherGeneration++
	generation := a.ipodWatcherGeneration
	ctx, cancel := context.WithCancel(baseContext)
	a.ipodWatcherStarting = true
	a.ipodWatcherCancel = cancel
	a.mu.Unlock()

	cancelPath := filepath.Join(getAppDataDir(), "ipod-watcher.cancel")
	_ = os.Remove(cancelPath)
	request := map[string]interface{}{
		"protocol_version": 1,
		"operation":        "watch",
		"app_data_dir":     getAppDataDir(),
		"cancel_path":      cancelPath,
		"interval_seconds": 2,
	}
	cmd, output, cleanup, err := a.prepareIPodCommand(ctx, "watch", request)
	if err != nil {
		a.finishIPodWatcherStart(generation, cancel)
		return err
	}
	if err := cmd.Start(); err != nil {
		cleanup()
		a.finishIPodWatcherStart(generation, cancel)
		return err
	}
	a.mu.Lock()
	if a.ipodWatcherGeneration != generation || !a.ipodWatcherStarting {
		a.mu.Unlock()
		_ = killCommandTree(cmd)
		cleanup()
		cancel()
		return nil
	}
	a.ipodWatcherCmd = cmd
	a.ipodWatcherStarting = false
	a.mu.Unlock()

	go func() {
		defer cleanup()
		scanErr := scanIPodEvents(output, func(payload map[string]interface{}) {
			wailsRuntime.EventsEmit(a.ctx, "ipod-event", payload)
		})
		waitErr := cmd.Wait()
		a.mu.Lock()
		wasCurrent := a.ipodWatcherGeneration == generation && a.ipodWatcherCmd == cmd
		if wasCurrent {
			a.ipodWatcherCmd = nil
			a.ipodWatcherCancel = nil
		}
		a.mu.Unlock()
		wasCancelled := ctx.Err() != nil
		cancel()
		if wasCurrent && !wasCancelled && (scanErr != nil || waitErr != nil) {
			message := errors.Join(scanErr, waitErr).Error()
			wailsRuntime.EventsEmit(a.ctx, "ipod-event", map[string]interface{}{
				"type": "ipod_watch_error", "message": message, "protocol_version": 1,
			})
		}
	}()
	return nil
}

func (a *App) finishIPodWatcherStart(generation uint64, cancel context.CancelFunc) {
	a.mu.Lock()
	if a.ipodWatcherGeneration == generation && a.ipodWatcherStarting {
		a.ipodWatcherStarting = false
		a.ipodWatcherCancel = nil
	}
	a.mu.Unlock()
	cancel()
}

func (a *App) StopIPodWatcher() {
	a.mu.Lock()
	a.ipodWatcherGeneration++
	cmd := a.ipodWatcherCmd
	cancel := a.ipodWatcherCancel
	a.ipodWatcherCmd = nil
	a.ipodWatcherStarting = false
	a.ipodWatcherCancel = nil
	a.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	if cmd != nil {
		_ = killCommandTree(cmd)
	}
}

func (a *App) ScanIPodDevices() string {
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	result, err := a.callReadOnlyHelper(ctx, "ipod_scan", map[string]interface{}{})
	if err != nil {
		return jsonError(err)
	}
	return string(result)
}

func (a *App) BrowseIPodLibrary(request IPodBrowseRequest) string {
	return a.runIPodRequest("browse", request, "ipod_browse")
}

func (a *App) CreateIPodSyncPlan(request IPodPlanRequest) string {
	return a.runIPodRequest("plan", request, "ipod_plan")
}

func (a *App) GetIPodSyncPlanDetails(request IPodPlanDetailsRequest) string {
	return a.runIPodRequest("plan-details", request, "ipod_plan_details")
}

func (a *App) CreateIPodBackup(planID string) error {
	return a.startIPodMutation("backup", "backup", map[string]interface{}{"plan_id": planID})
}

func (a *App) ExecuteIPodSync(request IPodExecuteRequest) error {
	if !request.Confirmed {
		return errors.New("the reviewed iPod plan must be confirmed")
	}
	return a.startIPodMutation("execute", "sync", request)
}

func (a *App) StageDownloadsForIPod(request IPodStageRequest) string {
	payload, err := requestMap(request)
	if err != nil {
		return jsonError(err)
	}
	payload["library_root"] = a.GetConfig().DownloadPath
	return a.runIPodRequest("stage", payload, "ipod_stage")
}

func (a *App) ListIPodBackupDevices() string {
	route := ipodBackupDevicesRoute
	return a.runIPodRequest(route.operation, nil, route.terminalType)
}

func (a *App) ListIPodBackupSnapshots(request IPodBackupSnapshotsRequest) string {
	route := ipodBackupSnapshotsRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) GetIPodBackupSnapshot(request IPodBackupSnapshotRequest) string {
	route := ipodBackupDetailsRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) VerifyIPodBackup(request IPodBackupVerifyRequest) string {
	route := ipodBackupVerifyRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) CreateManualIPodBackup(request IPodManualBackupRequest) error {
	route := ipodBackupManualRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) UpdateIPodBackupNote(request IPodBackupNoteRequest) error {
	route := ipodBackupNoteRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) ExportIPodBackup(request IPodBackupExportRequest) error {
	route := ipodBackupExportRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) DeleteIPodBackup(request IPodBackupDeleteRequest) error {
	if !request.Confirmed {
		return errors.New("backup deletion must be confirmed")
	}
	route := ipodBackupDeleteRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) PreflightIPodRestore(request IPodRestorePreflightRequest) string {
	route := ipodRestorePreflightRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) StartIPodRestore(request IPodRestoreRequest) error {
	if !request.Confirmed {
		return errors.New("the reviewed iPod restore must be confirmed")
	}
	route := ipodRestoreRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) PreflightIPodMigration(request IPodMigrationPreflightRequest) string {
	route := ipodMigrationPreflightRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) StartIPodMigration(request IPodMigrationRequest) error {
	if !request.Confirmed {
		return errors.New("the reviewed iPod migration must be confirmed")
	}
	route := ipodMigrationRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) GetIPodRecoveryState() string {
	route := ipodRecoveryStateRoute
	return a.runIPodRequest(route.operation, nil, route.terminalType)
}

func (a *App) GetIPodCapacityUnlockEligibility(request IPodCapacityUnlockEligibilityRequest) string {
	route := ipodCapacityUnlockEligibilityRoute
	return a.runIPodRequest(route.operation, request, route.terminalType)
}

func (a *App) StartIPodCapacityUnlock(request IPodCapacityUnlockStartRequest) error {
	if !request.Confirmed {
		return errors.New("the destructive iPod capacity unlock must be confirmed")
	}
	route := ipodCapacityUnlockStartRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func (a *App) AdvanceIPodCapacityUnlock(request IPodCapacityUnlockAdvanceRequest) error {
	request.Action = strings.ToLower(strings.TrimSpace(request.Action))
	if capacityUnlockActionRequiresConfirmation(request.Action) && !request.Confirmed {
		return errors.New("the capacity unlock step must be confirmed")
	}
	route := ipodCapacityUnlockAdvanceRoute
	return a.startIPodMutation(route.operation, route.kind, request)
}

func capacityUnlockActionRequiresConfirmation(action string) bool {
	switch strings.ToLower(strings.TrimSpace(action)) {
	case "status", "list":
		return false
	default:
		return true
	}
}

func (a *App) InspectIPodRecoveryUSB() IPodRecoveryUSBInspection {
	return inspectIPodRecoveryUSB()
}

// PickIPodRecoveryFile opens a native read-only file picker for SysCfg dumps,
// NOR readbacks, and user-supplied pinned artifacts. Selecting a path does not
// read or modify the file; Python applies the operation-specific safety gates.
func (a *App) PickIPodRecoveryFile() string {
	path, err := wailsRuntime.OpenFileDialog(a.ctx, wailsRuntime.OpenDialogOptions{
		Title: "Select iPod recovery file",
	})
	if err != nil {
		return ""
	}
	return path
}

func (a *App) CancelIPodOperation() error {
	return a.cancelIPodOperation("")
}

func (a *App) CancelIPodOperationByID(operationID string) error {
	operationID = strings.TrimSpace(operationID)
	if operationID == "" {
		return errors.New("an iPod operation ID is required")
	}
	return a.cancelIPodOperation(operationID)
}

func (a *App) cancelIPodOperation(operationID string) error {
	a.mu.Lock()
	cancelPath := a.ipodCancelPath
	runID := a.ipodMutationRunID
	activeOperationID := a.ipodMutationOperationID
	phase := a.ipodMutationPhase
	canCancel := a.ipodMutationCanCancel
	if cancelPath == "" {
		a.mu.Unlock()
		if operationID != "" && activeOperationID == "" {
			return errors.New("there is no active iPod operation")
		}
		return nil
	}
	if operationID != "" && operationID != activeOperationID && operationID != runID {
		a.mu.Unlock()
		return errors.New("the active iPod operation does not match the requested operation ID")
	}
	if !canCancel || isProtectedIPodMutationPhase(phase) {
		a.mu.Unlock()
		return fmt.Errorf("the iPod operation cannot be cancelled during %s", displayIPodMutationPhase(phase))
	}
	if err := os.MkdirAll(filepath.Dir(cancelPath), 0755); err != nil {
		a.mu.Unlock()
		return err
	}
	if err := os.WriteFile(cancelPath, []byte("cancel\n"), 0600); err != nil {
		a.mu.Unlock()
		return err
	}
	if a.ipodMutationOperationID == activeOperationID {
		a.ipodMutationPhase = "cancelling"
		a.ipodMutationCanCancel = false
	}
	a.mu.Unlock()
	return nil
}

func (a *App) EjectIPod(mountPath string) string {
	a.mu.Lock()
	busy := a.ipodMutationCmd != nil || a.ipodMutationStarting
	a.mu.Unlock()
	if busy {
		return `{"error":"Wait for backup or sync to finish before ejecting."}`
	}
	return a.runIPodRequest("eject", map[string]interface{}{"mount_path": mountPath}, "ipod_eject")
}

func (a *App) startIPodMutation(operation string, kind string, request interface{}) error {
	runID := newIPodOperationID(operation)
	cancelPath := filepath.Join(getAppDataDir(), fmt.Sprintf("ipod-%s.cancel", runID))
	_ = os.Remove(cancelPath)
	a.mu.Lock()
	if a.ipodMutationCmd != nil || a.ipodMutationStarting {
		a.mu.Unlock()
		return errors.New("another iPod mutation is already running")
	}
	if a.activeCmd != nil {
		a.mu.Unlock()
		return errors.New("wait for the active download to finish before syncing an iPod")
	}
	a.ipodMutationStarting = true
	a.ipodCancelPath = cancelPath
	a.ipodMutationRunID = runID
	a.ipodMutationOperationID = runID
	a.ipodMutationOperation = operation
	a.ipodMutationKind = kind
	a.ipodMutationPhase = "starting"
	a.ipodMutationCanCancel = true
	a.mu.Unlock()
	releaseLibraryResources := a.beginExplicitLibraryWork()
	clearStarting := func() {
		a.mu.Lock()
		if a.ipodMutationRunID == runID {
			a.clearIPodMutationLocked()
		}
		a.mu.Unlock()
		_ = os.Remove(cancelPath)
	}

	payload, err := requestMap(request)
	if err != nil {
		clearStarting()
		releaseLibraryResources()
		return err
	}
	payload["cancel_path"] = cancelPath
	payload["operation_id"] = runID
	payload["kind"] = kind
	cmd, output, cleanup, err := a.prepareIPodCommand(context.Background(), operation, payload)
	if err != nil {
		clearStarting()
		releaseLibraryResources()
		return err
	}
	if err := cmd.Start(); err != nil {
		cleanup()
		clearStarting()
		releaseLibraryResources()
		return err
	}
	a.mu.Lock()
	if a.ipodMutationRunID != runID {
		a.mu.Unlock()
		_ = killCommandTree(cmd)
		cleanup()
		_ = os.Remove(cancelPath)
		releaseLibraryResources()
		return errors.New("the iPod operation was stopped while starting")
	}
	a.ipodMutationCmd = cmd
	a.ipodMutationStarting = false
	a.ipodMutationPhase = "running"
	a.mu.Unlock()

	go func() {
		defer cleanup()
		defer releaseLibraryResources()
		publicOperationID := runID
		terminalKind := kind
		backendErrorCode := ""
		backendErrorMessage := ""
		scanErr := scanIPodEvents(output, func(event map[string]interface{}) {
			if eventType, _ := event["type"].(string); eventType == "ipod_error" {
				backendErrorCode, _ = event["code"].(string)
				backendErrorMessage, _ = event["message"].(string)
			}
			eventBackendID, eventBackendKind := backendIPodOperationMetadata(event)
			a.mu.Lock()
			phase := a.ipodMutationPhase
			canCancel := a.ipodMutationCanCancel
			effectiveKind := terminalKind
			if a.ipodMutationRunID == runID {
				if eventBackendID != "" {
					a.ipodMutationOperationID = eventBackendID
				}
				if eventBackendKind != "" {
					a.ipodMutationKind = eventBackendKind
				}
				a.ipodMutationPhase, a.ipodMutationCanCancel = updateIPodMutationBoundary(
					a.ipodMutationPhase,
					a.ipodMutationCanCancel,
					event,
				)
				phase = a.ipodMutationPhase
				canCancel = a.ipodMutationCanCancel
				effectiveKind = a.ipodMutationKind
				publicOperationID = a.ipodMutationOperationID
			}
			a.mu.Unlock()
			if eventBackendKind != "" {
				terminalKind = eventBackendKind
			}
			enveloped := withIPodOperationMetadata(event, operation, publicOperationID, effectiveKind)
			if publicOperationID != runID {
				enveloped["bridge_operation_id"] = runID
			}
			enveloped["phase"] = phase
			enveloped["can_cancel"] = canCancel
			wailsRuntime.EventsEmit(a.ctx, "ipod-event", enveloped)
		})
		waitErr := cmd.Wait()
		a.mu.Lock()
		wasCurrent := a.ipodMutationCmd == cmd && a.ipodMutationRunID == runID
		if wasCurrent {
			if a.ipodMutationOperationID != "" {
				publicOperationID = a.ipodMutationOperationID
			}
			if a.ipodMutationKind != "" {
				terminalKind = a.ipodMutationKind
			}
			a.clearIPodMutationLocked()
		}
		a.mu.Unlock()
		_ = os.Remove(cancelPath)
		terminalEvent := map[string]interface{}{
			"type":             "ipod_operation_ended",
			"operation":        operation,
			"operation_id":     publicOperationID,
			"kind":             terminalKind,
			"protocol_version": 1,
		}
		if publicOperationID != runID {
			terminalEvent["bridge_operation_id"] = runID
		}
		if waitErr != nil || scanErr != nil || backendErrorMessage != "" {
			message := "the iPod backend event stream failed"
			if backendErrorMessage != "" {
				message = backendErrorMessage
			} else if waitErr != nil {
				message = waitErr.Error()
			} else if scanErr != nil {
				message = scanErr.Error()
			}
			terminalEvent["status"] = "failed"
			terminalEvent["phase"] = "failed"
			terminalEvent["can_cancel"] = false
			terminalEvent["message"] = message
			if backendErrorCode != "" {
				terminalEvent["code"] = backendErrorCode
			}
		} else {
			terminalEvent["status"] = "completed"
			terminalEvent["phase"] = "complete"
			terminalEvent["can_cancel"] = false
		}
		wailsRuntime.EventsEmit(a.ctx, "ipod-event", terminalEvent)
	}()
	return nil
}

func (a *App) clearIPodMutationLocked() {
	a.ipodMutationCmd = nil
	a.ipodMutationStarting = false
	a.ipodCancelPath = ""
	a.ipodMutationRunID = ""
	a.ipodMutationOperationID = ""
	a.ipodMutationOperation = ""
	a.ipodMutationKind = ""
	a.ipodMutationPhase = ""
	a.ipodMutationCanCancel = false
}

func newIPodOperationID(operation string) string {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err == nil {
		return operation + "-" + hex.EncodeToString(random)
	}
	return fmt.Sprintf("%s-%d", operation, time.Now().UnixNano())
}

func withIPodOperationMetadata(
	event map[string]interface{},
	operation string,
	operationID string,
	kind string,
) map[string]interface{} {
	enveloped := make(map[string]interface{}, len(event)+3)
	for key, value := range event {
		enveloped[key] = value
	}
	backendOperationID, backendKind := backendIPodOperationMetadata(event)
	if backendOperationID != "" && backendOperationID != operationID {
		enveloped["backend_operation_id"] = backendOperationID
	}
	if backendKind != "" && backendKind != kind {
		enveloped["backend_operation_kind"] = backendKind
	}
	enveloped["operation"] = operation
	enveloped["operation_id"] = operationID
	enveloped["kind"] = kind
	return enveloped
}

func backendIPodOperationMetadata(event map[string]interface{}) (string, string) {
	return firstIPodEventString(event, "operation_id"),
		firstIPodEventString(event, "operation_kind", "kind")
}

func updateIPodMutationBoundary(
	currentPhase string,
	currentCanCancel bool,
	event map[string]interface{},
) (string, bool) {
	phase := firstIPodEventString(event, "phase", "stage", "event")
	if phase == "" {
		phase = currentPhase
	}
	if currentPhase == "cancelling" && !isProtectedIPodMutationPhase(phase) {
		return currentPhase, false
	}
	if canCancel, ok := firstIPodEventBool(event, "can_cancel", "canCancel"); ok {
		currentCanCancel = canCancel
	}
	if isProtectedIPodMutationPhase(phase) {
		currentCanCancel = false
	}
	return phase, currentCanCancel
}

func firstIPodEventString(event map[string]interface{}, keys ...string) string {
	for _, source := range ipodEventSources(event) {
		for _, key := range keys {
			if value, ok := source[key].(string); ok && strings.TrimSpace(value) != "" {
				return strings.TrimSpace(value)
			}
		}
	}
	return ""
}

func firstIPodEventBool(event map[string]interface{}, keys ...string) (bool, bool) {
	for _, source := range ipodEventSources(event) {
		for _, key := range keys {
			if value, ok := source[key].(bool); ok {
				return value, true
			}
		}
	}
	return false, false
}

func ipodEventSources(event map[string]interface{}) []map[string]interface{} {
	sources := []map[string]interface{}{event}
	if data, ok := event["data"].(map[string]interface{}); ok {
		sources = append(sources, data)
	}
	if details, ok := event["details"].(map[string]interface{}); ok {
		sources = append(sources, details)
	}
	return sources
}

func isProtectedIPodMutationPhase(phase string) bool {
	words := strings.FieldsFunc(strings.ToLower(phase), func(char rune) bool {
		return (char < 'a' || char > 'z') && (char < '0' || char > '9')
	})
	for _, word := range words {
		switch word {
		case "commit", "committing", "committed",
			"finalize", "finalizing", "finalized",
			"finalise", "finalising", "finalised",
			"flush", "flushing", "flushed":
			return true
		}
	}
	return false
}

func displayIPodMutationPhase(phase string) string {
	phase = strings.TrimSpace(phase)
	if phase == "" {
		return "the current phase"
	}
	return phase
}

func (a *App) runIPodRequest(operation string, request interface{}, terminalType string) string {
	payload, err := requestMap(request)
	if err != nil {
		return jsonError(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), ipodRequestTimeout(operation))
	defer cancel()
	cmd, output, cleanup, err := a.prepareIPodCommand(ctx, operation, payload)
	if err != nil {
		return jsonError(err)
	}
	defer cleanup()
	if err := cmd.Start(); err != nil {
		return jsonError(err)
	}
	var terminal map[string]interface{}
	scanErr := scanIPodEvents(output, func(event map[string]interface{}) {
		eventType, _ := event["type"].(string)
		if eventType == terminalType || eventType == "ipod_error" {
			terminal = event
		} else if eventType == "ipod_progress" {
			wailsRuntime.EventsEmit(a.ctx, "ipod-event", event)
		}
	})
	waitErr := cmd.Wait()
	if scanErr != nil {
		return jsonError(scanErr)
	}
	if terminal == nil {
		if waitErr != nil {
			return jsonError(waitErr)
		}
		return `{"error":"The iPod backend returned no result."}`
	}
	if terminal["type"] == "ipod_error" {
		message, _ := terminal["message"].(string)
		return `{"error":` + quoteJSON(message) + `}`
	}
	data, _ := json.Marshal(terminal["data"])
	return string(data)
}

func ipodRequestTimeout(operation string) time.Duration {
	switch operation {
	case ipodBackupVerifyRoute.operation,
		ipodRestorePreflightRoute.operation,
		ipodMigrationPreflightRoute.operation:
		return 2 * time.Hour
	default:
		return 5 * time.Minute
	}
}

func (a *App) prepareIPodCommand(
	ctx context.Context,
	operation string,
	request interface{},
) (*exec.Cmd, *bufio.Scanner, func(), error) {
	a.mu.Lock()
	factory := a.ipodCommandFactory
	a.mu.Unlock()
	if factory != nil {
		return factory(ctx, operation, request)
	}

	payload, err := requestMap(request)
	if err != nil {
		return nil, nil, func() {}, err
	}
	payload["protocol_version"] = 1
	payload["operation"] = operation
	payload["app_data_dir"] = getAppDataDir()
	if err := os.MkdirAll(getAppDataDir(), 0755); err != nil {
		return nil, nil, func() {}, err
	}
	requestFile, err := os.CreateTemp(getAppDataDir(), "ipod-request-*.json")
	if err != nil {
		return nil, nil, func() {}, err
	}
	requestPath := requestFile.Name()
	cleanup := func() { _ = os.Remove(requestPath) }
	encoder := json.NewEncoder(requestFile)
	if err := encoder.Encode(payload); err != nil {
		requestFile.Close()
		cleanup()
		return nil, nil, func() {}, err
	}
	if err := requestFile.Close(); err != nil {
		cleanup()
		return nil, nil, func() {}, err
	}

	command, args, workDir, env, err := a.resolveBackendCommand([]string{})
	if err != nil {
		cleanup()
		return nil, nil, func() {}, err
	}
	args = append(args, "--ipod-operation", operation, "--ipod-request", requestPath)
	cmd := exec.CommandContext(ctx, command, args...)
	hideProcess(cmd)
	cmd.Dir = workDir
	cmd.Env = env
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		cleanup()
		return nil, nil, func() {}, err
	}
	cmd.Stderr = cmd.Stdout
	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 2*1024*1024)
	return cmd, scanner, cleanup, nil
}

func scanIPodEvents(scanner *bufio.Scanner, emit func(map[string]interface{})) error {
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		var payload map[string]interface{}
		if json.Unmarshal([]byte(line), &payload) == nil {
			emit(payload)
		}
	}
	return scanner.Err()
}

func requestMap(request interface{}) (map[string]interface{}, error) {
	if request == nil {
		return map[string]interface{}{}, nil
	}
	if value, ok := request.(map[string]interface{}); ok {
		result := make(map[string]interface{}, len(value))
		for key, item := range value {
			result[key] = item
		}
		return result, nil
	}
	data, err := json.Marshal(request)
	if err != nil {
		return nil, err
	}
	var result map[string]interface{}
	if err := json.Unmarshal(data, &result); err != nil {
		return nil, err
	}
	return result, nil
}

func quoteJSON(value string) string {
	data, _ := json.Marshal(value)
	return string(data)
}

func jsonError(err error) string {
	return `{"error":` + quoteJSON(err.Error()) + `}`
}
