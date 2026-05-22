// SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
//
// SPDX-License-Identifier: MPL-2.0

package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestHFFilterFiles(t *testing.T) {
	files := []string{
		"model-Q4_0.gguf",
		"model-Q8_0.gguf",
		"mmproj-BF16.gguf",
		"README.md",
		"config.json",
	}

	main, mmproj := hfFilterFiles(files, "*Q4_0*.gguf", "*mmproj*BF16*.gguf")
	if len(main) != 1 || main[0] != "model-Q4_0.gguf" {
		t.Errorf("main: got %v", main)
	}
	if len(mmproj) != 1 || mmproj[0] != "mmproj-BF16.gguf" {
		t.Errorf("mmproj: got %v", mmproj)
	}

	// mmproj must not appear in main even if it matches the pattern
	main2, _ := hfFilterFiles(files, "*.gguf", "")
	for _, f := range main2 {
		if strings.HasPrefix(f, "mmproj") {
			t.Errorf("mmproj file %q leaked into main list", f)
		}
	}
}

func TestHFFilterFiles_NoMmproj(t *testing.T) {
	files := []string{"model-Q4_0.gguf", "README.md"}
	main, mmproj := hfFilterFiles(files, "*Q4_0*.gguf", "")
	if len(main) != 1 {
		t.Errorf("expected 1 main file, got %d", len(main))
	}
	if len(mmproj) != 0 {
		t.Errorf("expected 0 mmproj files, got %d", len(mmproj))
	}
}

func TestToGlobPattern(t *testing.T) {
	cases := []struct{ in, want string }{
		{"Q4_0", "*Q4_0*"},
		{"model.gguf", "model.gguf"},
		{"*Q4*", "*Q4*"},
		{"model?file", "model?file"}, // ? is already a glob character
	}
	for _, c := range cases {
		got := toGlobPattern(c.in)
		if got != c.want {
			t.Errorf("toGlobPattern(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

func TestGenerateModelsIni(t *testing.T) {
	dir := t.TempDir()

	// Create fake GGUF structure: dir/repo/model-Q4_0.gguf + mmproj-BF16.gguf
	repoDir := filepath.Join(dir, "myrepo")
	os.MkdirAll(repoDir, 0o755)
	os.WriteFile(filepath.Join(repoDir, "model-Q4_0.gguf"), []byte{}, 0o644)
	os.WriteFile(filepath.Join(repoDir, "mmproj-BF16.gguf"), []byte{}, 0o644)
	os.WriteFile(filepath.Join(repoDir, "model-Q8_0.gguf"), []byte{}, 0o644)

	if err := generateModelsIni(dir); err != nil {
		t.Fatalf("generateModelsIni: %v", err)
	}

	data, err := os.ReadFile(filepath.Join(dir, "models.ini"))
	if err != nil {
		t.Fatalf("read models.ini: %v", err)
	}
	ini := string(data)

	// Should have sections for the two non-mmproj models
	for _, section := range []string{"[model-Q4_0]", "[model-Q8_0]"} {
		if !strings.Contains(ini, section) {
			t.Errorf("missing section %s in models.ini:\n%s", section, ini)
		}
	}
	// mmproj should not be a section
	if strings.Contains(ini, "[mmproj") {
		t.Errorf("mmproj should not be a section:\n%s", ini)
	}
	// Q4_0 entry should reference the mmproj
	if !strings.Contains(ini, "mmproj =") {
		t.Errorf("expected mmproj reference in models.ini:\n%s", ini)
	}
	// Q8_0 entry is in a different dir — no mmproj in same dir (both are in same dir, so it gets one too)
}

func TestHFListFiles(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer mytoken" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		resp := map[string]interface{}{
			"siblings": []map[string]string{
				{"rfilename": "model-Q4_0.gguf"},
				{"rfilename": "model-Q8_0.gguf"},
				{"rfilename": "README.md"},
			},
		}
		json.NewEncoder(w).Encode(resp)
	}))
	defer srv.Close()

	files, err := hfListFilesURL(srv.Client(), "myorg/myrepo", "mytoken", srv.URL+"/api/models/%s")
	if err != nil {
		t.Fatalf("hfListFiles: %v", err)
	}
	if len(files) != 3 {
		t.Errorf("expected 3 files, got %d: %v", len(files), files)
	}
}
