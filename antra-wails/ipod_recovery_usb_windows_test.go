//go:build windows

package main

import "testing"

func TestParseIPodRecoveryUSBIdentifiers(t *testing.T) {
	vendorID, productID, ok := parseUSBVendorProductID(`USB\VID_05AC&PID_1223\RECOVERY`)
	if !ok || vendorID != 0x05ac || productID != 0x1223 {
		t.Fatalf("unexpected parsed identifiers: %04x:%04x %v", vendorID, productID, ok)
	}
	if _, _, ok := parseUSBVendorProductID(`USB\VID_ZZZZ&PID_1223\INVALID`); ok {
		t.Fatal("invalid hexadecimal identifiers must be rejected")
	}
}

func TestIPodRecoveryUSBProductClassification(t *testing.T) {
	tests := map[uint16]string{
		0x1223: "dfu",
		0x1241: "wtf",
		0x1245: "wtf",
		0x1247: "wtf",
		0x1250: "wtf",
	}
	for productID, wantMode := range tests {
		got, ok := ipodRecoveryUSBProductIDs[productID]
		if !ok || got.mode != wantMode || got.modelHint == "" {
			t.Fatalf("unexpected classification for %04X: %#v", productID, got)
		}
	}
	if _, ok := ipodRecoveryUSBProductIDs[0x1261]; ok {
		t.Fatal("normal mounted-mode iPods must not be classified as DFU/WTF")
	}
}
