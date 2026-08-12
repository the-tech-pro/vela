//go:build windows

package main

import (
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"

	"golang.org/x/sys/windows"
)

const appleUSBVendorID uint16 = 0x05ac

type ipodRecoveryUSBMode struct {
	mode      string
	modelHint string
}

var ipodRecoveryUSBProductIDs = map[uint16]ipodRecoveryUSBMode{
	0x1223: {mode: "dfu", modelHint: "iPod classic 6G/6.5G or iPod nano 3G"},
	0x1241: {mode: "wtf", modelHint: "iPod classic 6G (late 2007)"},
	0x1245: {mode: "wtf", modelHint: "iPod classic 6.5G (late 2008)"},
	0x1247: {mode: "wtf", modelHint: "iPod classic 7G (late 2009)"},
	0x1250: {mode: "wtf", modelHint: "iPod classic late revision"},
}

func inspectIPodRecoveryUSB() IPodRecoveryUSBInspection {
	result := IPodRecoveryUSBInspection{
		Supported: true,
		ReadOnly:  true,
		Platform:  "windows",
		Devices:   make([]IPodRecoveryUSBDevice, 0),
	}

	deviceSet, err := windows.SetupDiGetClassDevsEx(
		nil,
		"USB",
		0,
		windows.DIGCF_PRESENT|windows.DIGCF_ALLCLASSES,
		0,
		"",
	)
	if err != nil {
		result.Message = "Windows USB device inspection is currently unavailable."
		result.Error = err.Error()
		return result
	}
	defer deviceSet.Close()
	result.Available = true

	seen := make(map[string]struct{})
	for index := 0; ; index++ {
		device, enumErr := deviceSet.EnumDeviceInfo(index)
		if errors.Is(enumErr, windows.ERROR_NO_MORE_ITEMS) {
			break
		}
		if enumErr != nil {
			continue
		}
		instanceID, instanceErr := deviceSet.DeviceInstanceID(device)
		if instanceErr != nil {
			continue
		}
		vendorID, productID, ok := parseUSBVendorProductID(instanceID)
		if !ok || vendorID != appleUSBVendorID {
			continue
		}
		recoveryMode, ok := ipodRecoveryUSBProductIDs[productID]
		if !ok {
			continue
		}
		key := strings.ToUpper(instanceID)
		if _, duplicate := seen[key]; duplicate {
			continue
		}
		seen[key] = struct{}{}
		result.Devices = append(result.Devices, IPodRecoveryUSBDevice{
			VendorID:   fmt.Sprintf("%04X", vendorID),
			ProductID:  fmt.Sprintf("%04X", productID),
			Mode:       recoveryMode.mode,
			ModelHint:  recoveryMode.modelHint,
			Name:       windowsDeviceDisplayName(deviceSet, device),
			InstanceID: instanceID,
		})
	}
	sort.Slice(result.Devices, func(left, right int) bool {
		return result.Devices[left].InstanceID < result.Devices[right].InstanceID
	})
	if len(result.Devices) == 0 {
		result.Message = "No supported iPod DFU or WTF device is currently visible."
	}
	return result
}

func parseUSBVendorProductID(instanceID string) (uint16, uint16, bool) {
	upper := strings.ToUpper(instanceID)
	vendorID, vendorOK := parseUSBHexField(upper, "VID_")
	productID, productOK := parseUSBHexField(upper, "PID_")
	return vendorID, productID, vendorOK && productOK
}

func parseUSBHexField(value string, marker string) (uint16, bool) {
	start := strings.Index(value, marker)
	if start < 0 {
		return 0, false
	}
	start += len(marker)
	if len(value)-start < 4 {
		return 0, false
	}
	parsed, err := strconv.ParseUint(value[start:start+4], 16, 16)
	if err != nil {
		return 0, false
	}
	return uint16(parsed), true
}

func windowsDeviceDisplayName(deviceSet windows.DevInfo, device *windows.DevInfoData) string {
	for _, property := range []windows.SPDRP{windows.SPDRP_FRIENDLYNAME, windows.SPDRP_DEVICEDESC} {
		value, err := deviceSet.DeviceRegistryProperty(device, property)
		if err != nil {
			continue
		}
		if name, ok := value.(string); ok && strings.TrimSpace(name) != "" {
			return strings.TrimSpace(name)
		}
	}
	return ""
}
