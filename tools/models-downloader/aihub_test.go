// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAIHubResolveURL_ChipsetSpecific(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Only accept the chipset-specific URL
		if strings.Contains(r.URL.Path, "qualcomm_qcs8275") {
			w.WriteHeader(http.StatusOK)
		} else {
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	// Temporarily swap the S3 base for a test-local function
	url, err := aiHubResolveURLBase(srv.Client(), "melotts_en", "voice_ai", "mixed_with_float", "qualcomm-qcs8275", "0.51.0", srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(url, "qualcomm_qcs8275") {
		t.Errorf("expected chipset-specific URL, got: %s", url)
	}
	if !strings.HasSuffix(url, ".zip") {
		t.Errorf("expected .zip URL, got: %s", url)
	}
}

func TestAIHubResolveURL_FallbackGeneric(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Reject chipset-specific, accept generic
		if strings.Contains(r.URL.Path, "qualcomm_qcs8275") {
			w.WriteHeader(http.StatusNotFound)
		} else {
			w.WriteHeader(http.StatusOK)
		}
	}))
	defer srv.Close()

	url, err := aiHubResolveURLBase(srv.Client(), "melotts_en", "voice_ai", "mixed_with_float", "qualcomm-qcs8275", "0.51.0", srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strings.Contains(url, "qualcomm_qcs8275") {
		t.Errorf("expected generic URL, got chipset-specific: %s", url)
	}
}

func TestAIHubResolveURL_NotFound(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer srv.Close()

	_, err := aiHubResolveURLBase(srv.Client(), "nonexistent", "genie", "w4a16", "qualcomm-qcs8275", "0.51.0", srv.URL)
	if err == nil {
		t.Fatal("expected error for 404 on both candidates")
	}
}

func TestAIHubChipsetUnderscore(t *testing.T) {
	// Verify hyphens are converted to underscores in the URL
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "my_chip_set") {
			w.WriteHeader(http.StatusOK)
		} else {
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()

	url, err := aiHubResolveURLBase(srv.Client(), "model", "type", "quant", "my-chip-set", "1.0.0", srv.URL)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(url, "my_chip_set") {
		t.Errorf("expected underscored chipset in URL, got: %s", url)
	}
}
